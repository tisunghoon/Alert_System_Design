import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.models.notification import Notification

logger = logging.getLogger(__name__)

DEDUP_TTL_SECONDS = 24 * 60 * 60


def _key(event_id: str) -> str:
    return f"dedup:{event_id}"


async def _delivered_in_db(event_id: str, db: AsyncSession) -> bool:
    stmt = (
        select(Notification.id)
        .where(Notification.event_id == event_id, Notification.status == "DELIVERED")
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def check_duplicate(
    event_id: str, db: AsyncSession, client: aioredis.Redis | None = None
) -> bool:
    client = client or get_redis()
    try:
        if await client.exists(_key(event_id)):
            return True
    except RedisError:
        logger.warning("Redis 응답 불가로 중복 검사를 DB로 폴백합니다: event_id=%s", event_id)
    return await _delivered_in_db(event_id, db)


async def mark_processed(event_id: str, client: aioredis.Redis | None = None) -> None:
    client = client or get_redis()
    await client.set(_key(event_id), "1", ex=DEDUP_TTL_SECONDS)
