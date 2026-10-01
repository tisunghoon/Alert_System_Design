import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api import notifications
from app.core.database import get_db
from app.core.redis import CHANNEL_STREAMS
from app.core.errors import register_exception_handlers
from app.models import App, Notification, NotificationStatusHistory, NotificationTemplate
from app.services import rate_limiter

APP = App(id=uuid.uuid4(), app_key="key", app_secret="secret", name="svc", is_active=True)
CREDS = {"app_key": "key", "app_secret": "secret"}


class FakeDB:
    def __init__(self):
        self.added = []
        self.add = self.added.append
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.get = AsyncMock(return_value=None)
        self.delivered_exists = False
        self.preference_enabled = None
        self.notification = None
        self.lookup_error = None

    async def execute(self, stmt):
        sql = str(stmt)
        if self.lookup_error and "FROM apps" not in sql:
            raise self.lookup_error
        result = MagicMock()
        if "FROM apps" in sql:
            result.scalar_one_or_none.return_value = APP
        elif "FROM notifications" in sql and "history" not in sql.split("FROM")[0]:
            result.first.return_value = (1,) if self.delivered_exists else None
            result.scalar_one_or_none.return_value = self.notification
        else:
            result.scalar_one_or_none.return_value = self.preference_enabled
        return result


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def client(db, redis):
    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(notifications.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[notifications.redis_client] = lambda: redis
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test")


def payload(**overrides):
    return {**CREDS, "event_id": "evt-1", "channel": "ios", "recipient_id": "u1", "title": "t", "body": "b", **overrides}


async def stream_entries(redis, channel="ios"):
    return await redis.xrange(CHANNEL_STREAMS[channel])


async def test_post_queues_notification(client, db, redis):
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 202
    body = resp.json()
    assert (body["event_id"], body["status"]) == ("evt-1", "queued")
    assert body["queued_at"]
    [log] = [o for o in db.added if isinstance(o, Notification)]
    assert (log.status, log.channel, log.recipient_id) == ("QUEUED", "ios", "u1")
    db.commit.assert_awaited()
    [(_, fields)] = await stream_entries(redis)
    assert fields["event_id"] == "evt-1" and fields["body"] == "b" and fields["retry_count"] == "0"
    assert await redis.exists("dedup:evt-1")


async def test_post_generates_event_id(client, redis):
    resp = await client.post("/notifications", json={k: v for k, v in payload().items() if k != "event_id"})
    assert resp.status_code == 202
    assert resp.json()["event_id"].startswith("evt_")


@pytest.mark.parametrize("channel", ["ios", "android", "sms", "email"])
async def test_post_uses_channel_stream(client, redis, channel):
    await client.post("/notifications", json=payload(channel=channel))
    assert len(await stream_entries(redis, channel)) == 1


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"channel": "push"}, "channel"),
        ({"channel": None}, "channel"),
        ({"recipient_id": ""}, "recipient_id"),
        ({"recipient_id": "r" * 129}, "recipient_id"),
        ({"title": ""}, "title"),
        ({"title": "t" * 257}, "title"),
        ({"body": ""}, "body"),
        ({"body": "b" * 4097}, "body"),
    ],
)
async def test_post_invalid_field_returns_400(client, db, redis, override, field):
    resp = await client.post("/notifications", json=payload(**override))
    assert resp.status_code == 400
    assert field in [d["field"] for d in resp.json()["error"]["details"]]
    assert db.added == []
    assert await stream_entries(redis) == []


async def test_post_missing_required_fields_returns_400(client):
    data = {k: v for k, v in payload().items() if k not in ("recipient_id", "body")}
    resp = await client.post("/notifications", json=data)
    assert resp.status_code == 400
    assert {d["field"] for d in resp.json()["error"]["details"]} == {"recipient_id", "body"}


async def test_post_unauthenticated_returns_401(client, db):
    resp = await client.post("/notifications", json={"channel": "ios", "recipient_id": "u1", "body": "b"})
    assert resp.status_code == 401
    assert db.added == []


async def test_post_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(rate_limiter.settings, "RATE_LIMIT_DEFAULT", 2)
    codes = [(await client.post("/notifications", json=payload(event_id=f"e{i}"))).status_code for i in range(3)]
    assert codes == [202, 202, 429]
    resp = await client.post("/notifications", json=payload(event_id="e9"))
    assert resp.headers["retry-after"] == "60"


async def test_post_duplicate_from_cache_returns_409(client, db):
    assert (await client.post("/notifications", json=payload())).status_code == 202
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 409
    assert len(db.added) == 2


async def test_post_duplicate_with_empty_cache_returns_409_from_db(client, db, redis):
    assert (await client.post("/notifications", json=payload())).status_code == 202
    await redis.delete("dedup:evt-1")
    db.flush.side_effect = IntegrityError("insert", {}, Exception("uq_notifications_event_id"))
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 409
    db.rollback.assert_awaited()
    assert len(await stream_entries(redis)) == 1


async def test_post_redis_down_falls_back_to_db_duplicate_check(db):
    down = fakeredis.FakeAsyncRedis(connected=False, decode_responses=True)
    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(notifications.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[notifications.redis_client] = lambda: down
    db.delivered_exists = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        resp = await c.post("/notifications", json=payload())
    assert resp.status_code == 409


async def test_post_redis_down_returns_503_but_keeps_queued_log(db):
    down = fakeredis.FakeAsyncRedis(connected=False, decode_responses=True)
    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(notifications.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[notifications.redis_client] = lambda: down
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        resp = await c.post("/notifications", json=payload())
    assert resp.status_code == 503
    assert [o for o in db.added if isinstance(o, Notification)]
    db.commit.assert_awaited()


async def test_post_queue_failure_returns_503_and_keeps_log(client, db, monkeypatch):
    monkeypatch.setattr(notifications, "xadd_notification", AsyncMock(side_effect=RedisError("down")))
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 503
    assert [o for o in db.added if isinstance(o, Notification)]
    db.commit.assert_awaited()


async def test_post_queue_timeout_returns_503(client, monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(notifications, "xadd_notification", hang)
    monkeypatch.setattr(notifications, "QUEUE_TIMEOUT_SECONDS", 0.01)
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 503


async def test_post_log_failure_returns_error_and_skips_queue(client, db, redis):
    db.commit.side_effect = OperationalError("commit", {}, Exception("db down"))
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 503
    assert await stream_entries(redis) == []
    assert not await redis.exists("dedup:evt-1")


async def test_post_disabled_channel_is_skipped(client, db, redis):
    db.preference_enabled = False
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 200
    assert resp.json() == {"event_id": "evt-1", "status": "skipped", "queued_at": None, "reason": "channel_disabled"}
    assert db.added == []
    assert await stream_entries(redis) == []


async def test_post_enabled_preference_is_queued(client, db):
    db.preference_enabled = True
    assert (await client.post("/notifications", json=payload())).status_code == 202


def seed_template(redis_client, title="안녕 {{name}}", body="{{item}} 도착"):
    template_id = uuid.uuid4()
    return template_id, redis_client.set(
        f"template:{template_id}", json.dumps({"id": str(template_id), "name": "n", "title": title, "body": body})
    )


async def test_post_renders_template(client, db, redis):
    template_id, seeded = seed_template(redis)
    await seeded
    data = {k: v for k, v in payload().items() if k not in ("title", "body")}
    resp = await client.post(
        "/notifications",
        json={**data, "template_id": str(template_id), "template_variables": {"name": "민수", "item": "택배"}},
    )
    assert resp.status_code == 202
    [(_, fields)] = await stream_entries(redis)
    assert (fields["title"], fields["body"]) == ("안녕 민수", "택배 도착")


async def test_post_template_variable_mismatch_returns_400(client, db, redis):
    template_id, seeded = seed_template(redis)
    await seeded
    data = {k: v for k, v in payload().items() if k not in ("title", "body")}
    resp = await client.post(
        "/notifications", json={**data, "template_id": str(template_id), "template_variables": {"name": "민수"}}
    )
    assert resp.status_code == 400
    assert db.added == []


async def test_post_unknown_template_returns_404(client, db):
    data = {k: v for k, v in payload().items() if k not in ("title", "body")}
    resp = await client.post("/notifications", json={**data, "template_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def make_notification(app_id=APP.id) -> Notification:
    queued = datetime.now(UTC) - timedelta(seconds=3)
    n = Notification(
        id=uuid.uuid4(), event_id="evt-1", app_id=app_id, channel="ios", recipient_id="u1", title="t", body="b",
        template_id=None, status="DELIVERED", retry_count=1, total_duration_ms=900, queued_at=queued,
        delivered_at=queued + timedelta(milliseconds=900), failed_at=None,
    )
    n.history = [
        NotificationStatusHistory(status="QUEUED", worker_id=None, changed_at=queued, note=None),
        NotificationStatusHistory(status="DELIVERED", worker_id="w1", changed_at=queued + timedelta(seconds=1), note=None),
    ]
    return n


HEADERS = {"X-App-Key": "key", "X-App-Secret": "secret"}


async def test_get_returns_status_and_history(client, db):
    db.notification = make_notification()
    resp = await client.get("/notifications/evt-1", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert (body["status"], body["channel"], body["recipient_id"], body["body"]) == ("DELIVERED", "ios", "u1", "b")
    assert [h["status"] for h in body["history"]] == ["QUEUED", "DELIVERED"]
    assert body["history"][1]["worker_id"] == "w1"


async def test_get_unknown_event_returns_404(client):
    resp = await client.get("/notifications/missing", headers=HEADERS)
    assert resp.status_code == 404


async def test_get_other_apps_notification_returns_404(client, db):
    db.notification = make_notification(app_id=uuid.uuid4())
    resp = await client.get("/notifications/evt-1", headers=HEADERS)
    assert resp.status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-App-Key": "key"}, {"X-App-Key": "key", "X-App-Secret": "wrong"}])
async def test_get_requires_credentials(client, db, headers):
    db.notification = make_notification()
    resp = await client.get("/notifications/evt-1", headers=headers)
    assert resp.status_code == 401


async def test_error_body_uses_standard_format(client):
    resp = await client.post("/notifications", json=payload(channel="push"))
    error = resp.json()["error"]
    assert (error["code"], error["details"][0]["field"]) == ("VALIDATION_ERROR", "channel")
    assert resp.json()["request_id"].startswith("req_")


async def test_post_template_lookup_falls_back_to_db_when_redis_down(db):
    template_id = uuid.uuid4()
    db.get = AsyncMock(
        return_value=NotificationTemplate(
            id=template_id, name="n", title=None, body="{{item}} 도착", placeholders=[], is_deleted=False
        )
    )
    down = fakeredis.FakeAsyncRedis(connected=False, decode_responses=True)
    sent = []

    async def capture(client, stream, fields):
        sent.append(fields)

    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(notifications.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[notifications.redis_client] = lambda: down
    data = {k: v for k, v in payload().items() if k not in ("title", "body")}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(notifications, "xadd_notification", capture)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
            resp = await c.post(
                "/notifications",
                json={**data, "template_id": str(template_id), "template_variables": {"item": "택배"}},
            )
    assert resp.status_code == 202
    assert sent[0]["body"] == "택배 도착"


async def test_post_deleted_template_returns_404_when_redis_down(db):
    db.get = AsyncMock(
        return_value=NotificationTemplate(id=uuid.uuid4(), name="n", title=None, body="x", placeholders=[], is_deleted=True)
    )
    down = fakeredis.FakeAsyncRedis(connected=False, decode_responses=True)
    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(notifications.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[notifications.redis_client] = lambda: down
    data = {k: v for k, v in payload().items() if k not in ("title", "body")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as c:
        resp = await c.post("/notifications", json={**data, "template_id": str(uuid.uuid4())})
    assert resp.status_code == 404


async def test_post_db_error_during_lookup_returns_503(client, db):
    db.lookup_error = OperationalError("select", {}, Exception("down"))
    resp = await client.post("/notifications", json=payload())
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DB_UNAVAILABLE"


@pytest.mark.parametrize("content", [b"[1]", b"not json", b"", b"\"text\"", b"null"])
async def test_post_non_object_body_with_header_auth_returns_400(client, db, content):
    resp = await client.post("/notifications", content=content, headers=HEADERS)
    assert resp.status_code == 400
    assert resp.json()["error"]["details"][0]["field"] == "body"
    assert db.added == []
