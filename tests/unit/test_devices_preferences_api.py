import uuid
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.redis import get_redis
from app.main import app
from app.models import Device, User, UserPreference
from app.services.auth import get_current_app

USER = User(id=uuid.uuid4(), external_id="u1")


def result(scalar=None, many=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalar_one.return_value = scalar
    r.scalars.return_value.all.return_value = many or []
    return r


def make_device(**overrides):
    values = dict(id=uuid.uuid4(), user_id=USER.id, channel="ios", token="tok")
    return Device(**{**values, **overrides})


@pytest.fixture
def db():
    session = MagicMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
async def client(db, redis_client):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis_client
    app.dependency_overrides[get_current_app] = lambda: object()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_register_device_returns_201_and_invalidates_cache(client, db, redis_client):
    await redis_client.set("device:u1", "[]")
    db.execute.side_effect = [result(USER), result(0)]

    res = await client.post("/users/u1/devices", json={"channel": "sms", "token": "010-1234"})

    assert res.status_code == 201
    assert res.json()["channel"] == "sms"
    assert await redis_client.exists("device:u1") == 0


async def test_register_creates_missing_user(client, db):
    db.execute.side_effect = [result(None), result(0)]

    res = await client.post("/users/new/devices", json={"channel": "email", "token": "a@b.c"})

    assert res.status_code == 201
    created = [c.args[0] for c in db.add.call_args_list]
    assert any(isinstance(o, User) and o.external_id == "new" for o in created)


@pytest.mark.parametrize("channel", ["push", "", "IOS"])
async def test_register_rejects_invalid_channel(client, channel):
    res = await client.post("/users/u1/devices", json={"channel": channel, "token": "t"})

    assert res.status_code == 400


async def test_register_rejects_empty_token(client):
    res = await client.post("/users/u1/devices", json={"channel": "ios", "token": ""})

    assert res.status_code == 400


async def test_register_allows_tenth_device(client, db):
    db.execute.side_effect = [result(USER), result(9)]

    res = await client.post("/users/u1/devices", json={"channel": "ios", "token": "t"})

    assert res.status_code == 201


async def test_register_rejects_eleventh_device(client, db):
    db.execute.side_effect = [result(USER), result(10)]

    res = await client.post("/users/u1/devices", json={"channel": "ios", "token": "t"})

    assert res.status_code == 400
    db.commit.assert_not_awaited()


async def test_register_duplicate_returns_409(client, db):
    db.execute.side_effect = [result(USER), result(1)]
    db.commit.side_effect = IntegrityError("insert", {}, Exception())

    res = await client.post("/users/u1/devices", json={"channel": "ios", "token": "t"})

    assert res.status_code == 409


async def test_list_devices_cache_aside(client, db):
    device = make_device()
    db.execute.side_effect = [result(USER), result(many=[device])]

    first = await client.get("/users/u1/devices")
    second = await client.get("/users/u1/devices")

    assert first.json() == second.json() == [
        {"id": str(device.id), "channel": "ios", "token": "tok"}
    ]
    assert db.execute.await_count == 2


async def test_list_devices_unknown_user_is_empty(client, db):
    db.execute.side_effect = [result(None)]

    res = await client.get("/users/ghost/devices")

    assert res.status_code == 200
    assert res.json() == []


async def test_delete_device_removes_and_invalidates_cache(client, db, redis_client):
    device = make_device()
    await redis_client.set("device:u1", "[]")
    db.execute.side_effect = [result(USER)]
    db.get.return_value = device

    res = await client.delete(f"/users/u1/devices/{device.id}")

    assert res.status_code == 204
    db.delete.assert_awaited_once_with(device)
    assert await redis_client.exists("device:u1") == 0


async def test_delete_other_users_device_returns_404(client, db):
    db.execute.side_effect = [result(USER)]
    db.get.return_value = make_device(user_id=uuid.uuid4())

    res = await client.delete(f"/users/u1/devices/{uuid.uuid4()}")

    assert res.status_code == 404
    db.delete.assert_not_awaited()


async def test_get_preferences_defaults_to_enabled(client, db):
    db.execute.side_effect = [result(USER), result(many=[])]

    res = await client.get("/users/u1/preferences")

    assert res.json() == {"ios": True, "android": True, "sms": True, "email": True}


async def test_get_preferences_uses_cache(client, db):
    pref = UserPreference(user_id=USER.id, channel="sms", is_enabled=False)
    db.execute.side_effect = [result(USER), result(many=[pref])]

    first = await client.get("/users/u1/preferences")
    second = await client.get("/users/u1/preferences")

    assert first.json() == second.json()
    assert first.json()["sms"] is False
    assert db.execute.await_count == 2


async def test_put_preferences_updates_inserts_and_invalidates_cache(client, db, redis_client):
    existing = UserPreference(user_id=USER.id, channel="sms", is_enabled=True)
    await redis_client.set("pref:u1", "{}")
    db.execute.side_effect = [
        result(USER),
        result(many=[existing]),
        result(USER),
        result(many=[existing]),
    ]

    res = await client.put("/users/u1/preferences", json={"preferences": {"sms": False, "email": False}})

    assert res.status_code == 200
    assert existing.is_enabled is False
    added = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], UserPreference)]
    assert [(p.channel, p.is_enabled) for p in added] == [("email", False)]
    assert res.json()["sms"] is False


@pytest.mark.parametrize("payload", [{}, {"preferences": {}}, {"preferences": {"push": True}}])
async def test_put_preferences_rejects_invalid_payload(client, payload):
    res = await client.put("/users/u1/preferences", json=payload)

    assert res.status_code == 400


async def test_cache_ttls_are_five_minutes(client, db, redis_client):
    db.execute.side_effect = [result(USER), result(many=[]), result(USER), result(many=[])]

    await client.get("/users/u1/devices")
    await client.get("/users/u1/preferences")

    assert await redis_client.ttl("device:u1") == pytest.approx(300, abs=2)
    assert await redis_client.ttl("pref:u1") == pytest.approx(300, abs=2)


async def test_put_preferences_retries_after_concurrent_insert(client, db):
    existing = UserPreference(user_id=USER.id, channel="sms", is_enabled=True)
    db.commit.side_effect = [IntegrityError("insert", {}, Exception()), None]
    db.execute.side_effect = [
        result(USER),
        result(many=[]),
        result(USER),
        result(many=[existing]),
        result(USER),
        result(many=[existing]),
    ]

    res = await client.put("/users/u1/preferences", json={"preferences": {"sms": False}})

    assert res.status_code == 200
    assert existing.is_enabled is False
    assert db.commit.await_count == 2
    db.rollback.assert_awaited_once()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/users/u1/devices", {"channel": "ios", "token": "t"}),
        ("GET", "/users/u1/devices", {}),
        ("GET", "/users/u1/preferences", {}),
        ("PUT", "/users/u1/preferences", {"preferences": {"sms": False}}),
    ],
)
async def test_invalid_credentials_return_standard_401(db, redis_client, method, path, body):
    db.execute.return_value = result(None)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            res = await c.request(method, path, json={**body, "app_key": "k", "app_secret": "s"})
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"
    assert res.json()["request_id"].startswith("req_")
