import pytest

from app.mocks import config_store
from app.mocks.third_party_mock import MOCKS

NOTIFICATION = {"channel": "sms", "recipient_id": "u1", "body": "hi"}


@pytest.fixture(autouse=True)
def clean_mocks():
    yield
    for mock in MOCKS.values():
        mock.reset()


async def test_load_defaults_when_unset(fake_redis):
    assert await config_store.load_config(fake_redis, "sms") == (100, 0)


async def test_save_partial_updates_keep_other_field(fake_redis):
    await config_store.save_config(fake_redis, "sms", success_rate=30)
    await config_store.save_config(fake_redis, "sms", delay_ms=250)
    assert await config_store.load_config(fake_redis, "sms") == (30, 250)
    assert await config_store.load_config(fake_redis, "email") == (100, 0)


async def test_apply_config_sets_mock(fake_redis):
    await config_store.save_config(fake_redis, "sms", success_rate=0, delay_ms=5)
    await config_store.apply_config(fake_redis, "sms", MOCKS["sms"])
    assert (MOCKS["sms"].success_rate, MOCKS["sms"].delay_ms) == (0, 5)


async def test_send_uses_shared_config_and_records_in_redis(fake_redis):
    await config_store.save_config(fake_redis, "sms", success_rate=0)
    assert await config_store.send_with_shared_state(fake_redis, "sms", NOTIFICATION) is False
    await config_store.save_config(fake_redis, "sms", success_rate=100)
    assert await config_store.send_with_shared_state(fake_redis, "sms", NOTIFICATION) is True

    records = await config_store.get_records(fake_redis, "sms")
    assert [r["recipient_id"] for r in records] == ["u1", "u1"]
    assert await config_store.get_records(fake_redis, "email") == []


async def test_records_keep_latest_max(fake_redis, monkeypatch):
    monkeypatch.setattr(config_store, "MAX_RECORDS", 3)
    for i in range(5):
        await config_store.push_record(fake_redis, "sms", {"n": i})
    assert [r["n"] for r in await config_store.get_records(fake_redis, "sms")] == [2, 3, 4]


async def test_reset_all_clears_config_records_and_memory(fake_redis):
    await config_store.save_config(fake_redis, "sms", success_rate=10, delay_ms=1)
    await config_store.send_with_shared_state(fake_redis, "sms", NOTIFICATION)
    await config_store.reset_all(fake_redis)
    assert await config_store.load_config(fake_redis, "sms") == (100, 0)
    assert await config_store.get_records(fake_redis, "sms") == []
    assert MOCKS["sms"].get_records() == []
