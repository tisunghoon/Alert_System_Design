import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import notification_log as log


async def create(db, app_row, event_id="evt_1", channel="sms"):
    notification = await log.create_queued_log(
        event_id, app_row.id, channel, "u1", "title", "body", None, db
    )
    await db.commit()
    return notification


async def test_get_by_event_id_returns_history_in_chronological_order(db, app_row):
    n = await create(db, app_row)
    await log.update_status(n.id, "PROCESSING", "w1", None, db)
    await asyncio.sleep(0.01)
    await log.update_status(n.id, "DELIVERED", "w1", "ok", db)
    await db.commit()
    db.expire_all()

    found = await log.get_by_event_id("evt_1", db)
    assert found.status == "DELIVERED"
    assert found.delivered_at is not None
    assert [h.status for h in found.history] == ["QUEUED", "PROCESSING", "DELIVERED"]
    assert await log.get_by_event_id("missing", db) is None


async def test_expires_at_is_queued_at_plus_30_days(db, app_row):
    n = await create(db, app_row)
    await db.refresh(n)
    assert n.expires_at - n.queued_at == timedelta(days=30)


async def test_set_final_duration_uses_delivered_at(db, app_row):
    n = await create(db, app_row)
    await asyncio.sleep(0.05)
    await log.update_status(n.id, "DELIVERED", "w1", None, db)
    duration = await log.set_final_duration(n.id, db)
    await db.commit()
    await db.refresh(n)
    assert n.total_duration_ms == duration
    assert duration >= 50


async def test_duplicate_event_id_violates_unique(db, app_row):
    await create(db, app_row)
    with pytest.raises(IntegrityError):
        await create(db, app_row)
    await db.rollback()


async def test_invalid_channel_and_status_violate_check(db, app_row):
    with pytest.raises(IntegrityError):
        await create(db, app_row, event_id="evt_bad", channel="push")
    await db.rollback()
    await db.refresh(app_row)

    n = await create(db, app_row)
    n.status = "UNKNOWN"
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_update_status_unknown_notification(db):
    with pytest.raises(LookupError):
        await log.update_status(uuid.uuid4(), "DELIVERED", None, None, db)
