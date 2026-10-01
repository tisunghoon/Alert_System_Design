import pytest

from app.services import deduplication
from app.services import notification_log as log


async def create_with_status(db, app_row, event_id, status):
    n = await log.create_queued_log(event_id, app_row.id, "sms", "u1", None, "b", None, db)
    if status != "QUEUED":
        await log.update_status(n.id, status, "w1", None, db)
    await db.commit()


@pytest.mark.parametrize(
    ("status", "expected"),
    [("DELIVERED", True), ("QUEUED", False), ("PROCESSING", False), ("FAILED", False)],
)
async def test_db_fallback_only_counts_delivered(db, app_row, redis_client, status, expected):
    await create_with_status(db, app_row, "evt_1", status)
    assert await deduplication.check_duplicate("evt_1", db, client=redis_client) is expected


async def test_unknown_event_id_is_not_duplicate(db, app_row, redis_client):
    assert not await deduplication.check_duplicate("evt_none", db, client=redis_client)


async def test_cache_hit_without_db_record(db, redis_client):
    await deduplication.mark_processed("evt_1", client=redis_client)
    assert await deduplication.check_duplicate("evt_1", db, client=redis_client)
    assert 0 < await redis_client.ttl("dedup:evt_1") <= 24 * 60 * 60
