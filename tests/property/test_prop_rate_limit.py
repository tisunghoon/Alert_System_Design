from unittest.mock import patch

import fakeredis
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import settings as app_settings
from app.services import rate_limiter


async def _results(client, user_id, channel, requests):
    return [
        await rate_limiter.check_rate_limit(user_id, channel, client=client)
        for _ in range(requests)
    ]


@settings(max_examples=30, deadline=None)
@given(extra=st.integers(min_value=1, max_value=120))
async def test_requests_over_default_limit_are_rejected(extra):
    """Feature: alert-system, Property 4: 기본 한도 이하는 허용, 초과분부터 모두 거부"""
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    limit = app_settings.RATE_LIMIT_DEFAULT
    results = await _results(client, "u1", "sms", limit + extra)
    assert all(results[:limit])
    assert not any(results[limit:])


@settings(max_examples=30, deadline=None)
@given(
    user_limit=st.integers(min_value=1, max_value=100),
    default_limit=st.integers(min_value=1, max_value=100),
)
async def test_user_limit_overrides_global_default(user_limit, default_limit):
    """Feature: alert-system, Property 5: 사용자별 한도가 전역 기본값보다 항상 우선"""
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    await rate_limiter.set_user_limit("u1", "sms", user_limit, client=client)
    with patch.object(app_settings, "RATE_LIMIT_DEFAULT", default_limit):
        results = await _results(client, "u1", "sms", user_limit + 1)
    assert all(results[:user_limit])
    assert results[user_limit] is False
