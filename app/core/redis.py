import json
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.core.config import settings

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


async def init_all_consumer_groups(client: aioredis.Redis) -> None:
    for stream in ALL_STREAMS:
        await init_consumer_group(client, stream)


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


async def get_stream_length(client: aioredis.Redis, stream: str) -> int:
    return await client.xlen(stream)


async def get_cache(client: aioredis.Redis, key: str) -> Any | None:
    raw = await client.get(key)
    return None if raw is None else json.loads(raw)


async def set_cache(
    client: aioredis.Redis, key: str, value: Any, ttl_seconds: int
) -> None:
    await client.set(key, json.dumps(value), ex=ttl_seconds)


async def delete_cache(client: aioredis.Redis, key: str) -> int:
    return await client.delete(key)


async def incr_with_ttl(client: aioredis.Redis, key: str, ttl_seconds: int) -> int:
    # NX는 키에 TTL이 없을 때만 설정하므로 첫 증가에서만 만료가 걸리고, 트랜잭션이라 TTL 없는 키가 남지 않는다.
    async with client.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)
        count, _ = await pipe.execute()
    return count
