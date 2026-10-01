import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import httpx
import pytest

from app.api import monitoring
from app.core.config import settings
from app.core.database import get_db
from app.main import app


@pytest.fixture
async def redis_client():
    c = fakeredis.FakeAsyncRedis(decode_responses=True)
    with patch.object(monitoring, "get_redis", return_value=c):
        yield c
    await c.aclose()


@pytest.fixture
def db():
    session = AsyncMock()
    app.dependency_overrides[get_db] = lambda: session
    yield session
    app.dependency_overrides.clear()


@pytest.fixture
async def http():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_queues_report_size_per_channel(http, redis_client):
    await redis_client.xadd("sms_stream", {"a": "1"})
    await redis_client.xadd("sms_stream", {"a": "2"})
    body = (await http.get("/monitoring/queues")).json()
    assert body["queues"] == [
        {"channel": "ios", "queue": "ios_stream", "size": 0},
        {"channel": "android", "queue": "android_stream", "size": 0},
        {"channel": "sms", "queue": "sms_stream", "size": 2},
        {"channel": "email", "queue": "email_stream", "size": 0},
    ]


async def test_queue_over_threshold_logs_warning(http, redis_client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "QUEUE_ALERT_THRESHOLD", 1)
    for _ in range(2):
        await redis_client.xadd("email_stream", {"a": "1"})
    with caplog.at_level(logging.WARNING):
        await http.get("/monitoring/queues")
    assert "channel=email size=2 threshold=1" in caplog.text


@pytest.mark.parametrize("params", [{}, {"start": "2024-01-01T00:00:00"}, {"end": "2024-01-02T00:00:00"}])
async def test_stats_requires_time_range(http, db, params):
    assert (await http.get("/monitoring/stats", params=params)).status_code == 400


async def test_stats_rejects_range_over_30_days_or_reversed(http, db):
    start = datetime(2024, 1, 1)
    over = {"start": start.isoformat(), "end": (start + timedelta(days=30, seconds=1)).isoformat()}
    assert (await http.get("/monitoring/stats", params=over)).status_code == 400
    reversed_ = {"start": "2024-01-02T00:00:00", "end": "2024-01-01T00:00:00"}
    assert (await http.get("/monitoring/stats", params=reversed_)).status_code == 400


async def test_stats_aggregates_and_fills_missing_channels(http, db):
    result = MagicMock()
    result.all.return_value = [("sms", 5, 2, 7)]
    db.execute.return_value = result
    params = {"start": "2024-01-01T00:00:00", "end": "2024-01-31T00:00:00"}
    resp = await http.get("/monitoring/stats", params=params)
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    assert channels["sms"] == {"delivered": 5, "failed": 2, "retries": 7}
    assert channels["ios"] == {"delivered": 0, "failed": 0, "retries": 0}


async def test_health_ok(http, db, redis_client):
    resp = await http.get("/health")
    assert resp.status_code == 200
    assert resp.json()["components"] == {
        "database": "healthy", "cache": "healthy", "message_queue": "healthy"
    }


async def test_health_timeout_counts_as_unhealthy(http, db, redis_client, monkeypatch):
    async def slow(*_):
        await asyncio.sleep(1)

    monkeypatch.setattr(monitoring, "HEALTH_TIMEOUT_SECONDS", 0.05)
    db.execute.side_effect = slow
    resp = await http.get("/health")
    assert resp.status_code == 503
    assert resp.json()["unhealthy"] == ["database"]
