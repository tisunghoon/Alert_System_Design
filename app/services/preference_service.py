import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_cache, set_cache
from app.models import UserPreference
from app.models.base import CHANNELS
from app.services.user_service import get_user

PREFERENCE_CACHE_TTL = 300


def pref_cache_key(user_id: str) -> str:
    return f"pref:{user_id}"


def is_channel_enabled(preferences: dict[str, bool], channel: str) -> bool:
    return preferences.get(channel, True)


async def get_preferences(
    db: AsyncSession, client: aioredis.Redis, user_id: str
) -> dict[str, bool]:
    key = pref_cache_key(user_id)
    cached = await get_cache(client, key)
    if cached is not None:
        return cached

    preferences = dict.fromkeys(CHANNELS, True)
    user = await get_user(db, user_id)
    if user is not None:
        result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
        preferences.update({p.channel: p.is_enabled for p in result.scalars().all()})
    await set_cache(client, key, preferences, PREFERENCE_CACHE_TTL)
    return preferences
