import logging

from redis.exceptions import ConnectionError

from app.services import deduplication
from tests.support.db_fakes import make_db


class DownRedis:
    async def exists(self, *_):
        raise ConnectionError("down")


async def test_cache_hit_skips_db(fake_redis):
    await deduplication.mark_processed("evt_1", client=fake_redis)
    db = make_db(False)
    assert await deduplication.check_duplicate("evt_1", db, client=fake_redis)
    db.execute.assert_not_awaited()


async def test_mark_processed_sets_24h_ttl(fake_redis):
    await deduplication.mark_processed("evt_1", client=fake_redis)
    assert 0 < await fake_redis.ttl("dedup:evt_1") <= 24 * 60 * 60


async def test_cache_miss_falls_back_to_db(fake_redis):
    assert await deduplication.check_duplicate("evt_1", make_db(True), client=fake_redis)
    assert not await deduplication.check_duplicate("evt_1", make_db(False), client=fake_redis)


async def test_redis_down_falls_back_to_db_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert await deduplication.check_duplicate("evt_1", make_db(True), client=DownRedis())
        assert not await deduplication.check_duplicate("evt_1", make_db(False), client=DownRedis())
    assert "중복 검사" in caplog.text
