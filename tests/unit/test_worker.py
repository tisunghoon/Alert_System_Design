import asyncio
from datetime import datetime

import pytest

from app.core.redis import CONSUMER_GROUP, DEAD_LETTER_STREAM, IOS_STREAM, RETRY_STREAM, xadd_notification
from app.mocks import config_store
from app.mocks.third_party_mock import MOCKS
from app.workers import base_worker
from app.workers.android_worker import AndroidWorker
from app.workers.base_worker import BaseWorker, backoff_delay
from app.workers.email_worker import EmailWorker
from app.workers.ios_worker import IOSWorker
from app.workers.sms_worker import SMSWorker
from tests.support.worker_fakes import EVENT_ID, NOW, FakeSessions, make_notification, message

@pytest.fixture(autouse=True)
def clean_mocks():
    yield
    for mock in MOCKS.values():
        mock.reset()


@pytest.fixture
def sessions():
    return FakeSessions(make_notification())


@pytest.fixture
def clock():
    return [NOW]


@pytest.fixture
async def worker(fake_redis, sessions, clock):
    w = IOSWorker(
        redis_client=fake_redis,
        session_factory=sessions,
        worker_id="ios-test",
        clock=lambda: clock[0],
    )
    await w.setup()
    return w


async def pending_count(client, stream, group=CONSUMER_GROUP):
    return (await client.xpending(stream, group))["pending"]


async def test_success_marks_delivered_and_acks(worker, fake_redis, sessions):
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    assert sessions.notification.status == "DELIVERED"
    assert sessions.notification.total_duration_ms is not None
    assert [h.status for h in sessions.history] == ["PROCESSING", "DELIVERED"]
    assert [r["recipient_id"] for r in await config_store.get_records(fake_redis, "ios")] == ["u1"]
    assert await pending_count(fake_redis, IOS_STREAM) == 0


async def test_already_delivered_is_skipped_and_acked(worker, fake_redis, sessions):
    sessions.notification.status = "DELIVERED"
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    assert await config_store.get_records(fake_redis, "ios") == []
    assert sessions.history == []
    assert await pending_count(fake_redis, IOS_STREAM) == 0


async def test_failure_enqueues_retry_with_backoff(worker, fake_redis, sessions):
    await config_store.save_config(fake_redis, "ios", success_rate=0)
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    (_, fields), = await fake_redis.xrange(RETRY_STREAM)
    assert fields["retry_count"] == "1"
    assert fields["channel"] == "ios"
    assert int(fields["next_retry_after"]) == int(NOW) + 1
    assert sessions.notification.retry_count == 1
    assert sessions.notification.status == "QUEUED"
    note = sessions.history[-1].note
    assert "재시도 1/3" in note and "전송 실패" in note
    assert await pending_count(fake_redis, IOS_STREAM) == 0
    assert await fake_redis.xlen(DEAD_LETTER_STREAM) == 0


async def test_retry_waits_until_due_then_runs(worker, fake_redis, sessions, clock):
    await xadd_notification(
        fake_redis, RETRY_STREAM, message(1, next_retry_after=int(NOW) + 10)
    )

    await worker.poll_once()
    assert sessions.history == []
    assert await pending_count(fake_redis, RETRY_STREAM, worker.retry_group) == 1

    clock[0] = NOW + 10
    await worker.poll_once()
    assert sessions.notification.status == "DELIVERED"
    assert [h.note for h in sessions.history][0] == "재시도 1회차 시작"
    assert await pending_count(fake_redis, RETRY_STREAM, worker.retry_group) == 0


async def test_retry_for_other_channel_is_ignored(worker, fake_redis, sessions):
    await xadd_notification(
        fake_redis, RETRY_STREAM, message(1, channel="sms", next_retry_after=int(NOW))
    )

    await worker.poll_once()

    assert sessions.history == []
    assert await config_store.get_records(fake_redis, "ios") == []
    assert await pending_count(fake_redis, RETRY_STREAM, worker.retry_group) == 0


async def test_retry_exhaustion_goes_to_dead_letter(worker, fake_redis, sessions):
    await config_store.save_config(fake_redis, "ios", success_rate=0)
    await xadd_notification(fake_redis, IOS_STREAM, message(retry_count=3))

    await worker.poll_once()

    assert sessions.notification.status == "FAILED"
    assert sessions.notification.total_duration_ms is not None
    assert "최종 실패" in sessions.history[-1].note
    assert await fake_redis.xlen(RETRY_STREAM) == 0
    (_, dlq), = await fake_redis.xrange(DEAD_LETTER_STREAM)
    assert dlq["notification_id"] == EVENT_ID
    assert dlq["retry_count"] == "3"
    assert dlq["failure_reason"]
    assert datetime.fromisoformat(dlq["failed_at"]).tzinfo is not None
    assert await pending_count(fake_redis, IOS_STREAM) == 0


async def test_timeout_is_recorded_as_failure_and_retried(worker, fake_redis, sessions):
    async def slow(_notification):
        await asyncio.sleep(1)
        return True

    worker.deliver = slow
    worker.send_timeout = 0.01
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    assert "타임아웃" in sessions.history[-1].note
    assert await fake_redis.xlen(RETRY_STREAM) == 1


async def test_db_failure_retries_then_dead_letters(worker, fake_redis, sessions):
    sessions.fail = True
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    (_, retry), = await fake_redis.xrange(RETRY_STREAM)
    assert retry["retry_count"] == "1"
    assert await pending_count(fake_redis, IOS_STREAM) == 0

    await xadd_notification(fake_redis, IOS_STREAM, message(retry_count=3))
    await worker.poll_once()

    (_, dlq), = await fake_redis.xrange(DEAD_LETTER_STREAM)
    assert "ConnectionError" in dlq["failure_reason"]


async def test_unknown_event_is_retried(worker, fake_redis):
    await xadd_notification(fake_redis, IOS_STREAM, {**message(), "event_id": "missing"})
    worker.session_factory = FakeSessions(None)

    await worker.poll_once()

    assert await fake_redis.xlen(RETRY_STREAM) == 1


async def test_unacked_message_is_reprocessed_from_pending(worker, fake_redis, sessions):
    msg_id = await xadd_notification(fake_redis, IOS_STREAM, message())
    await fake_redis.xreadgroup(CONSUMER_GROUP, worker.worker_id, {IOS_STREAM: ">"}, count=10)
    assert await pending_count(fake_redis, IOS_STREAM) == 1

    await worker.poll_once()

    assert sessions.notification.status == "DELIVERED"
    assert msg_id
    assert await pending_count(fake_redis, IOS_STREAM) == 0


async def test_run_stops_after_stop_is_requested(worker, fake_redis):
    polls = 0
    original = worker.poll_once

    async def poll_then_stop():
        nonlocal polls
        polls += 1
        await original()
        worker.stop()

    worker.poll_once = poll_then_stop

    await asyncio.wait_for(worker.run(), timeout=5)

    assert polls == 1


@pytest.mark.parametrize(
    ("cls", "channel", "stream"),
    [
        (IOSWorker, "ios", "ios_stream"),
        (AndroidWorker, "android", "android_stream"),
        (SMSWorker, "sms", "sms_stream"),
        (EmailWorker, "email", "email_stream"),
    ],
)
def test_channel_workers_use_their_own_stream(fake_redis, cls, channel, stream):
    w = cls(redis_client=fake_redis, session_factory=lambda: None)
    assert issubclass(cls, BaseWorker)
    assert (w.channel, w.stream) == (channel, stream)


@pytest.mark.parametrize(("n", "delay"), [(1, 1), (2, 2), (3, 4), (6, 32), (9, 32)])
def test_backoff_delay(n, delay):
    assert backoff_delay(n) == delay


@pytest.fixture
def ordered_events(monkeypatch, sessions):
    original_ack = base_worker.xack_message
    original_xadd = base_worker.xadd_notification

    async def recording_ack(*args, **kwargs):
        sessions.events.append("xack")
        return await original_ack(*args, **kwargs)

    async def recording_xadd(client, stream, fields):
        sessions.events.append(f"xadd:{stream}")
        return await original_xadd(client, stream, fields)

    original_xdel = base_worker.xdel_message

    async def recording_xdel(client, stream, message_id):
        sessions.events.append(f"xdel:{stream}")
        return await original_xdel(client, stream, message_id)

    monkeypatch.setattr(base_worker, "xack_message", recording_ack)
    monkeypatch.setattr(base_worker, "xdel_message", recording_xdel)
    monkeypatch.setattr(base_worker, "xadd_notification", recording_xadd)
    return sessions.events


async def test_xack_happens_after_delivered_commit(worker, fake_redis, ordered_events):
    await xadd_notification(fake_redis, IOS_STREAM, message())
    ordered_events.clear()

    await worker.poll_once()

    assert ordered_events[-3:] == ["commit", "xack", f"xdel:{IOS_STREAM}"]
    assert ordered_events.count("xack") == 1


async def test_xack_happens_after_retry_enqueue_and_commit(worker, fake_redis, ordered_events):
    await config_store.save_config(fake_redis, "ios", success_rate=0)
    await xadd_notification(fake_redis, IOS_STREAM, message())
    ordered_events.clear()

    await worker.poll_once()

    assert ordered_events[-4:] == ["commit", f"xadd:{RETRY_STREAM}", "xack", f"xdel:{IOS_STREAM}"]


async def test_xack_happens_after_dead_letter_commit(worker, fake_redis, ordered_events):
    await config_store.save_config(fake_redis, "ios", success_rate=0)
    await xadd_notification(fake_redis, IOS_STREAM, message(retry_count=3))
    ordered_events.clear()

    await worker.poll_once()

    assert ordered_events[-4:] == ["commit", f"xadd:{DEAD_LETTER_STREAM}", "xack", f"xdel:{IOS_STREAM}"]


async def test_processed_message_is_removed_from_channel_stream(worker, fake_redis):
    await xadd_notification(fake_redis, IOS_STREAM, message())
    assert await fake_redis.xlen(IOS_STREAM) == 1

    await worker.poll_once()

    assert await fake_redis.xlen(IOS_STREAM) == 0


async def test_failed_message_is_removed_from_channel_stream_after_retry_enqueue(worker, fake_redis):
    await config_store.save_config(fake_redis, "ios", success_rate=0)
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    assert await fake_redis.xlen(IOS_STREAM) == 0
    assert await fake_redis.xlen(RETRY_STREAM) == 1


async def test_retry_stream_message_is_not_deleted(worker, fake_redis, ordered_events):
    await xadd_notification(fake_redis, RETRY_STREAM, message(retry_count=1, next_retry_after=0))
    ordered_events.clear()

    await worker.poll_once()

    assert "xack" in ordered_events
    assert not any(e.startswith("xdel") for e in ordered_events)
    assert await fake_redis.xlen(RETRY_STREAM) == 1


async def test_unprocessed_message_stays_in_channel_stream(worker, fake_redis):
    worker.stop()
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    assert await fake_redis.xlen(IOS_STREAM) == 1


def test_default_consumer_name_is_stable_per_channel(fake_redis, monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    assert IOSWorker(redis_client=fake_redis).worker_id == "ios"
    assert IOSWorker(redis_client=fake_redis).worker_id == IOSWorker(redis_client=fake_redis).worker_id

    monkeypatch.setenv("WORKER_ID", "ios-2")
    assert IOSWorker(redis_client=fake_redis).worker_id == "ios-2"


async def test_restarted_worker_picks_up_pending_of_previous_run(fake_redis, sessions, monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    first = IOSWorker(redis_client=fake_redis, session_factory=sessions)
    await first.setup()
    await xadd_notification(fake_redis, IOS_STREAM, message())
    # 처리 도중 프로세스가 죽은 상황: 읽기만 하고 ack하지 않는다.
    await fake_redis.xreadgroup(CONSUMER_GROUP, first.worker_id, {IOS_STREAM: ">"}, count=10)

    restarted = IOSWorker(redis_client=fake_redis, session_factory=sessions)
    await restarted.poll_once()

    assert sessions.notification.status == "DELIVERED"
    assert await pending_count(fake_redis, IOS_STREAM) == 0


async def test_failure_trims_old_retry_entries_only(worker, fake_redis, clock):
    await config_store.save_config(fake_redis, "ios", success_rate=0)
    await fake_redis.xadd(RETRY_STREAM, {"event_id": "old", "channel": "sms"}, id="1-0")
    await xadd_notification(fake_redis, IOS_STREAM, message())

    await worker.poll_once()

    events = [fields["event_id"] for _, fields in await fake_redis.xrange(RETRY_STREAM)]
    assert events == [EVENT_ID]


async def test_trimmed_pending_entry_is_acked(worker, fake_redis):
    await xadd_notification(fake_redis, RETRY_STREAM, message(1, next_retry_after=int(NOW) + 100))
    await worker.poll_once()
    assert await pending_count(fake_redis, RETRY_STREAM, worker.retry_group) == 1

    await fake_redis.xtrim(RETRY_STREAM, maxlen=0)
    await worker.poll_once()

    assert await pending_count(fake_redis, RETRY_STREAM, worker.retry_group) == 0


@pytest.mark.parametrize("trimmed_fields", [{}, None])
async def test_pending_entry_with_empty_body_is_acked_not_processed(
    worker, fake_redis, sessions, monkeypatch, trimmed_fields
):
    acked = []

    async def fake_read(group, consumer, streams, count, block):
        (stream,) = streams
        return [[stream, [("1-0", trimmed_fields)]]] if streams[stream] == "0" else []

    async def fake_ack(client, stream, message_id, group):
        acked.append((stream, message_id))

    monkeypatch.setattr(fake_redis, "xreadgroup", fake_read)
    monkeypatch.setattr(base_worker, "xack_message", fake_ack)

    await worker.poll_once()

    assert (RETRY_STREAM, "1-0") in acked
    assert sessions.history == []
    assert await fake_redis.xlen(RETRY_STREAM) == 0
    assert await fake_redis.xlen(DEAD_LETTER_STREAM) == 0


async def test_due_retry_is_not_starved_by_many_not_due(worker, fake_redis, sessions):
    for i in range(12):
        await xadd_notification(
            fake_redis,
            RETRY_STREAM,
            {**message(1, next_retry_after=int(NOW) + 1000), "event_id": f"later-{i}"},
        )
    await xadd_notification(fake_redis, RETRY_STREAM, message(1, next_retry_after=int(NOW)))

    await worker.poll_once()
    await worker.poll_once()

    assert sessions.notification.status == "DELIVERED"
    assert await pending_count(fake_redis, RETRY_STREAM, worker.retry_group) == 12
