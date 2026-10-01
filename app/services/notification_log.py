import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Notification, NotificationStatusHistory
from app.models.notification import STATUSES


def _now() -> datetime:
    return datetime.now(UTC)


async def create_queued_log(
    event_id: str,
    app_id: uuid.UUID,
    channel: str,
    recipient_id: str,
    title: str | None,
    body: str,
    template_id: uuid.UUID | None,
    db: AsyncSession,
) -> Notification:
    now = _now()
    notification = Notification(
        id=uuid.uuid4(),
        event_id=event_id,
        app_id=app_id,
        channel=channel,
        recipient_id=recipient_id,
        title=title,
        body=body,
        template_id=template_id,
        status="QUEUED",
        retry_count=0,
        queued_at=now,
    )
    db.add(notification)
    db.add(NotificationStatusHistory(notification_id=notification.id, status="QUEUED", changed_at=now))
    await db.flush()
    return notification


async def update_status(
    notification_id: uuid.UUID,
    status: str,
    worker_id: str | None,
    note: str | None,
    db: AsyncSession,
) -> Notification:
    if status not in STATUSES:
        raise ValueError(f"유효하지 않은 상태: {status}")
    notification = await db.get(Notification, notification_id)
    if notification is None:
        raise LookupError(f"알림을 찾을 수 없습니다: {notification_id}")

    now = _now()
    notification.status = status
    if status == "DELIVERED":
        notification.delivered_at = now
    elif status == "FAILED":
        notification.failed_at = now
    db.add(
        NotificationStatusHistory(
            notification_id=notification.id,
            status=status,
            worker_id=worker_id,
            changed_at=now,
            note=note,
        )
    )
    await db.flush()
    return notification


async def set_final_duration(notification_id: uuid.UUID, db: AsyncSession) -> int:
    notification = await db.get(Notification, notification_id)
    if notification is None:
        raise LookupError(f"알림을 찾을 수 없습니다: {notification_id}")

    finished_at = notification.delivered_at or notification.failed_at or _now()
    notification.total_duration_ms = int((finished_at - notification.queued_at).total_seconds() * 1000)
    await db.flush()
    return notification.total_duration_ms


async def get_by_event_id(event_id: str, db: AsyncSession) -> Notification | None:
    result = await db.execute(
        select(Notification)
        .where(Notification.event_id == event_id)
        .options(selectinload(Notification.history))
    )
    return result.scalar_one_or_none()
