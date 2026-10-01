import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import CHANNEL_CHECK, Base, uuid_pk

STATUSES = ("QUEUED", "PROCESSING", "DELIVERED", "FAILED")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(CHANNEL_CHECK),
        CheckConstraint("status IN ('QUEUED', 'PROCESSING', 'DELIVERED', 'FAILED')"),
        Index("idx_notifications_event_id", "event_id"),
        Index("idx_notifications_status", "status"),
        Index("idx_notifications_queued_at", "queued_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notification_templates.id")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="QUEUED", server_default="QUEUED"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    total_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), Computed("queued_at + INTERVAL '30 days'", persisted=True)
    )

    history: Mapped[list["NotificationStatusHistory"]] = relationship(
        back_populates="notification",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="NotificationStatusHistory.changed_at",
    )
