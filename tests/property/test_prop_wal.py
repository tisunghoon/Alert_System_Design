import asyncio
import uuid
from dataclasses import dataclass

import fakeredis
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.redis import (
    CHANNEL_STREAMS,
    CONSUMER_GROUP,
    init_consumer_group,
    xack_message,
    xadd_notification,
    xreadgroup_messages,
)
from app.models import Notification, NotificationStatusHistory
from app.services import notification_log

CHANNELS = list(CHANNEL_STREAMS)


@dataclass
class NotificationRequest:
    event_id: str
    channel: str
    recipient_id: str
    title: str | None
    body: str


requests = st.builds(
    NotificationRequest,
    event_id=st.uuids().map(str),
    channel=st.sampled_from(CHANNELS),
    recipient_id=st.text(min_size=1, max_size=128),
    title=st.none() | st.text(min_size=1, max_size=256),
    body=st.text(min_size=1, max_size=200),
)


class FakeSession:
    """notification_log가 쓰는 add/flush/get만 흉내 내는 인메모리 세션."""

    def __init__(self):
        self.notifications: dict[uuid.UUID, Notification] = {}
        self.history: list[NotificationStatusHistory] = []
        self.fail = False
        self._pending: list = []

    def add(self, obj):
        self._pending.append(obj)

    async def flush(self):
        pending, self._pending = self._pending, []
        if self.fail:
            raise RuntimeError("db down")
        for obj in pending:
            if isinstance(obj, Notification):
                self.notifications[obj.id] = obj
            else:
                self.history.append(obj)

    async def get(self, model, pk):
        return self.notifications.get(pk)

    def by_event_id(self, event_id):
        return next((n for n in self.notifications.values() if n.event_id == event_id), None)


async def enqueue(req: NotificationRequest, db: FakeSession, client) -> bool:
    try:
        await notification_log.create_queued_log(
            req.event_id, uuid.uuid4(), req.channel, req.recipient_id, req.title, req.body, None, db
        )
    except RuntimeError:
        return False
    await xadd_notification(client, CHANNEL_STREAMS[req.channel], {"event_id": req.event_id})
    return True


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(requests, st.booleans()), min_size=1, max_size=15, unique_by=lambda t: t[0].event_id))
def test_property_12_queued_log_exists_before_queue(items):
    """Feature: alert-system, Property 12: WAL 패턴 - 큐 삽입 전 로그 선기록"""

    async def run():
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        db = FakeSession()
        for req, log_fails in items:
            db.fail = log_fails
            assert await enqueue(req, db, client) is (not log_fails)

        for req, log_fails in items:
            entries = await client.xrange(CHANNEL_STREAMS[req.channel])
            queued_ids = [fields["event_id"] for _, fields in entries]
            if log_fails:
                assert req.event_id not in queued_ids
                assert db.by_event_id(req.event_id) is None

        for channel in CHANNELS:
            for _, fields in await client.xrange(CHANNEL_STREAMS[channel]):
                log = db.by_event_id(fields["event_id"])
                assert log is not None and log.status == "QUEUED"
                first = [h for h in db.history if h.notification_id == log.id][0]
                assert first.status == "QUEUED"

    asyncio.run(run())


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(requests, st.booleans(), st.booleans()), min_size=1, max_size=10,
                unique_by=lambda t: t[0].event_id))
def test_property_13_acked_messages_have_final_status(items):
    """Feature: alert-system, Property 13: 처리 완료 후 큐 메시지 삭제"""

    async def run():
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        db = FakeSession()
        for req, _, _ in items:
            await enqueue(req, db, client)

        outcomes = {req.event_id: (success, log_fails) for req, success, log_fails in items}
        acked: list[str] = []
        for stream in CHANNEL_STREAMS.values():
            await init_consumer_group(client, stream)
            for msg_id, fields in await xreadgroup_messages(client, stream, "w1", block_ms=None):
                success, log_fails = outcomes[fields["event_id"]]
                log = db.by_event_id(fields["event_id"])
                db.fail = log_fails
                try:
                    await notification_log.update_status(
                        log.id, "DELIVERED" if success else "FAILED", "w1", None, db
                    )
                    await notification_log.set_final_duration(log.id, db)
                except RuntimeError:
                    continue
                await xack_message(client, stream, msg_id)
                acked.append(fields["event_id"])

        for req, _, log_fails in items:
            assert (req.event_id in acked) is (not log_fails)
        for event_id in acked:
            log = db.by_event_id(event_id)
            assert log.status in ("DELIVERED", "FAILED")
            assert log.total_duration_ms is not None

        pending = 0
        for stream in CHANNEL_STREAMS.values():
            pending += (await client.xpending(stream, CONSUMER_GROUP))["pending"]
        assert pending == sum(1 for _, _, log_fails in items if log_fails)

    asyncio.run(run())
