import pytest
from sqlalchemy import CheckConstraint, Computed, UniqueConstraint, create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers

from app.models import (
    Base,
    Device,
    Notification,
    NotificationStatusHistory,
    User,
    UserPreference,
)


def table(name):
    return Base.metadata.tables[name]


def check_texts(tbl):
    return {str(c.sqltext) for c in tbl.constraints if isinstance(c, CheckConstraint)}


def unique_columns(tbl):
    return {tuple(c.name for c in u.columns) for u in tbl.constraints if isinstance(u, UniqueConstraint)}


def test_all_tables_defined():
    assert set(Base.metadata.tables) == {
        "apps",
        "users",
        "devices",
        "user_preferences",
        "notification_templates",
        "notifications",
        "notification_status_history",
    }


def test_relationships_resolve():
    configure_mappers()
    assert User.devices.property.mapper.class_ is Device
    assert User.preferences.property.mapper.class_ is UserPreference
    assert Notification.history.property.mapper.class_ is NotificationStatusHistory


@pytest.mark.parametrize("name", ["devices", "user_preferences", "notifications"])
def test_channel_check_constraint(name):
    assert "channel IN ('ios', 'android', 'sms', 'email')" in check_texts(table(name))


def test_notification_status_check_and_defaults():
    t = table("notifications")
    assert "status IN ('QUEUED', 'PROCESSING', 'DELIVERED', 'FAILED')" in check_texts(t)
    assert t.c.status.default.arg == "QUEUED"
    assert t.c.retry_count.default.arg == 0


def test_notification_expires_at_is_generated():
    col = table("notifications").c.expires_at
    assert isinstance(col.computed, Computed)
    assert col.computed.persisted is True
    assert "30 days" in str(col.computed.sqltext)


def test_unique_constraints():
    assert table("apps").c.app_key.unique
    assert table("users").c.external_id.unique
    assert table("notifications").c.event_id.unique
    assert table("notification_templates").c.name.unique
    assert ("user_id", "channel", "token") in unique_columns(table("devices"))
    assert ("user_id", "channel") in unique_columns(table("user_preferences"))


def test_indexes():
    names = {i.name for t in Base.metadata.tables.values() for i in t.indexes}
    assert {
        "idx_devices_user_id",
        "idx_notifications_event_id",
        "idx_notifications_status",
        "idx_notifications_queued_at",
        "idx_status_history_notification_id",
    } <= names


def test_foreign_key_delete_rules():
    def ondelete(tbl, col):
        return next(iter(table(tbl).c[col].foreign_keys)).ondelete

    assert ondelete("devices", "user_id") == "CASCADE"
    assert ondelete("user_preferences", "user_id") == "CASCADE"
    assert ondelete("notification_status_history", "notification_id") == "CASCADE"
    assert ondelete("notifications", "app_id") is None


def test_template_column_limits():
    t = table("notification_templates")
    assert t.c.title.type.length == 200
    assert t.c.title.nullable is True
    assert t.c.placeholders.nullable is False
    assert t.c.is_deleted.default.arg is False


@pytest.fixture
def session():
    # users/devices/user_preferences만 SQLite로 만든다. 나머지 테이블은 JSONB, GENERATED 때문에 PostgreSQL이 필요하다.
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine, tables=[table("users"), table("devices"), table("user_preferences")]
    )
    with Session(engine) as s:
        yield s


def test_device_invalid_channel_rejected(session):
    user = User(external_id="u1")
    session.add_all([user, Device(user=user, channel="fax", token="t")])
    with pytest.raises(IntegrityError):
        session.flush()


def test_device_duplicate_rejected(session):
    user = User(external_id="u1")
    session.add_all([user, Device(user=user, channel="sms", token="t")])
    session.flush()
    session.add(Device(user_id=user.id, channel="sms", token="t"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_preference_unique_per_channel(session):
    user = User(external_id="u1")
    session.add_all([user, UserPreference(user=user, channel="email")])
    session.flush()
    session.add(UserPreference(user_id=user.id, channel="email"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_user_external_id_unique(session):
    session.add_all([User(external_id="u1"), User(external_id="u1")])
    with pytest.raises(IntegrityError):
        session.flush()
