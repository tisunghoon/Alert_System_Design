from unittest.mock import AsyncMock, patch

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

from app.api import monitoring
from app.core.database import get_db
from app.core.redis import get_redis
from app.main import app


@settings(max_examples=16, deadline=None)
@given(db_ok=st.booleans(), cache_ok=st.booleans(), mq_ok=st.booleans())
async def test_health_reports_all_components(db_ok, cache_ok, mq_ok):
    """Feature: alert-system, Property 15: 응답은 세 구성 요소 상태를 모두 담고, 하나라도 비정상이면 503"""
    async def check(ok):
        if not ok:
            raise ConnectionError("down")

    async def check_db(_db):
        await check(db_ok)

    async def check_cache(_client):
        await check(cache_ok)

    async def check_queue(_client):
        await check(mq_ok)

    with (
        patch.object(monitoring, "_check_db", new=check_db),
        patch.object(monitoring, "_check_cache", new=check_cache),
        patch.object(monitoring, "_check_queue", new=check_queue),
    ):
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_redis] = lambda: AsyncMock()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

    body = resp.json()
    states = {"database": db_ok, "cache": cache_ok, "message_queue": mq_ok}
    assert set(body["components"]) == set(states)
    for name, ok in states.items():
        assert body["components"][name] == ("healthy" if ok else "unhealthy")
    if all(states.values()):
        assert resp.status_code == 200
    else:
        assert resp.status_code == 503
        assert set(body["unhealthy"]) == {n for n, ok in states.items() if not ok}
