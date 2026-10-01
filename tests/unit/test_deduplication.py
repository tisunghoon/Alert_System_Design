import logging
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from redis.exceptions import ConnectionError

from app.services import deduplication


@pytest.fixture
async def client():
    c = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield c
    await c.aclose()


def make_db(found: bool):
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = ("id",) if found else None
    db.execute.return_value = result
    return db


class DownRedis:
    async def exists(self, *_):
        raise ConnectionError("down")


async def test_cache_hit_skips_db(client):
    await deduplication.mark_processed("evt_1", client=client)
    db = make_db(False)
    assert await deduplication.check_duplicate("evt_1", db, client=client)
    db.execute.assert_not_awaited()


async def test_mark_processed_sets_24h_ttl(client):
    await deduplication.mark_processed("evt_1", client=client)
    assert 0 < await client.ttl("dedup:evt_1") <= 24 * 60 * 60


async def test_cache_miss_falls_back_to_db(client):
    assert await deduplication.check_duplicate("evt_1", make_db(True), client=client)
    assert not await deduplication.check_duplicate("evt_1", make_db(False), client=client)


async def test_redis_down_falls_back_to_db_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert await deduplication.check_duplicate("evt_1", make_db(True), client=DownRedis())
        assert not await deduplication.check_duplicate("evt_1", make_db(False), client=DownRedis())
    assert "중복 검사" in caplog.text
