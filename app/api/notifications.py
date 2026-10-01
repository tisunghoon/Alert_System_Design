import asyncio
import logging
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import CHANNEL_STREAMS, get_redis, xadd_notification
from app.models import App, User, UserPreference
from app.schemas.notification import NotificationAccepted, NotificationDetail, NotificationRequest
from app.services import notification_log
from app.services.auth import get_current_app
from app.services.deduplication import check_duplicate, mark_processed
from app.services.rate_limiter import WINDOW_SECONDS, check_rate_limit
from app.services.template_service import (
    TemplateNotFoundError,
    TemplateVariableMismatchError,
    get_template,
    render_template,
)

logger = logging.getLogger(__name__)

router = APIRouter()

QUEUE_TIMEOUT_SECONDS = 3


def redis_client() -> aioredis.Redis:
    return get_redis()


def _error(status: int, code: str, message: str, details: list[dict] | None = None, **kwargs) -> HTTPException:
    detail = {"code": code, "message": message, "details": details or []}
    return HTTPException(status_code=status, detail=detail, **kwargs)


def _reason(error: dict) -> str:
    kind = error["type"]
    if kind == "missing":
        return "필수 필드가 누락되었습니다."
    if kind == "literal_error":
        return f"'{error['input']}'는 유효하지 않은 값입니다. (허용값: {error['ctx']['expected'].replace(' or ', ', ')})"
    if kind in ("string_too_short", "string_too_long"):
        ctx = error["ctx"]
        limit = f"{ctx['min_length']}자 이상" if kind == "string_too_short" else f"{ctx['max_length']}자 이하"
        return f"길이는 {limit}이어야 합니다."
    return "유효하지 않은 값입니다."


def _validate(payload: dict) -> NotificationRequest:
    details = []
    req = None
    try:
        req = NotificationRequest.model_validate(payload)
    except ValidationError as e:
        details = [{"field": ".".join(str(p) for p in err["loc"]), "reason": _reason(err)} for err in e.errors()]
    # 템플릿이 없으면 body가 필수다.
    if payload.get("body") is None and payload.get("template_id") is None:
        details.append({"field": "body", "reason": "필수 필드가 누락되었습니다."})
    if details:
        raise _error(400, "VALIDATION_ERROR", "요청 필드 유효성 검증에 실패했습니다.", details)
    return req


async def _channel_disabled(recipient_id: str, channel: str, db: AsyncSession) -> bool:
    stmt = (
        select(UserPreference.is_enabled)
        .join(User, User.id == UserPreference.user_id)
        .where(User.external_id == recipient_id, UserPreference.channel == channel)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is False


async def _render(req: NotificationRequest, db: AsyncSession, client: aioredis.Redis) -> tuple[str | None, str]:
    if req.template_id is None:
        return req.title, req.body
    try:
        template = await get_template(db, client, req.template_id)
        rendered = render_template(template, req.template_variables or {})
    except TemplateNotFoundError as e:
        raise _error(404, "TEMPLATE_NOT_FOUND", str(e)) from None
    except TemplateVariableMismatchError as e:
        details = [{"field": f"template_variables.{name}", "reason": "필수 변수가 누락되었습니다."} for name in e.missing]
        details += [{"field": f"template_variables.{name}", "reason": "템플릿에 없는 변수입니다."} for name in e.extra]
        raise _error(400, "TEMPLATE_VARIABLE_MISMATCH", "템플릿 변수가 일치하지 않습니다.", details) from None
    return rendered["title"], rendered["body"]


@router.post("/notifications", response_model=NotificationAccepted, status_code=202)
async def create_notification(
    request: Request,
    response: Response,
    app: App = Depends(get_current_app),
    db: AsyncSession = Depends(get_db),
    client: aioredis.Redis = Depends(redis_client),
):
    payload = await request.json()

    recipient, channel = payload.get("recipient_id"), payload.get("channel")
    if isinstance(recipient, str) and recipient and isinstance(channel, str) and channel in CHANNEL_STREAMS:
        if not await check_rate_limit(recipient, channel, db, client):
            raise _error(
                429, "RATE_LIMIT_EXCEEDED", "요청 한도를 초과했습니다.", headers={"Retry-After": str(WINDOW_SECONDS)}
            )

    event_id = payload.get("event_id")
    if isinstance(event_id, str) and event_id and await check_duplicate(event_id, db, client):
        raise _error(409, "DUPLICATE_EVENT", f"이미 처리된 event_id입니다: {event_id}")

    req = _validate(payload)
    event_id = req.event_id or f"evt_{uuid.uuid4().hex}"

    if await _channel_disabled(req.recipient_id, req.channel, db):
        response.status_code = 200
        return NotificationAccepted(event_id=event_id, status="skipped", reason="channel_disabled")

    title, body = await _render(req, db, client)

    try:
        log = await notification_log.create_queued_log(
            event_id, app.id, req.channel, req.recipient_id, title, body, req.template_id, db
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _error(409, "DUPLICATE_EVENT", f"이미 처리된 event_id입니다: {event_id}") from None
    except SQLAlchemyError:
        logger.exception("Notification_Log 기록에 실패했습니다: event_id=%s", event_id)
        await db.rollback()
        raise _error(503, "LOG_WRITE_FAILED", "알림 로그 기록에 실패했습니다.") from None

    try:
        await mark_processed(event_id, client)
    except RedisError:
        logger.warning("중복 방지 키 저장에 실패했습니다: event_id=%s", event_id)

    fields = {
        "event_id": event_id,
        "app_id": app.id,
        "channel": req.channel,
        "recipient_id": req.recipient_id,
        "title": title or "",
        "body": body,
        "retry_count": 0,
        "next_retry_after": "",
        "enqueued_at": datetime.now(UTC).isoformat(),
    }
    try:
        await asyncio.wait_for(
            xadd_notification(client, CHANNEL_STREAMS[req.channel], fields), QUEUE_TIMEOUT_SECONDS
        )
    except (TimeoutError, RedisError):
        logger.exception("Message_Queue 삽입에 실패했습니다: event_id=%s", event_id)
        raise _error(503, "QUEUE_UNAVAILABLE", "서비스를 일시적으로 사용할 수 없습니다.") from None

    return NotificationAccepted(event_id=event_id, status="queued", queued_at=log.queued_at)


@router.get("/notifications/{event_id}", response_model=NotificationDetail)
async def get_notification(event_id: str, db: AsyncSession = Depends(get_db)):
    notification = await notification_log.get_by_event_id(event_id, db)
    if notification is None:
        raise _error(404, "NOTIFICATION_NOT_FOUND", f"event_id를 찾을 수 없습니다: {event_id}")
    return NotificationDetail(
        **{c: getattr(notification, c) for c in NotificationDetail.model_fields if c != "history"},
        history=[
            {"status": h.status, "worker_id": h.worker_id, "changed_at": h.changed_at, "note": h.note}
            for h in notification.history
        ],
    )
