import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import delete_cache, get_redis
from app.models import App, UserPreference
from app.schemas.preference import PreferenceUpdate
from app.services.auth import get_current_app
from app.services.preference_service import get_preferences, pref_cache_key
from app.services.user_service import get_or_create_user

router = APIRouter(prefix="/users/{user_id}/preferences", tags=["preferences"])


async def _upsert(db: AsyncSession, user_id: str, preferences: dict[str, bool]) -> None:
    user = await get_or_create_user(db, user_id)
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    existing = {p.channel: p for p in result.scalars().all()}
    for channel, enabled in preferences.items():
        if channel in existing:
            existing[channel].is_enabled = enabled
        else:
            db.add(UserPreference(user_id=user.id, channel=channel, is_enabled=enabled))
    await db.commit()


@router.get("")
async def read_preferences(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
) -> dict[str, bool]:
    return await get_preferences(db, redis, user_id)


@router.put("")
async def update_preferences(
    user_id: str,
    payload: PreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
) -> dict[str, bool]:
    try:
        await _upsert(db, user_id, payload.preferences)
    except IntegrityError:
        # 동시 요청이 같은 행을 먼저 삽입한 경우: 롤백 후 한 번 더 시도하면 기존 행을 갱신한다.
        await db.rollback()
        await _upsert(db, user_id, payload.preferences)
    await delete_cache(redis, pref_cache_key(user_id))
    return await get_preferences(db, redis, user_id)
