import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis, incr_with_ttl

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


def _counter_key(user_id: str, channel: str) -> str:
    return f"rate:{user_id}:{channel}"


def _config_key(user_id: str, channel: str) -> str:
    return f"rate_limit_cfg:{user_id}:{channel}"


def _parse_limit(configured: str | None, user_id: str, channel: str) -> int:
    if configured is None:
        return settings.RATE_LIMIT_DEFAULT
    try:
        return int(configured)
    except ValueError:
        logger.warning(
            "잘못된 Rate Limit 설정이라 기본값을 사용합니다: user=%s channel=%s value=%r",
            user_id, channel, configured,
        )
        return settings.RATE_LIMIT_DEFAULT


async def set_user_limit(
    user_id: str, channel: str, limit: int, client: aioredis.Redis | None = None
) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit은 1 이상의 정수여야 합니다")
    client = client or get_redis()
    await client.set(_config_key(user_id, channel), limit)


async def check_rate_limit(
    user_id: str,
    channel: str,
    db: AsyncSession | None = None,
    client: aioredis.Redis | None = None,
) -> bool:
    """요청을 허용하면 True, limit+1번째부터 False. db는 호출부 시그니처 호환용이며 사용하지 않는다."""
    client = client or get_redis()
    try:
        configured = await client.get(_config_key(user_id, channel))
        limit = _parse_limit(configured, user_id, channel)
        count = await incr_with_ttl(client, _counter_key(user_id, channel), WINDOW_SECONDS)
    except RedisError:
        logger.warning("Redis 응답 불가로 Rate Limit 검사를 건너뜁니다: user=%s channel=%s", user_id, channel)
        return True
    return count <= limit
