from datetime import UTC, datetime, timedelta

from app.models import Notification

START = datetime(2024, 1, 1, tzinfo=UTC)


def make(app_row, event_id, channel, status, queued_at, retry_count=0):
    return Notification(
        event_id=event_id,
        app_id=app_row.id,
        channel=channel,
        recipient_id="u1",
        body="b",
        status=status,
        retry_count=retry_count,
        queued_at=queued_at,
    )


async def test_stats_aggregate_by_channel_within_range(http, db, app_row):
    db.add_all(
        [
            make(app_row, "e1", "sms", "DELIVERED", START + timedelta(days=1), retry_count=1),
            make(app_row, "e2", "sms", "DELIVERED", START + timedelta(days=2)),
            make(app_row, "e3", "sms", "FAILED", START + timedelta(days=3), retry_count=3),
            make(app_row, "e4", "ios", "FAILED", START + timedelta(days=4), retry_count=2),
            make(app_row, "e5", "sms", "QUEUED", START + timedelta(days=5)),
            make(app_row, "e6", "sms", "DELIVERED", START + timedelta(days=40)),
            make(app_row, "e7", "email", "DELIVERED", START - timedelta(days=1)),
        ]
    )
    await db.commit()

    params = {"start": START.isoformat(), "end": (START + timedelta(days=10)).isoformat()}
    resp = await http.get("/monitoring/stats", params=params)
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    assert channels["sms"] == {"delivered": 2, "failed": 1, "retries": 4}
    assert channels["ios"] == {"delivered": 0, "failed": 1, "retries": 2}
    assert channels["email"] == {"delivered": 0, "failed": 0, "retries": 0}
    assert channels["android"] == {"delivered": 0, "failed": 0, "retries": 0}


async def test_stats_range_over_30_days_is_rejected(http):
    params = {"start": START.isoformat(), "end": (START + timedelta(days=31)).isoformat()}
    assert (await http.get("/monitoring/stats", params=params)).status_code == 400


async def test_health_and_queues_with_real_services(http, monkeypatch, redis_client):
    from app.api import monitoring

    monkeypatch.setattr(monitoring, "get_redis", lambda: redis_client)
    await redis_client.xadd("sms_stream", {"a": "1"})

    resp = await http.get("/health")
    assert resp.status_code == 200
    assert set(resp.json()["components"].values()) == {"healthy"}

    queues = {q["channel"]: q["size"] for q in (await http.get("/monitoring/queues")).json()["queues"]}
    assert queues["sms"] == 1
