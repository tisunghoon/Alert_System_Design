from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, Depends

from app.core.database import get_db
from app.models import App
from app.services import auth
from app.services.auth import authenticate_app, get_current_app
from tests.support.http import make_client


def make_db(app: App | None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = app
    db.execute.return_value = result
    return db


def make_app(is_active: bool = True) -> App:
    return App(app_key="key", app_secret="secret", name="svc", is_active=is_active)


async def test_valid_credentials_return_app():
    app = make_app()
    assert await authenticate_app("key", "secret", make_db(app)) is app


async def test_wrong_secret_rejected():
    assert await authenticate_app("key", "wrong", make_db(make_app())) is None


async def test_unknown_key_rejected():
    assert await authenticate_app("nope", "secret", make_db(None)) is None


async def test_inactive_app_rejected():
    assert await authenticate_app("key", "secret", make_db(make_app(is_active=False))) is None


@pytest.mark.parametrize(
    ("key", "secret"),
    [("", "secret"), ("key", ""), (None, "secret"), ("key", None), ("k" * 257, "secret"), ("key", "s" * 257)],
)
async def test_invalid_credential_shape_skips_db(key, secret):
    db = make_db(make_app())
    assert await authenticate_app(key, secret, db) is None
    db.execute.assert_not_awaited()


async def test_max_length_credentials_accepted():
    app = App(app_key="k" * 256, app_secret="s" * 256, name="svc", is_active=True)
    assert await authenticate_app("k" * 256, "s" * 256, make_db(app)) is app


@pytest.fixture
def client_factory():
    def build(app: App | None):
        router = APIRouter()

        @router.post("/protected")
        async def protected(current: App = Depends(get_current_app)):
            return {"name": current.name}

        return make_client(router, overrides={get_db: lambda: make_db(app)})

    return build


async def test_dependency_allows_valid_body(client_factory):
    async with client_factory(make_app()) as client:
        resp = await client.post("/protected", json={"app_key": "key", "app_secret": "secret"})
    assert resp.status_code == 200
    assert resp.json() == {"name": "svc"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"app_key": "key"},
        {"app_secret": "secret"},
        {"app_key": "", "app_secret": ""},
        {"app_key": "key", "app_secret": "wrong"},
        [],
    ],
)
async def test_dependency_returns_401(client_factory, payload):
    app = make_app()
    async with client_factory(app) as client:
        resp = await client.post("/protected", json=payload)
    assert resp.status_code == 401


async def test_dependency_returns_401_for_non_json_body(client_factory):
    async with client_factory(make_app()) as client:
        resp = await client.post("/protected", content=b"not json")
    assert resp.status_code == 401


async def test_dependency_returns_401_for_unknown_app(client_factory):
    async with client_factory(None) as client:
        resp = await client.post("/protected", json={"app_key": "key", "app_secret": "secret"})
    assert resp.status_code == 401


async def test_dependency_accepts_headers_without_body(client_factory):
    async with client_factory(make_app()) as client:
        resp = await client.post("/protected", headers={"X-App-Key": "key", "X-App-Secret": "secret"})
    assert resp.status_code == 200


async def test_dependency_prefers_body_over_headers(client_factory):
    async with client_factory(make_app()) as client:
        resp = await client.post(
            "/protected",
            json={"app_key": "key", "app_secret": "wrong"},
            headers={"X-App-Key": "key", "X-App-Secret": "secret"},
        )
    assert resp.status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-App-Key": "key"}, {"X-App-Secret": "secret"}, {"X-App-Key": "", "X-App-Secret": ""}])
async def test_dependency_rejects_missing_headers(client_factory, headers):
    async with client_factory(make_app()) as client:
        resp = await client.post("/protected", headers=headers)
    assert resp.status_code == 401


async def test_secret_is_compared_with_compare_digest(monkeypatch):
    calls = []
    real = auth.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    assert await authenticate_app("key", "secret", make_db(make_app())) is not None
    assert calls == [(b"secret", b"secret")]
    assert await authenticate_app("key", "wrong", make_db(make_app())) is None
    assert calls[-1] == (b"secret", b"wrong")
