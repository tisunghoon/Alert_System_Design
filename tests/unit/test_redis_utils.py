
from app.core import redis as r


async def test_channel_stream_mapping():
    assert r.CHANNEL_STREAMS == {
        "ios": "ios_stream",
        "android": "android_stream",
        "sms": "sms_stream",
        "email": "email_stream",
    }
    assert r.RETRY_STREAM == "retry_stream"
    assert r.DEAD_LETTER_STREAM == "dead_letter_stream"


async def test_group_init_is_idempotent(fake_redis):
    await r.init_consumer_group(fake_redis, r.IOS_STREAM)
    await r.init_consumer_group(fake_redis, r.IOS_STREAM)
    groups = await fake_redis.xinfo_groups(r.IOS_STREAM)
    assert [g["name"] for g in groups] == [r.CONSUMER_GROUP]


async def test_init_all_creates_every_stream(fake_redis):
    await r.init_all_consumer_groups(fake_redis)
    for stream in r.ALL_STREAMS:
        assert await r.get_stream_length(fake_redis, stream) == 0
        assert len(await fake_redis.xinfo_groups(stream)) == 1


async def test_add_read_ack_length(fake_redis):
    await r.init_consumer_group(fake_redis, r.SMS_STREAM)
    msg_id = await r.xadd_notification(
        fake_redis, r.SMS_STREAM, {"event_id": "evt_1", "retry_count": 0}
    )
    assert await r.get_stream_length(fake_redis, r.SMS_STREAM) == 1

    messages = await r.xreadgroup_messages(fake_redis, r.SMS_STREAM, "c1", block_ms=10)
    assert messages == [(msg_id, {"event_id": "evt_1", "retry_count": "0"})]
    assert await r.xreadgroup_messages(fake_redis, r.SMS_STREAM, "c1", block_ms=10) == []

    assert (await fake_redis.xpending(r.SMS_STREAM, r.CONSUMER_GROUP))["pending"] == 1
    assert await r.xack_message(fake_redis, r.SMS_STREAM, msg_id) == 1
    assert (await fake_redis.xpending(r.SMS_STREAM, r.CONSUMER_GROUP))["pending"] == 0


async def test_incr_with_ttl_sets_ttl_only_once(fake_redis):
    assert await r.incr_with_ttl(fake_redis, "rate:u1:sms", 60) == 1
    assert 0 < await fake_redis.ttl("rate:u1:sms") <= 60

    await fake_redis.expire("rate:u1:sms", 30)
    assert await r.incr_with_ttl(fake_redis, "rate:u1:sms", 60) == 2
    assert await fake_redis.ttl("rate:u1:sms") <= 30


async def test_incr_with_ttl_restores_ttl_on_ttlless_key(fake_redis):
    await fake_redis.set("rate:u2:sms", 5)
    assert await r.incr_with_ttl(fake_redis, "rate:u2:sms", 60) == 6
    assert await fake_redis.ttl("rate:u2:sms") > 0


async def test_cache_json_roundtrip_and_delete(fake_redis):
    value = {"token": "abc", "devices": [1, 2], "name": "홍길동"}
    assert await r.get_cache(fake_redis, "device:u1") is None

    await r.set_cache(fake_redis, "device:u1", value, 300)
    assert await r.get_cache(fake_redis, "device:u1") == value
    assert 0 < await fake_redis.ttl("device:u1") <= 300

    assert await r.delete_cache(fake_redis, "device:u1") == 1
    assert await r.get_cache(fake_redis, "device:u1") is None


async def test_get_redis_returns_singleton_and_close_resets():
    first = r.get_redis()
    assert r.get_redis() is first
    await r.close_redis()
    assert r.get_redis() is not first
    await r.close_redis()
