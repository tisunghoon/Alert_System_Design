import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import httpx
from fastapi import FastAPI
from hypothesis import given, settings
from hypothesis import strategies as st

from app.api import notifications
from app.core.database import get_db
from app.models import App

REGISTERED = App(id=uuid.uuid4(), app_key="valid-key", app_secret="valid-secret", name="svc", is_active=True)
CHANNELS = ["ios", "android", "sms", "email"]


class FakeDB:
    def __init__(self):
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, stmt):
        result = MagicMock()
        result.first.return_value = None
        if "FROM apps" in str(stmt):
            key = next(iter(stmt.compile().params.values()))
            result.scalar_one_or_none.return_value = REGISTERED if key == REGISTERED.app_key else None
        else:
            result.scalar_one_or_none.return_value = None
        return result


async def post(body: dict) -> httpx.Response:
    api = FastAPI()
    api.include_router(notifications.router)
    api.dependency_overrides[get_db] = lambda: FakeDB()
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    api.dependency_overrides[notifications.redis_client] = lambda: redis
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        return await client.post("/notifications", json=body)


def valid_body(**overrides) -> dict:
    return {
        "app_key": REGISTERED.app_key,
        "app_secret": REGISTERED.app_secret,
        "channel": "ios",
        "recipient_id": "u1",
        "title": "t",
        "body": "b",
        **overrides,
    }


credential = st.one_of(st.just(""), st.text(max_size=300), st.sampled_from([REGISTERED.app_key, REGISTERED.app_secret]))


@settings(max_examples=100, deadline=None)
@given(app_key=credential | st.none(), app_secret=credential | st.none())
def test_property_1_only_valid_credentials_are_processed(app_key, app_secret):
    """Feature: alert-system, Property 1: 유효한 인증 요청만 처리됨"""
    body = valid_body()
    for name, value in (("app_key", app_key), ("app_secret", app_secret)):
        if value is None:
            body.pop(name)
        else:
            body[name] = value

    resp = asyncio.run(post(body))

    is_valid = (body.get("app_key"), body.get("app_secret")) == (REGISTERED.app_key, REGISTERED.app_secret)
    assert resp.status_code == (202 if is_valid else 401)


def sized(limit: int):
    return st.integers(min_value=0, max_value=limit + 5).map(lambda n: "x" * n)


@settings(max_examples=100, deadline=None)
@given(
    channel=st.sampled_from(CHANNELS) | st.text(max_size=10),
    recipient_id=sized(128),
    title=st.none() | sized(256),
    body=sized(4096),
)
def test_property_2_invalid_fields_return_400_with_field_names(channel, recipient_id, title, body):
    """Feature: alert-system, Property 2: 알림 요청 필드 유효성 검증"""
    resp = asyncio.run(
        post(valid_body(channel=channel, recipient_id=recipient_id, title=title, body=body))
    )

    expected_invalid = set()
    if channel not in CHANNELS:
        expected_invalid.add("channel")
    if not 1 <= len(recipient_id) <= 128:
        expected_invalid.add("recipient_id")
    if title is not None and not 1 <= len(title) <= 256:
        expected_invalid.add("title")
    if not 1 <= len(body) <= 4096:
        expected_invalid.add("body")

    if not expected_invalid:
        assert resp.status_code == 202
    else:
        assert resp.status_code == 400
        assert {d["field"] for d in resp.json()["detail"]["details"]} == expected_invalid
