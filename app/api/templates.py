import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis
from app.models import App
from app.models.notification_template import NotificationTemplate
from app.schemas.template import TemplateIn, TemplateOut, extract_placeholders
from app.services.auth import get_current_app
from app.services.template_service import TemplateNotFoundError, get_template

router = APIRouter(prefix="/templates", tags=["templates"])


def _not_found(template_id: uuid.UUID) -> HTTPException:
    return HTTPException(404, f"템플릿을 찾을 수 없습니다: {template_id}")


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "같은 이름의 템플릿이 이미 존재합니다.") from None


async def _active_template(db: AsyncSession, template_id: uuid.UUID) -> NotificationTemplate:
    row = await db.get(NotificationTemplate, template_id)
    if row is None or row.is_deleted:
        raise _not_found(template_id)
    return row


@router.post("", status_code=201, response_model=TemplateOut)
async def create_template(
    payload: TemplateIn,
    db: AsyncSession = Depends(get_db),
    _app: App = Depends(get_current_app),
):
    row = NotificationTemplate(
        id=uuid.uuid4(),
        name=payload.name,
        title=payload.title,
        body=payload.body,
        placeholders=extract_placeholders(payload.title, payload.body),
    )
    db.add(row)
    await _commit(db)
    return row


@router.get("/{template_id}", response_model=TemplateOut)
async def read_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
):
    try:
        return await get_template(db, redis, template_id)
    except TemplateNotFoundError:
        raise _not_found(template_id) from None


@router.put("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID,
    payload: TemplateIn,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
):
    row = await _active_template(db, template_id)
    row.name = payload.name
    row.title = payload.title
    row.body = payload.body
    row.placeholders = extract_placeholders(payload.title, payload.body)
    await _commit(db)
    await redis.delete(f"template:{template_id}")
    return row


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _app: App = Depends(get_current_app),
):
    row = await _active_template(db, template_id)
    if row.ref_count > 0:
        raise HTTPException(409, f"활성 규칙 {row.ref_count}개가 이 템플릿을 참조하고 있어 삭제할 수 없습니다.")
    row.is_deleted = True
    await _commit(db)
    await redis.delete(f"template:{template_id}")
    return Response(status_code=204)
