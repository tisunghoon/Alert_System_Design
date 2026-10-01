import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Notification, NotificationStatusHistory
from app.services import notification_log


def make_db(existing: Notification | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.get.return_value = existing
    return db


def added(db, model):
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], model)]


def make_notification(**kwargs) -> Notification:
    defaults = dict(
        id=uuid.uuid4(),
        event_id="evt-1",
        app_id=uuid.uuid4(),
        channel="ios",
        recipient_id="u1",
        body="hi",
        status="QUEUED",
        queued_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    return Notification(**{**defaults, **kwargs})


async def create(db, **overrides):
    args = dict(
        event_id="evt-1",
        app_id=uuid.uuid4(),
        channel="sms",
        recipient_id="u1",
        title="t",
        body="b",
        template_id=None,
        db=db,
    )
    return await notification_log.create_queued_log(**{**args, **overrides})


async def test_create_queued_log_records_queued_notification_and_history():
    db = make_db()
    n = await create(db)
    assert added(db, Notification) == [n]
    assert (n.status, n.retry_count, n.event_id, n.channel) == ("QUEUED", 0, "evt-1", "sms")
    [history] = added(db, NotificationStatusHistory)
    assert (history.notification_id, history.status) == (n.id, "QUEUED")
    db.flush.assert_awaited_once()


async def test_create_queued_log_propagates_db_error():
    db = make_db()
    db.flush.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        await create(db)


async def test_update_status_records_history_with_worker_and_note():
    n = make_notification()
    db = make_db(n)
    await notification_log.update_status(n.id, "PROCESSING", "worker-1", None, db)
    assert n.status == "PROCESSING"
    [history] = added(db, NotificationStatusHistory)
    assert (history.status, history.worker_id, history.note) == ("PROCESSING", "worker-1", None)


async def test_update_status_sets_final_timestamps():
    delivered, failed = make_notification(), make_notification()
    await notification_log.update_status(delivered.id, "DELIVERED", "w", None, make_db(delivered))
    await notification_log.update_status(failed.id, "FAILED", "w", "timeout", make_db(failed))
    assert delivered.delivered_at is not None and delivered.failed_at is None
    assert failed.failed_at is not None and failed.delivered_at is None


async def test_update_status_rejects_unknown_status():
    n = make_notification()
    with pytest.raises(ValueError):
        await notification_log.update_status(n.id, "DONE", "w", None, make_db(n))


async def test_update_status_unknown_notification():
    with pytest.raises(LookupError):
        await notification_log.update_status(uuid.uuid4(), "PROCESSING", "w", None, make_db(None))


async def test_set_final_duration_uses_delivered_at():
    queued = datetime.now(UTC) - timedelta(seconds=5)
    n = make_notification(queued_at=queued, delivered_at=queued + timedelta(milliseconds=1500))
    assert await notification_log.set_final_duration(n.id, make_db(n)) == 1500
    assert n.total_duration_ms == 1500


async def test_set_final_duration_falls_back_to_failed_at():
    queued = datetime.now(UTC) - timedelta(seconds=5)
    n = make_notification(queued_at=queued, failed_at=queued + timedelta(seconds=3))
    assert await notification_log.set_final_duration(n.id, make_db(n)) == 3000


async def test_set_final_duration_unknown_notification():
    with pytest.raises(LookupError):
        await notification_log.set_final_duration(uuid.uuid4(), make_db(None))


async def test_get_by_event_id_returns_notification_or_none():
    n = make_notification()
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = n
    db.execute.return_value = result
    assert await notification_log.get_by_event_id("evt-1", db) is n
    result.scalar_one_or_none.return_value = None
    assert await notification_log.get_by_event_id("missing", db) is None
