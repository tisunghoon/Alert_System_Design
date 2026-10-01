import asyncio
import logging
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import ALL_STREAMS, CHANNEL_STREAMS, get_redis, get_stream_length
from app.models.notification import Notification

logger = logging.getLogger(__name__)

router = APIRouter(tags=["monitoring"])

MAX_STATS_RANGE = timedelta(days=30)
HEALTH_TIMEOUT_SECONDS = 3


@router.get("/monitoring/queues")
async def get_queues(client: aioredis.Redis = Depends(get_redis)):
    sizes = await asyncio.gather(*(get_stream_length(client, s) for s in CHANNEL_STREAMS.values()))
    queues = []
    for (channel, stream), size in zip(CHANNEL_STREAMS.items(), sizes, strict=True):
        if size > settings.QUEUE_ALERT_THRESHOLD:
            logger.warning(
                "큐 크기가 임계값을 초과했습니다: channel=%s size=%d threshold=%d",
                channel, size, settings.QUEUE_ALERT_THRESHOLD,
            )
        queues.append({"channel": channel, "queue": stream, "size": size})
    return {"queues": queues}


@router.get("/monitoring/stats")
async def get_stats(
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: AsyncSession = Depends(get_db),
):
    # naive 입력은 UTC로 간주해 aware 값과 비교할 때 TypeError가 나지 않게 한다
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    end = end if end.tzinfo else end.replace(tzinfo=UTC)
    if end < start or end - start > MAX_STATS_RANGE:
        raise HTTPException(400, "시간 범위는 start 이후 최대 30일이어야 합니다.")

    stmt = (
        select(
            Notification.channel,
            func.count(case((Notification.status == "DELIVERED", 1))),
            func.count(case((Notification.status == "FAILED", 1))),
            func.coalesce(func.sum(Notification.retry_count), 0),
        )
        .where(Notification.queued_at >= start, Notification.queued_at <= end)
        .group_by(Notification.channel)
    )
    rows = {row[0]: row for row in (await db.execute(stmt)).all()}
    stats = {}
    for channel in CHANNEL_STREAMS:
        _, delivered, failed, retries = rows.get(channel, (channel, 0, 0, 0))
        stats[channel] = {"delivered": delivered, "failed": failed, "retries": int(retries)}
    return {"start": start, "end": end, "channels": stats}


async def _check_db(db: AsyncSession) -> None:
    await db.execute(text("SELECT 1"))


async def _check_cache(client: aioredis.Redis) -> None:
    await client.ping()


async def _check_queue(client: aioredis.Redis) -> None:
    await asyncio.gather(*(get_stream_length(client, stream) for stream in ALL_STREAMS))


async def _status(name: str, check) -> str:
    try:
        await asyncio.wait_for(check, HEALTH_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("헬스체크 실패: component=%s", name, exc_info=True)
        return "unhealthy"
    return "healthy"


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db), client: aioredis.Redis = Depends(get_redis)):
    db_status, cache_status, queue_status = await asyncio.gather(
        _status("database", _check_db(db)),
        _status("cache", _check_cache(client)),
        _status("message_queue", _check_queue(client)),
    )
    components = {"database": db_status, "cache": cache_status, "message_queue": queue_status}
    unhealthy = [name for name, status in components.items() if status != "healthy"]
    if unhealthy:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "components": components, "unhealthy": unhealthy},
        )
    return {"status": "healthy", "components": components}
