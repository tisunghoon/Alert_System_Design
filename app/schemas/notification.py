import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.device import Channel


class NotificationRequest(BaseModel):
    event_id: str | None = Field(default=None, min_length=1, max_length=256)
    channel: Channel
    recipient_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    body: str | None = Field(default=None, min_length=1, max_length=4096)
    template_id: uuid.UUID | None = None
    template_variables: dict[str, str] | None = None


class NotificationAccepted(BaseModel):
    event_id: str
    status: Literal["queued", "skipped"]
    queued_at: datetime | None = None
    reason: str | None = None


class StatusHistoryItem(BaseModel):
    status: str
    worker_id: str | None
    changed_at: datetime
    note: str | None


class NotificationDetail(BaseModel):
    event_id: str
    status: str
    channel: str
    recipient_id: str
    title: str | None
    body: str
    template_id: uuid.UUID | None
    retry_count: int
    total_duration_ms: int | None
    queued_at: datetime
    delivered_at: datetime | None
    failed_at: datetime | None
    history: list[StatusHistoryItem]
