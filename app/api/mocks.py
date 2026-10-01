from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StrictInt

from app.core.redis import get_redis
from app.mocks import config_store
from app.mocks.third_party_mock import MOCKS

router = APIRouter(prefix="/mocks", tags=["mocks"])


class MockConfig(BaseModel):
    success_rate: StrictInt | None = Field(None, ge=0, le=100)
    delay_ms: StrictInt | None = Field(None, ge=0, le=30_000)


def _require_channel(channel: str) -> None:
    if channel not in MOCKS:
        raise HTTPException(404, f"'{channel}'는 유효하지 않은 채널입니다. (허용값: {', '.join(MOCKS)})")


@router.post("/reset")
async def reset_mocks():
    await config_store.reset_all(get_redis())
    return {"status": "reset"}


@router.put("/{channel}/config")
async def update_config(channel: str, body: MockConfig):
    _require_channel(channel)
    client = get_redis()
    await config_store.save_config(client, channel, body.success_rate, body.delay_ms)
    success_rate, delay_ms = await config_store.load_config(client, channel)
    return {"channel": channel, "success_rate": success_rate, "delay_ms": delay_ms}


@router.get("/{channel}/records")
async def get_records(channel: str):
    _require_channel(channel)
    records = await config_store.get_records(get_redis(), channel)
    return {"channel": channel, "count": len(records), "records": records}
