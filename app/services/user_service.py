import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def get_user(db: AsyncSession, external_id: str) -> User | None:
    result = await db.execute(select(User).where(User.external_id == external_id))
    return result.scalar_one_or_none()


async def get_or_create_user(db: AsyncSession, external_id: str) -> User:
    user = await get_user(db, external_id)
    if user is not None:
        return user
    user = User(id=uuid.uuid4(), external_id=external_id)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # 동시 요청이 먼저 만든 경우
        await db.rollback()
        user = await get_user(db, external_id)
        if user is None:
            raise
    return user
