"""초기 스키마

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    """
CREATE TABLE apps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_key     VARCHAR(256) NOT NULL UNIQUE,
    app_secret  VARCHAR(256) NOT NULL,
    name        VARCHAR(256) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
    """,
    """
CREATE TABLE users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id VARCHAR(128) NOT NULL UNIQUE,  -- recipient_id
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
    """,
    """
CREATE TABLE devices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     VARCHAR(16) NOT NULL CHECK (channel IN ('ios', 'android', 'sms', 'email')),
    token       VARCHAR(512) NOT NULL,          -- 디바이스 토큰 / 전화번호 / 이메일
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, channel, token)
)
    """,
    """
CREATE INDEX idx_devices_user_id ON devices(user_id)
    """,
    """
CREATE TABLE user_preferences (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     VARCHAR(16) NOT NULL CHECK (channel IN ('ios', 'android', 'sms', 'email')),
    is_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, channel)
)
    """,
    """
CREATE TABLE notification_templates (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(256) NOT NULL UNIQUE,
    title        VARCHAR(200),                      -- 최대 200자
    body         TEXT NOT NULL,                     -- 최대 10,000자
    placeholders JSONB NOT NULL DEFAULT '[]',       -- ["{{name}}", "{{item}}"] 최대 50개
    ref_count    INTEGER NOT NULL DEFAULT 0,        -- 활성 규칙 참조 수
    is_deleted   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
    """,
    """
CREATE TABLE notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        VARCHAR(256) NOT NULL UNIQUE,   -- 외부 제공 또는 시스템 생성
    app_id          UUID NOT NULL REFERENCES apps(id),
    channel         VARCHAR(16) NOT NULL CHECK (channel IN ('ios', 'android', 'sms', 'email')),
    recipient_id    VARCHAR(128) NOT NULL,
    title           VARCHAR(256),
    body            TEXT NOT NULL,
    template_id     UUID REFERENCES notification_templates(id),
    status          VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED', 'PROCESSING', 'DELIVERED', 'FAILED')),
    retry_count     INTEGER NOT NULL DEFAULT 0,
    total_duration_ms BIGINT,                      -- QUEUED → 최종 상태까지 경과 시간
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at    TIMESTAMPTZ,
    failed_at       TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ                    -- 30일 보존 기준
        GENERATED ALWAYS AS (((queued_at AT TIME ZONE 'UTC') + INTERVAL '30 days') AT TIME ZONE 'UTC') STORED
)
    """,
    """
CREATE INDEX idx_notifications_event_id ON notifications(event_id)
    """,
    """
CREATE INDEX idx_notifications_status   ON notifications(status)
    """,
    """
CREATE INDEX idx_notifications_queued_at ON notifications(queued_at)
    """,
    """
CREATE TABLE notification_status_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    status          VARCHAR(16) NOT NULL,
    worker_id       VARCHAR(128),                  -- 상태 전환한 Worker ID
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note            TEXT                           -- 실패 원인 등 메모
)
    """,
    """
CREATE INDEX idx_status_history_notification_id ON notification_status_history(notification_id)
    """,
]

TABLES = [
    "notification_status_history",
    "notifications",
    "notification_templates",
    "user_preferences",
    "devices",
    "users",
    "apps",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE {table}")
