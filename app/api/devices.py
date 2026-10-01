import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_cache, get_redis, invalidate_cache, set_cache
from app.models import App, Device
from app.schemas.device import DeviceIn, DeviceOut
from app.services.auth import get_current_app
from app.services.user_service import get_or_create_user, get_user

router = APIRouter(prefix="/users/{user_id}/devices", tags=["devices"])

MAX_DEVICES_PER_USER = 10
DEVICE_CACHE_TTL = 300


def _cache_key(user_id: str) -> str:
    return f"device:{user_id}"


def _to_dict(device: Device) -> dict:
    return {"id": str(device.id), "channel": device.channel, "token": device.token}


@router.post("", status_code=201, response_model=DeviceOut)
async def register_device(
    user_id: str,
    payload: DeviceIn,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
):
    user = await get_or_create_user(db, user_id)
    count = (
        await db.execute(
            select(func.count()).select_from(Device).where(Device.user_id == user.id, Device.is_active)
        )
    ).scalar_one()
    if count >= MAX_DEVICES_PER_USER:
        raise HTTPException(400, f"단말은 사용자당 최대 {MAX_DEVICES_PER_USER}개까지 등록할 수 있습니다.")

    device = Device(id=uuid.uuid4(), user_id=user.id, channel=payload.channel, token=payload.token)
    db.add(device)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "이미 등록된 단말입니다.") from None
    await invalidate_cache(redis, _cache_key(user_id))
    return _to_dict(device)


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
):
    key = _cache_key(user_id)
    cached = await get_cache(redis, key)
    if cached is not None:
        return cached

    devices: list[dict] = []
    user = await get_user(db, user_id)
    if user is not None:
        result = await db.execute(
            select(Device).where(Device.user_id == user.id, Device.is_active).order_by(Device.created_at)
        )
        devices = [_to_dict(d) for d in result.scalars().all()]
    await set_cache(redis, key, devices, DEVICE_CACHE_TTL)
    return devices


@router.delete("/{device_id}", status_code=204)
async def delete_device(
    user_id: str,
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
):
    user = await get_user(db, user_id)
    device = await db.get(Device, device_id)
    if user is None or device is None or device.user_id != user.id:
        raise HTTPException(404, f"단말을 찾을 수 없습니다: {device_id}")
    await db.delete(device)
    await db.commit()
    await invalidate_cache(redis, _cache_key(user_id))
    return Response(status_code=204)
