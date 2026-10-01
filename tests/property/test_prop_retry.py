import fakeredis
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.redis import DEAD_LETTER_STREAM, IOS_STREAM, RETRY_STREAM, xadd_notification
from app.mocks import config_store
from app.mocks.third_party_mock import MOCKS
from app.workers.base_worker import MAX_BACKOFF_SECONDS, backoff_delay
from app.workers.ios_worker import IOSWorker
from tests.unit.test_worker import NOW, FakeSessions, make_notification, message


async def make_failing_worker():
    for mock in MOCKS.values():
        mock.reset()
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    await config_store.save_config(client, "ios", success_rate=0)
    sessions = FakeSessions(make_notification())
    worker = IOSWorker(
        redis_client=client, session_factory=sessions, worker_id="ios-prop", clock=lambda: NOW
    )
    await worker.setup()
    return worker, client, sessions


@settings(max_examples=100, deadline=None)
@given(failures=st.integers(min_value=0, max_value=3))
async def test_prop_retry_count_increases_by_one_until_dead_letter(failures):
    """Feature: alert-system, Property 6: 재시도 횟수 단조 증가"""
    worker, client, sessions = await make_failing_worker()

    await xadd_notification(client, IOS_STREAM, message(retry_count=failures))
    await worker.poll_once()

    retries = await client.xrange(RETRY_STREAM)
    dead = await client.xrange(DEAD_LETTER_STREAM)
    if failures < 3:
        assert [int(f["retry_count"]) for _, f in retries] == [failures + 1]
        assert dead == []
        assert sessions.notification.retry_count == failures + 1
    else:
        assert retries == []
        assert len(dead) == 1
        assert sessions.notification.status == "FAILED"
    await client.aclose()


async def test_retry_count_walks_up_to_dead_letter_without_gaps():
    """Feature: alert-system, Property 6: 재시도 횟수 단조 증가"""
    worker, client, sessions = await make_failing_worker()
    await xadd_notification(client, IOS_STREAM, message())

    observed = []
    for _ in range(3):
        await worker.poll_once()
        ((retry_id, fields),) = await client.xrange(RETRY_STREAM)
        observed.append(int(fields["retry_count"]))
        await client.xdel(RETRY_STREAM, retry_id)
        await xadd_notification(client, IOS_STREAM, fields)
    await worker.poll_once()

    assert observed == [1, 2, 3]
    assert await client.xlen(RETRY_STREAM) == 0
    assert await client.xlen(DEAD_LETTER_STREAM) == 1
    assert len(await config_store.get_records(client, "ios")) == 4
    await client.aclose()


@settings(max_examples=100, deadline=None)
@given(retry_number=st.integers(min_value=1, max_value=3))
async def test_prop_backoff_delay_matches_formula_and_is_monotonic(retry_number):
    """Feature: alert-system, Property 7: 지수 백오프 지연 단조 증가"""
    worker, client, _ = await make_failing_worker()

    await xadd_notification(client, IOS_STREAM, message(retry_count=retry_number - 1))
    await worker.poll_once()

    ((_, fields),) = await client.xrange(RETRY_STREAM)
    expected = min(2 ** (retry_number - 1), MAX_BACKOFF_SECONDS)
    assert int(fields["next_retry_after"]) - int(NOW) == expected == backoff_delay(retry_number)
    assert backoff_delay(retry_number + 1) >= backoff_delay(retry_number)
    await client.aclose()
