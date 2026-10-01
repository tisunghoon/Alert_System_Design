import uuid

from redis.exceptions import ConnectionError
from sqlalchemy import select

from app.models import App, Notification
from .e2e_helpers import AUTH, get_notification, post_notification


async def test_post_to_delivered_with_history_duration_and_mock_record(
    http, app_row, make_worker, redis_client
):
    worker = await make_worker("ios")
    resp = await post_notification(http, "evt-1", "ios", "u1")
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert await redis_client.xlen("ios_stream") == 1

    await worker.poll_once()

    detail = (await get_notification(http, "evt-1")).json()
    assert detail["status"] == "DELIVERED"
    assert detail["delivered_at"] is not None
    assert detail["total_duration_ms"] is not None and detail["total_duration_ms"] >= 0
    assert [h["status"] for h in detail["history"]] == ["QUEUED", "PROCESSING", "DELIVERED"]
    assert detail["history"][1]["worker_id"] == "ios"

    records = (await http.get("/mocks/ios/records")).json()
    assert records["count"] == 1
    assert records["records"][0]["recipient_id"] == "u1"
    assert records["records"][0]["body"] == "hello"
    assert (await redis_client.xpending("ios_stream", "notification_consumers"))["pending"] == 0


async def test_disabled_channel_is_skipped_and_never_reaches_mock(
    http, app_row, make_worker, redis_client, db
):
    ios_worker = await make_worker("ios")
    sms_worker = await make_worker("sms")
    await http.put("/users/u1/preferences", json={**AUTH, "preferences": {"ios": False}})

    skipped = await post_notification(http, "evt-ios", "ios", "u1")
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert await redis_client.xlen("ios_stream") == 0
    assert await db.scalar(select(Notification).where(Notification.event_id == "evt-ios")) is None

    assert (await post_notification(http, "evt-sms", "sms", "u1")).status_code == 202
    await ios_worker.poll_once()
    await sms_worker.poll_once()

    assert (await http.get("/mocks/ios/records")).json()["count"] == 0
    assert (await http.get("/mocks/sms/records")).json()["count"] == 1


async def test_queue_failure_leaves_only_queued_log_and_no_stream_entry(
    http, app_row, redis_client, db, monkeypatch
):
    from app.api import notifications

    async def broken_xadd(*_args, **_kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(notifications, "xadd_notification", broken_xadd)
    resp = await post_notification(http, "evt-wal", "ios", "u1")
    assert resp.status_code == 503

    stored = await db.scalar(select(Notification).where(Notification.event_id == "evt-wal"))
    assert stored is not None and stored.status == "QUEUED"
    assert await redis_client.xlen("ios_stream") == 0


async def test_other_app_cannot_read_notification(http, app_row, db):
    db.add(App(id=uuid.uuid4(), app_key="other-key", app_secret="other-secret", name="other"))
    await db.commit()
    await post_notification(http, "evt-1", "ios", "u1")

    owner = await get_notification(http, "evt-1")
    other = await get_notification(
        http, "evt-1", auth={"app_key": "other-key", "app_secret": "other-secret"}
    )
    assert owner.status_code == 200
    assert other.status_code == 404
    assert (await get_notification(http, "evt-missing")).status_code == 404
