import uuid
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.redis import get_redis
from app.main import app
from app.models.notification_template import NotificationTemplate
from app.schemas.template import TemplateIn
from app.services.auth import get_current_app

VALID = {"name": "order", "title": "{{name}}님", "body": "{{item}} 주문이 접수되었습니다."}


def make_row(**overrides):
    values = dict(
        id=uuid.uuid4(),
        name="order",
        title="{{name}}님",
        body="{{item}}",
        placeholders=["{{name}}", "{{item}}"],
        ref_count=0,
        is_deleted=False,
    )
    return NotificationTemplate(**{**values, **overrides})


@pytest.fixture
def db():
    session = MagicMock()
    session.get = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
async def client(db, fake_redis):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_current_app] = lambda: object()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_create_returns_201_with_placeholders(client, db):
    res = await client.post("/templates", json=VALID)

    assert res.status_code == 201
    assert res.json()["placeholders"] == ["{{name}}", "{{item}}"]
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID, "title": "x" * 201},
        {**VALID, "body": "x" * 10_001},
        {**VALID, "body": "{{ spaced }}"},
        {**VALID, "body": "{{a.b}}"},
        {**VALID, "body": "{{}}"},
        {**VALID, "title": None, "body": " ".join(f"{{{{p{i}}}}}" for i in range(51))},
    ],
)
async def test_create_rejects_invalid_payload(client, payload):
    res = await client.post("/templates", json=payload)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_create_accepts_limits(client):
    body = " ".join(f"{{{{p{i}}}}}" for i in range(50))
    res = await client.post("/templates", json={**VALID, "title": "t" * 200, "body": body})

    assert res.status_code == 201


async def test_create_duplicate_name_returns_409(client, db):
    db.commit.side_effect = IntegrityError("insert", {}, Exception())

    res = await client.post("/templates", json=VALID)

    assert res.status_code == 409
    db.rollback.assert_awaited_once()


async def test_get_uses_cache_after_first_read(client, db):
    row = make_row()
    db.get.return_value = row

    first = await client.request("GET", f"/templates/{row.id}")
    second = await client.request("GET", f"/templates/{row.id}")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert db.get.await_count == 1


async def test_get_unknown_returns_404(client, db):
    db.get.return_value = None

    res = await client.request("GET", f"/templates/{uuid.uuid4()}")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


async def test_put_updates_row_and_invalidates_cache(client, db, fake_redis):
    row = make_row()
    db.get.return_value = row
    await client.request("GET", f"/templates/{row.id}")
    assert await fake_redis.exists(f"template:{row.id}") == 1

    res = await client.put(f"/templates/{row.id}", json={"name": "new", "body": "안녕 {{who}}"})

    assert res.status_code == 200
    assert row.body == "안녕 {{who}}"
    assert row.placeholders == ["{{who}}"]
    assert await fake_redis.exists(f"template:{row.id}") == 0


async def test_put_unknown_returns_404(client, db):
    db.get.return_value = None

    res = await client.put(f"/templates/{uuid.uuid4()}", json=VALID)

    assert res.status_code == 404


async def test_delete_soft_deletes_and_invalidates_cache(client, db, fake_redis):
    row = make_row()
    db.get.return_value = row
    await fake_redis.set(f"template:{row.id}", "{}")

    res = await client.delete(f"/templates/{row.id}")

    assert res.status_code == 204
    assert row.is_deleted is True
    assert await fake_redis.exists(f"template:{row.id}") == 0


async def test_delete_referenced_template_returns_409_with_count(client, db):
    row = make_row(ref_count=3)
    db.get.return_value = row

    res = await client.delete(f"/templates/{row.id}")

    assert res.status_code == 409
    assert "3" in res.json()["error"]["message"]
    assert row.is_deleted is False


async def test_deleted_template_is_not_found(client, db):
    db.get.return_value = make_row(is_deleted=True)

    res = await client.delete(f"/templates/{uuid.uuid4()}")

    assert res.status_code == 404


async def test_unauthenticated_request_returns_401_in_standard_format():
    transport = httpx.ASGITransport(app=app)
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            res = await c.post("/templates", json={**VALID, "app_key": "k", "app_secret": "s"})
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_extra_credential_fields_are_ignored_by_schema():
    TemplateIn(**VALID, app_key="k", app_secret="s")


@pytest.fixture
def redis_down():
    app.dependency_overrides[get_redis] = lambda: fakeredis.FakeAsyncRedis(connected=False, decode_responses=True)


async def test_read_falls_back_to_db_when_redis_is_down(client, db, redis_down):
    row = make_row()
    db.get.return_value = row

    res = await client.get(f"/templates/{row.id}")

    assert res.status_code == 200
    assert res.json()["name"] == "order"


async def test_read_missing_template_is_404_when_redis_is_down(client, db, redis_down):
    db.get.return_value = None

    res = await client.get(f"/templates/{uuid.uuid4()}")

    assert res.status_code == 404
