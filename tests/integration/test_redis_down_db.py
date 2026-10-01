import pytest
import redis.asyncio as aioredis

from app.core.redis import get_redis
from app.main import app

AUTH = {"X-App-Key": "test-key", "X-App-Secret": "test-secret"}


@pytest.fixture
async def redis_down():
    # 아무도 듣지 않는 포트라서 실제 연결 거부(ConnectionError)가 발생한다.
    client = aioredis.from_url("redis://127.0.0.1:1", decode_responses=True)
    app.dependency_overrides[get_redis] = lambda: client
    yield client
    await client.aclose()


async def test_devices_and_preferences_work_without_redis(http, app_row, redis_down):
    resp = await http.post("/users/u1/devices", headers=AUTH, json={"channel": "sms", "token": "010-1234"})
    assert resp.status_code == 201
    device_id = resp.json()["id"]

    listed = await http.get("/users/u1/devices", headers=AUTH)
    assert [d["id"] for d in listed.json()] == [device_id]

    put = await http.put("/users/u1/preferences", headers=AUTH, json={"preferences": {"sms": False}})
    assert put.status_code == 200
    assert (await http.get("/users/u1/preferences", headers=AUTH)).json()["sms"] is False

    assert (await http.delete(f"/users/u1/devices/{device_id}", headers=AUTH)).status_code == 204
    assert (await http.get("/users/u1/devices", headers=AUTH)).json() == []


async def test_templates_work_without_redis(http, app_row, redis_down):
    created = await http.post("/templates", headers=AUTH, json={"name": "order", "body": "{{item}} 도착"})
    assert created.status_code == 201
    template_id = created.json()["id"]

    assert (await http.get(f"/templates/{template_id}", headers=AUTH)).status_code == 200
    updated = await http.put(f"/templates/{template_id}", headers=AUTH, json={"name": "order", "body": "{{who}} 도착"})
    assert updated.status_code == 200
    assert (await http.delete(f"/templates/{template_id}", headers=AUTH)).status_code == 204
    assert (await http.get(f"/templates/{template_id}", headers=AUTH)).status_code == 404
