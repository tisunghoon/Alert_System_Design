import fakeredis
import pytest

from app.core import redis as r


@pytest.fixture
async def client():
    c = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield c
    await c.aclose()


async def test_channel_stream_mapping():
    assert r.CHANNEL_STREAMS == {
        "ios": "ios_stream",
        "android": "android_stream",
        "sms": "sms_stream",
        "email": "email_stream",
    }
    assert r.RETRY_STREAM == "retry_stream"
    assert r.DEAD_LETTER_STREAM == "dead_letter_stream"


async def test_group_init_is_idempotent(client):
    await r.init_consumer_group(client, r.IOS_STREAM)
    await r.init_consumer_group(client, r.IOS_STREAM)
    groups = await client.xinfo_groups(r.IOS_STREAM)
    assert [g["name"] for g in groups] == [r.CONSUMER_GROUP]


async def test_init_all_creates_every_stream(client):
    await r.init_all_consumer_groups(client)
    for stream in r.ALL_STREAMS:
        assert await r.get_stream_length(client, stream) == 0
        assert len(await client.xinfo_groups(stream)) == 1


async def test_add_read_ack_length(client):
    await r.init_consumer_group(client, r.SMS_STREAM)
    msg_id = await r.xadd_notification(
        client, r.SMS_STREAM, {"event_id": "evt_1", "retry_count": 0}
    )
    assert await r.get_stream_length(client, r.SMS_STREAM) == 1

    messages = await r.xreadgroup_messages(client, r.SMS_STREAM, "c1", block_ms=10)
    assert messages == [(msg_id, {"event_id": "evt_1", "retry_count": "0"})]
    assert await r.xreadgroup_messages(client, r.SMS_STREAM, "c1", block_ms=10) == []

    assert (await client.xpending(r.SMS_STREAM, r.CONSUMER_GROUP))["pending"] == 1
    assert await r.xack_message(client, r.SMS_STREAM, msg_id) == 1
    assert (await client.xpending(r.SMS_STREAM, r.CONSUMER_GROUP))["pending"] == 0


async def test_incr_with_ttl_sets_ttl_only_once(client):
    assert await r.incr_with_ttl(client, "rate:u1:sms", 60) == 1
    assert 0 < await client.ttl("rate:u1:sms") <= 60

    await client.expire("rate:u1:sms", 30)
    assert await r.incr_with_ttl(client, "rate:u1:sms", 60) == 2
    assert await client.ttl("rate:u1:sms") <= 30


async def test_incr_with_ttl_restores_ttl_on_ttlless_key(client):
    await client.set("rate:u2:sms", 5)
    assert await r.incr_with_ttl(client, "rate:u2:sms", 60) == 6
    assert await client.ttl("rate:u2:sms") > 0


async def test_cache_json_roundtrip_and_delete(client):
    value = {"token": "abc", "devices": [1, 2], "name": "홍길동"}
    assert await r.get_cache(client, "device:u1") is None

    await r.set_cache(client, "device:u1", value, 300)
    assert await r.get_cache(client, "device:u1") == value
    assert 0 < await client.ttl("device:u1") <= 300

    assert await r.delete_cache(client, "device:u1") == 1
    assert await r.get_cache(client, "device:u1") is None


async def test_get_redis_returns_singleton_and_close_resets():
    first = r.get_redis()
    assert r.get_redis() is first
    await r.close_redis()
    assert r.get_redis() is not first
    await r.close_redis()
