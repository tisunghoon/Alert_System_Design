import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ResponseError

from app.core.config import settings

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "notification_consumers"

IOS_STREAM = "ios_stream"
ANDROID_STREAM = "android_stream"
SMS_STREAM = "sms_stream"
EMAIL_STREAM = "email_stream"
RETRY_STREAM = "retry_stream"
DEAD_LETTER_STREAM = "dead_letter_stream"

CHANNEL_STREAMS = {
    "ios": IOS_STREAM,
    "android": ANDROID_STREAM,
    "sms": SMS_STREAM,
    "email": EMAIL_STREAM,
}
ALL_STREAMS = [*CHANNEL_STREAMS.values(), RETRY_STREAM, DEAD_LETTER_STREAM]

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def init_consumer_group(
    client: aioredis.Redis, stream: str, group: str = CONSUMER_GROUP
) -> None:
    try:
        await client.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def xadd_notification(
    client: aioredis.Redis, stream: str, fields: dict[str, Any]
) -> str:
    return await client.xadd(stream, {k: str(v) for k, v in fields.items()})


async def xreadgroup_messages(
    client: aioredis.Redis,
    stream: str,
    consumer: str,
    count: int = 10,
    block_ms: int = 1000,
    group: str = CONSUMER_GROUP,
) -> list[tuple[str, dict[str, str]]]:
    result = await client.xreadgroup(
        group, consumer, {stream: ">"}, count=count, block=block_ms
    )
    if not result:
        return []
    return [(msg_id, fields) for _, messages in result for msg_id, fields in messages]


async def xack_message(
    client: aioredis.Redis, stream: str, message_id: str, group: str = CONSUMER_GROUP
) -> int:
    return await client.xack(stream, group, message_id)


async def xdel_message(client: aioredis.Redis, stream: str, message_id: str) -> int:
    return await client.xdel(stream, message_id)


async def get_stream_length(client: aioredis.Redis, stream: str) -> int:
    return await client.xlen(stream)


# 캐시는 DB를 빠르게 읽기 위한 보조 수단이라, Redis 오류는 경고만 남기고 miss나 건너뜀으로 처리한다.
async def get_cache(client: aioredis.Redis, key: str) -> Any | None:
    try:
        raw = await client.get(key)
    except RedisError:
        logger.warning("캐시 조회에 실패했습니다: key=%s", key)
        return None
    return None if raw is None else json.loads(raw)


async def set_cache(
    client: aioredis.Redis, key: str, value: Any, ttl_seconds: int
) -> None:
    try:
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except RedisError:
        logger.warning("캐시 저장에 실패했습니다: key=%s", key)


async def invalidate_cache(client: aioredis.Redis, key: str) -> None:
    # 실패하면 오래된 값이 남을 수 있지만 TTL이 지나면 사라진다.
    try:
        await client.delete(key)
    except RedisError:
        logger.warning("캐시 무효화에 실패했습니다: key=%s", key)


async def incr_with_ttl(client: aioredis.Redis, key: str, ttl_seconds: int) -> int:
    # NX는 키에 TTL이 없을 때만 설정하므로 첫 증가에서만 만료가 걸리고, 트랜잭션이라 TTL 없는 키가 남지 않는다.
    async with client.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)
        count, _ = await pipe.execute()
    return count
