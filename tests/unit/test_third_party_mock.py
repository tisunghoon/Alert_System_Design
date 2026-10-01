import random
from datetime import datetime

import pytest

from app.mocks.third_party_mock import (
    MOCKS,
    APNSMock,
    FCMMock,
    SendGridMock,
    ThirdPartyMock,
    TwilioMock,
)


def make_notification(channel="ios", recipient_id="user-1", body="hello"):
    return {"channel": channel, "recipient_id": recipient_id, "body": body}


@pytest.mark.parametrize("value", [-1, 101, 1.5, "50", None])
def test_success_rate_rejects_invalid(value):
    mock = ThirdPartyMock()
    with pytest.raises(ValueError):
        mock.success_rate = value


@pytest.mark.parametrize("value", [0, 50, 100])
def test_success_rate_accepts_bounds(value):
    mock = ThirdPartyMock()
    mock.success_rate = value
    assert mock.success_rate == value


@pytest.mark.parametrize("value", [-1, 30_001, 0.5, "10", None])
def test_delay_ms_rejects_invalid(value):
    mock = ThirdPartyMock()
    with pytest.raises(ValueError):
        mock.delay_ms = value


@pytest.mark.parametrize("value", [0, 1000, 30_000])
def test_delay_ms_accepts_bounds(value):
    mock = ThirdPartyMock()
    mock.delay_ms = value
    assert mock.delay_ms == value


async def test_send_records_required_fields():
    mock = ThirdPartyMock()
    await mock.send(make_notification(channel="sms", recipient_id="u9", body="코드 1234"))

    (record,) = mock.get_records()
    assert record["channel"] == "sms"
    assert record["recipient_id"] == "u9"
    assert record["body"] == "코드 1234"
    assert datetime.fromisoformat(record["received_at"]).tzinfo is not None


async def test_failed_send_is_still_recorded():
    mock = ThirdPartyMock()
    mock.success_rate = 0

    assert await mock.send(make_notification()) is False
    assert len(mock.get_records()) == 1


async def test_full_success_rate_always_succeeds():
    mock = ThirdPartyMock(rng=random.Random(0))
    results = [await mock.send(make_notification()) for _ in range(200)]
    assert all(results)


async def test_send_waits_for_delay(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.mocks.third_party_mock.asyncio.sleep", fake_sleep)
    mock = ThirdPartyMock()
    mock.delay_ms = 250
    await mock.send(make_notification())

    assert slept == [0.25]


async def test_records_capped_at_max_and_drop_oldest():
    mock = ThirdPartyMock()
    for i in range(ThirdPartyMock.MAX_RECORDS + 5):
        await mock.send(make_notification(recipient_id=str(i)))

    records = mock.get_records()
    assert len(records) == ThirdPartyMock.MAX_RECORDS
    assert records[0]["recipient_id"] == "5"
    assert records[-1]["recipient_id"] == str(ThirdPartyMock.MAX_RECORDS + 4)


async def test_reset_clears_records_and_restores_defaults():
    mock = ThirdPartyMock()
    mock.success_rate = 10
    mock.delay_ms = 5
    await mock.send(make_notification())

    mock.reset()

    assert mock.get_records() == []
    assert mock.success_rate == 100
    assert mock.delay_ms == 0


async def test_mocks_are_independent_per_channel():
    apns, fcm = APNSMock(), FCMMock()
    apns.success_rate = 0
    await apns.send(make_notification(channel="ios"))

    assert fcm.success_rate == 100
    assert fcm.get_records() == []


def test_registry_has_one_mock_per_channel():
    assert set(MOCKS) == {"ios", "android", "sms", "email"}
    assert isinstance(MOCKS["ios"], APNSMock)
    assert isinstance(MOCKS["android"], FCMMock)
    assert isinstance(MOCKS["sms"], TwilioMock)
    assert isinstance(MOCKS["email"], SendGridMock)
    assert len({id(m) for m in MOCKS.values()}) == 4
