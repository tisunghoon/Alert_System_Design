import random

from hypothesis import given, settings
from hypothesis import strategies as st

from app.mocks.third_party_mock import ThirdPartyMock

NOTIFICATION = {"channel": "ios", "recipient_id": "user-1", "body": "hello"}


@settings(max_examples=100, deadline=None)
@given(rate=st.integers(min_value=0, max_value=100))
async def test_prop_mock_success_rate_converges(rate):
    """Feature: alert-system, Property 10: Mock 성공률 확률적 일치성"""
    mock = ThirdPartyMock(rng=random.Random(0))
    mock.success_rate = rate

    results = [await mock.send(NOTIFICATION) for _ in range(1000)]

    assert abs(sum(results) / 1000 * 100 - rate) <= 5


@settings(max_examples=5, deadline=None)
@given(n=st.integers(min_value=10_001, max_value=15_000))
async def test_prop_mock_records_capped(n):
    """Feature: alert-system, Property 11: Mock 인메모리 저장 용량 제한"""
    mock = ThirdPartyMock()

    for _ in range(n):
        await mock.send(NOTIFICATION)

    assert len(mock.records) == min(n, ThirdPartyMock.MAX_RECORDS)
