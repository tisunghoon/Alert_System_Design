import json

import redis.asyncio as aioredis

from app.mocks.third_party_mock import MOCKS, ThirdPartyMock

DEFAULT_SUCCESS_RATE = 100
DEFAULT_DELAY_MS = 0
MAX_RECORDS = ThirdPartyMock.MAX_RECORDS


def _config_key(channel: str) -> str:
    return f"mock_cfg:{channel}"


def _records_key(channel: str) -> str:
    return f"mock_records:{channel}"


async def save_config(
    client: aioredis.Redis,
    channel: str,
    success_rate: int | None = None,
    delay_ms: int | None = None,
) -> None:
    fields = {}
    if success_rate is not None:
        fields["success_rate"] = success_rate
    if delay_ms is not None:
        fields["delay_ms"] = delay_ms
    if fields:
        await client.hset(_config_key(channel), mapping=fields)


async def load_config(client: aioredis.Redis, channel: str) -> tuple[int, int]:
    cfg = await client.hgetall(_config_key(channel))
    return (
        int(cfg.get("success_rate", DEFAULT_SUCCESS_RATE)),
        int(cfg.get("delay_ms", DEFAULT_DELAY_MS)),
    )


async def apply_config(client: aioredis.Redis, channel: str, mock: ThirdPartyMock) -> None:
    mock.success_rate, mock.delay_ms = await load_config(client, channel)


async def push_record(client: aioredis.Redis, channel: str, record: dict) -> None:
    key = _records_key(channel)
    async with client.pipeline(transaction=True) as pipe:
        pipe.rpush(key, json.dumps(record))
        pipe.ltrim(key, -MAX_RECORDS, -1)
        await pipe.execute()


async def get_records(client: aioredis.Redis, channel: str) -> list[dict]:
    return [json.loads(r) for r in await client.lrange(_records_key(channel), 0, -1)]


async def reset_all(client: aioredis.Redis) -> None:
    await client.delete(
        *(key for channel in MOCKS for key in (_config_key(channel), _records_key(channel)))
    )
    for mock in MOCKS.values():
        mock.reset()


async def send_with_shared_state(
    client: aioredis.Redis, channel: str, notification: dict
) -> bool:
    """Worker가 전송할 때 쓰는 진입점: Redis 설정을 적용해 보내고 수신 기록을 Redis에도 남긴다."""
    mock = MOCKS[channel]
    await apply_config(client, channel, mock)
    success = await mock.send(notification)
    await push_record(client, channel, mock.records[-1])
    return success
