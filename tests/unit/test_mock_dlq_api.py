from unittest.mock import patch

import fakeredis
import httpx
import pytest

from app.api import dead_letter, mocks
from app.main import app
from app.mocks import config_store


@pytest.fixture
async def redis_client():
    c = fakeredis.FakeAsyncRedis(decode_responses=True)
    with (
        patch.object(mocks, "get_redis", return_value=c),
        patch.object(dead_letter, "get_redis", return_value=c),
    ):
        yield c
    await c.aclose()


@pytest.fixture
async def http(redis_client):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_dead_letter_empty(http):
    resp = await http.get("/dead-letter")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_dead_letter_lists_fields(http, redis_client):
    await redis_client.xadd(
        "dead_letter_stream",
        {
            "notification_id": "n1",
            "failed_at": "2024-01-15T10:00:00Z",
            "failure_reason": "timeout",
            "retry_count": "3",
        },
    )
    items = (await http.get("/dead-letter")).json()["items"]
    assert items == [
        {
            "notification_id": "n1",
            "failed_at": "2024-01-15T10:00:00Z",
            "failure_reason": "timeout",
            "retry_count": 3,
        }
    ]


async def test_config_update_and_read_back(http, redis_client):
    resp = await http.put("/mocks/sms/config", json={"success_rate": 40, "delay_ms": 100})
    assert resp.status_code == 200
    assert resp.json() == {"channel": "sms", "success_rate": 40, "delay_ms": 100}
    assert await config_store.load_config(redis_client, "sms") == (40, 100)


@pytest.mark.parametrize(
    "body", [{"success_rate": 101}, {"success_rate": -1}, {"delay_ms": 30001}, {"delay_ms": -1}]
)
async def test_config_rejects_out_of_range(http, body):
    assert (await http.put("/mocks/sms/config", json=body)).status_code == 422


async def test_unknown_channel_is_404(http):
    assert (await http.put("/mocks/push/config", json={})).status_code == 404
    assert (await http.get("/mocks/push/records")).status_code == 404


async def test_records_and_reset(http, redis_client):
    await config_store.push_record(redis_client, "email", {"recipient_id": "u1"})
    body = (await http.get("/mocks/email/records")).json()
    assert body["count"] == 1 and body["records"] == [{"recipient_id": "u1"}]

    await http.put("/mocks/email/config", json={"success_rate": 5})
    assert (await http.post("/mocks/reset")).status_code == 200
    assert (await http.get("/mocks/email/records")).json()["count"] == 0
    assert await config_store.load_config(redis_client, "email") == (100, 0)
