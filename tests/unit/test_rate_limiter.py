import logging

import fakeredis
import pytest
from redis.exceptions import ConnectionError

from app.core.config import settings
from app.services import rate_limiter


@pytest.fixture
async def client():
    c = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield c
    await c.aclose()


async def test_default_limit_blocks_after_limit(client):
    for _ in range(settings.RATE_LIMIT_DEFAULT):
        assert await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert not await rate_limiter.check_rate_limit("u1", "sms", client=client)


async def test_counters_are_per_user_and_channel(client):
    await rate_limiter.set_user_limit("u1", "sms", 1, client=client)
    assert await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert not await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert await rate_limiter.check_rate_limit("u1", "email", client=client)
    assert await rate_limiter.check_rate_limit("u2", "sms", client=client)


async def test_counter_has_ttl(client):
    await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert 0 < await client.ttl("rate:u1:sms") <= rate_limiter.WINDOW_SECONDS


async def test_allows_and_warns_when_redis_down(caplog):
    class Down:
        async def get(self, *_):
            raise ConnectionError("down")

    with caplog.at_level(logging.WARNING):
        assert await rate_limiter.check_rate_limit("u1", "sms", client=Down())
    assert "Rate Limit" in caplog.text


@pytest.mark.parametrize("bad", [0, -1, 1.5, "10", True, None])
async def test_set_user_limit_rejects_invalid(client, bad):
    with pytest.raises(ValueError):
        await rate_limiter.set_user_limit("u1", "sms", bad, client=client)
    assert await client.get("rate_limit_cfg:u1:sms") is None


async def test_corrupt_stored_limit_falls_back_to_default(client, caplog):
    await client.set("rate_limit_cfg:u1:sms", "abc")
    with caplog.at_level(logging.WARNING):
        for _ in range(settings.RATE_LIMIT_DEFAULT):
            assert await rate_limiter.check_rate_limit("u1", "sms", client=client)
        assert not await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert "잘못된 Rate Limit 설정" in caplog.text


async def test_ttl_is_not_extended_by_later_requests(client):
    await rate_limiter.check_rate_limit("u1", "sms", client=client)
    await client.expire("rate:u1:sms", 10)
    await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert await client.ttl("rate:u1:sms") <= 10


async def test_allows_and_warns_when_incr_fails(client, monkeypatch, caplog):
    async def boom(*_):
        raise ConnectionError("down")

    monkeypatch.setattr(rate_limiter, "incr_with_ttl", boom)
    with caplog.at_level(logging.WARNING):
        assert await rate_limiter.check_rate_limit("u1", "sms", client=client)
    assert "Rate Limit" in caplog.text
