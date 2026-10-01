import asyncio
import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal, engine
from app.core.redis import (
    CHANNEL_STREAMS,
    CONSUMER_GROUP,
    DEAD_LETTER_STREAM,
    RETRY_STREAM,
    close_redis,
    get_redis,
    init_consumer_group,
    xack_message,
    xadd_notification,
)
from app.mocks.config_store import send_with_shared_state
from app.services.notification_log import get_by_event_id, set_final_duration, update_status

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 32
SEND_TIMEOUT_SECONDS = 5
READ_COUNT = 10
BLOCK_MS = 1000


def backoff_delay(retry_number: int) -> int:
    return min(2 ** (retry_number - 1), MAX_BACKOFF_SECONDS)


class BaseWorker:
    channel: str

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        session_factory=SessionLocal,
        worker_id: str | None = None,
        send_timeout: float = SEND_TIMEOUT_SECONDS,
        clock=time.time,
    ):
        self.redis = redis_client or get_redis()
        self.session_factory = session_factory
        self.worker_id = worker_id or f"{self.channel}-{socket.gethostname()}-{os.getpid()}"
        self.send_timeout = send_timeout
        self.clock = clock
        self.stream = CHANNEL_STREAMS[self.channel]
        self.retry_group = f"{CONSUMER_GROUP}_{self.channel}_retry"
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def deliver(self, notification: dict) -> bool:
        return await send_with_shared_state(self.redis, self.channel, notification)

    async def setup(self) -> None:
        await init_consumer_group(self.redis, self.stream)
        await init_consumer_group(self.redis, RETRY_STREAM, self.retry_group)

    async def run(self) -> None:
        await self.setup()
        logger.info("%s 시작", self.worker_id)
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("폴링 중 오류, 1초 후 재시도")
                await asyncio.sleep(1)

    async def poll_once(self) -> None:
        # 이전에 ack하지 못한 메시지(미도래 재시도 포함)를 먼저 다시 본다.
        await self._consume(RETRY_STREAM, self.retry_group, "0", block=None)
        await self._consume(RETRY_STREAM, self.retry_group, ">", block=None)
        await self._consume(self.stream, CONSUMER_GROUP, "0", block=None)
        await self._consume(self.stream, CONSUMER_GROUP, ">", block=BLOCK_MS)

    async def _consume(self, stream: str, group: str, start_id: str, block: int | None) -> None:
        result = await self.redis.xreadgroup(
            group, self.worker_id, {stream: start_id}, count=READ_COUNT, block=block
        )
        for _, messages in result or []:
            for message_id, fields in messages:
                if self._stop.is_set():
                    return
                await self.handle_message(stream, group, message_id, fields)

    async def handle_message(self, stream: str, group: str, message_id: str, fields: dict) -> None:
        if stream == RETRY_STREAM:
            if fields.get("channel") != self.channel:
                await xack_message(self.redis, stream, message_id, group)
                return
            if float(fields.get("next_retry_after") or 0) > self.clock():
                return
        await self.process_message(stream, group, message_id, fields)

    async def process_message(self, stream: str, group: str, message_id: str, fields: dict) -> None:
        try:
            failure = await self._attempt(fields)
        except Exception as exc:
            logger.exception("처리 중 오류: %s", fields.get("event_id"))
            failure = f"{type(exc).__name__}: {exc}"
        if failure is not None:
            await self._retry_or_dead_letter(fields, failure)
        await xack_message(self.redis, stream, message_id, group)

    async def _attempt(self, fields: dict) -> str | None:
        """성공하거나 건너뛰면 None, 실패하면 실패 사유를 돌려준다."""
        retry_count = int(fields.get("retry_count") or 0)
        async with self.session_factory() as db:
            notification = await get_by_event_id(fields["event_id"], db)
            if notification is None:
                return "Notification_Log에 해당 event_id가 없습니다"
            if notification.status in ("DELIVERED", "FAILED"):
                return None
            note = f"재시도 {retry_count}회차 시작" if retry_count else None
            await update_status(notification.id, "PROCESSING", self.worker_id, note, db)
            await db.commit()
            notification_id = notification.id

            payload = {
                "channel": self.channel,
                "recipient_id": fields["recipient_id"],
                "body": fields["body"],
            }
            try:
                delivered = await asyncio.wait_for(self.deliver(payload), self.send_timeout)
            except TimeoutError:
                return f"{self.send_timeout:g}초 전송 타임아웃"
            if not delivered:
                return "Third_Party_Mock 전송 실패"

            await update_status(notification_id, "DELIVERED", self.worker_id, None, db)
            await set_final_duration(notification_id, db)
            await db.commit()
        return None

    async def _retry_or_dead_letter(self, fields: dict, reason: str) -> None:
        retry_count = int(fields.get("retry_count") or 0)
        exhausted = retry_count >= MAX_RETRIES
        next_retry = None if exhausted else self.clock() + backoff_delay(retry_count + 1)
        try:
            async with self.session_factory() as db:
                await self._record_failure(db, fields["event_id"], reason, retry_count, next_retry)
        except Exception:
            # DB가 내려가 있어도 메시지는 retry/DLQ로 보내 유실을 막는다.
            logger.exception("실패 이력을 기록하지 못했습니다: %s", fields.get("event_id"))

        if exhausted:
            await xadd_notification(
                self.redis,
                DEAD_LETTER_STREAM,
                {
                    "notification_id": fields["event_id"],
                    "failed_at": datetime.now(UTC).isoformat(),
                    "failure_reason": reason,
                    "retry_count": retry_count,
                },
            )
        else:
            await xadd_notification(
                self.redis,
                RETRY_STREAM,
                {**fields, "retry_count": retry_count + 1, "next_retry_after": int(next_retry)},
            )

    async def _record_failure(
        self, db: AsyncSession, event_id: str, reason: str, retry_count: int, next_retry: float | None
    ) -> None:
        notification = await get_by_event_id(event_id, db)
        if notification is None:
            return
        if next_retry is None:
            await update_status(notification.id, "FAILED", self.worker_id, f"최종 실패: {reason}", db)
            await set_final_duration(notification.id, db)
        else:
            when = datetime.fromtimestamp(next_retry, UTC).isoformat()
            note = f"전송 실패: {reason}; 재시도 {retry_count + 1}/{MAX_RETRIES}, 다음 재시도 {when}"
            updated = await update_status(notification.id, "QUEUED", self.worker_id, note, db)
            updated.retry_count = retry_count + 1
        await db.commit()


def run_worker(worker_cls: type[BaseWorker]) -> None:
    logging.basicConfig(level=logging.INFO)

    async def main() -> None:
        worker = worker_cls()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop)
        try:
            await worker.run()
        finally:
            await close_redis()
            await engine.dispose()

    asyncio.run(main())
