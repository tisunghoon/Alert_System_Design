import uuid
from datetime import datetime

from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class App(Base):
    __tablename__ = "apps"

    id: Mapped[uuid.UUID] = uuid_pk()
    app_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    app_secret: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
