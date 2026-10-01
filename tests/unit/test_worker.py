import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.core.redis import CONSUMER_GROUP, DEAD_LETTER_STREAM, IOS_STREAM, RETRY_STREAM, xadd_notification
from app.mocks import config_store
from app.mocks.third_party_mock import MOCKS
from app.models import Notification
from app.workers.android_worker import AndroidWorker
from app.workers.base_worker import BaseWorker, backoff_delay
from app.workers.email_worker import EmailWorker
from app.workers.ios_worker import IOSWorker
from app.workers.sms_worker import SMSWorker

EVENT_ID = "evt-1"
NOW = 1_000_000.0


class FakeDB:
    def __init__(self, notification, history):
        self.notification = notification
        self.history = history
        self.commits = 0

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.notification
        return result

    async def get(self, _model, _id):
        return self.notification

    def add(self, obj):
        self.history.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1


class FakeSessions:
    def __init__(self, notification):
        self.notification = notification
        self.history = []
        self.fail = False

    def __call__(self):
        @asynccontextmanager
        async def session():
            if self.fail:
                raise ConnectionError("db down")
            yield FakeDB(self.notification, self.history)

        return session()


def make_notification(status="QUEUED"):
    return Notification(
        id=uuid.uuid4(),
        event_id=EVENT_ID,
        app_id=uuid.uuid4(),
        channel="ios",
        recipient_id="u1",
        body="hi",
        status=status,
        retry_count=0,
        queued_at=datetime.now(UTC),
    )


@pytest.fixture
async def redis_client():
    c = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield c
    await c.aclose()


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
async def worker(redis_client, sessions, clock):
    w = IOSWorker(
        redis_client=redis_client,
        session_factory=sessions,
        worker_id="ios-test",
        clock=lambda: clock[0],
    )
    await w.setup()
    return w


def message(retry_count=0, **extra):
    return {
        "event_id": EVENT_ID,
        "channel": "ios",
        "recipient_id": "u1",
        "title": "t",
        "body": "hi",
        "retry_count": retry_count,
        **extra,
    }


async def pending_count(client, stream, group=CONSUMER_GROUP):
    return (await client.xpending(stream, group))["pending"]


async def test_success_marks_delivered_and_acks(worker, redis_client, sessions):
    await xadd_notification(redis_client, IOS_STREAM, message())

    await worker.poll_once()

    assert sessions.notification.status == "DELIVERED"
    assert sessions.notification.total_duration_ms is not None
    assert [h.status for h in sessions.history] == ["PROCESSING", "DELIVERED"]
    assert [r["recipient_id"] for r in await config_store.get_records(redis_client, "ios")] == ["u1"]
    assert await pending_count(redis_client, IOS_STREAM) == 0


async def test_already_delivered_is_skipped_and_acked(worker, redis_client, sessions):
    sessions.notification.status = "DELIVERED"
    await xadd_notification(redis_client, IOS_STREAM, message())

    await worker.poll_once()

    assert await config_store.get_records(redis_client, "ios") == []
    assert sessions.history == []
    assert await pending_count(redis_client, IOS_STREAM) == 0


async def test_failure_enqueues_retry_with_backoff(worker, redis_client, sessions):
    await config_store.save_config(redis_client, "ios", success_rate=0)
    await xadd_notification(redis_client, IOS_STREAM, message())

    await worker.poll_once()

    (_, fields), = await redis_client.xrange(RETRY_STREAM)
    assert fields["retry_count"] == "1"
    assert fields["channel"] == "ios"
    assert int(fields["next_retry_after"]) == int(NOW) + 1
    assert sessions.notification.retry_count == 1
    assert sessions.notification.status == "QUEUED"
    note = sessions.history[-1].note
    assert "재시도 1/3" in note and "전송 실패" in note
    assert await pending_count(redis_client, IOS_STREAM) == 0
    assert await redis_client.xlen(DEAD_LETTER_STREAM) == 0


async def test_retry_waits_until_due_then_runs(worker, redis_client, sessions, clock):
    await xadd_notification(
        redis_client, RETRY_STREAM, message(1, next_retry_after=int(NOW) + 10)
    )

    await worker.poll_once()
    assert sessions.history == []
    assert await pending_count(redis_client, RETRY_STREAM, worker.retry_group) == 1

    clock[0] = NOW + 10
    await worker.poll_once()
    assert sessions.notification.status == "DELIVERED"
    assert [h.note for h in sessions.history][0] == "재시도 1회차 시작"
    assert await pending_count(redis_client, RETRY_STREAM, worker.retry_group) == 0


async def test_retry_for_other_channel_is_ignored(worker, redis_client, sessions):
    await xadd_notification(
        redis_client, RETRY_STREAM, message(1, channel="sms", next_retry_after=int(NOW))
    )

    await worker.poll_once()

    assert sessions.history == []
    assert await config_store.get_records(redis_client, "ios") == []
    assert await pending_count(redis_client, RETRY_STREAM, worker.retry_group) == 0


async def test_retry_exhaustion_goes_to_dead_letter(worker, redis_client, sessions):
    await config_store.save_config(redis_client, "ios", success_rate=0)
    await xadd_notification(redis_client, IOS_STREAM, message(retry_count=3))

    await worker.poll_once()

    assert sessions.notification.status == "FAILED"
    assert sessions.notification.total_duration_ms is not None
    assert "최종 실패" in sessions.history[-1].note
    assert await redis_client.xlen(RETRY_STREAM) == 0
    (_, dlq), = await redis_client.xrange(DEAD_LETTER_STREAM)
    assert dlq["notification_id"] == EVENT_ID
    assert dlq["retry_count"] == "3"
    assert dlq["failure_reason"]
    assert datetime.fromisoformat(dlq["failed_at"]).tzinfo is not None
    assert await pending_count(redis_client, IOS_STREAM) == 0


async def test_timeout_is_recorded_as_failure_and_retried(worker, redis_client, sessions):
    async def slow(_notification):
        await asyncio.sleep(1)
        return True

    worker.deliver = slow
    worker.send_timeout = 0.01
    await xadd_notification(redis_client, IOS_STREAM, message())

    await worker.poll_once()

    assert "타임아웃" in sessions.history[-1].note
    assert await redis_client.xlen(RETRY_STREAM) == 1


async def test_db_failure_retries_then_dead_letters(worker, redis_client, sessions):
    sessions.fail = True
    await xadd_notification(redis_client, IOS_STREAM, message())

    await worker.poll_once()

    (_, retry), = await redis_client.xrange(RETRY_STREAM)
    assert retry["retry_count"] == "1"
    assert await pending_count(redis_client, IOS_STREAM) == 0

    await xadd_notification(redis_client, IOS_STREAM, message(retry_count=3))
    await worker.poll_once()

    (_, dlq), = await redis_client.xrange(DEAD_LETTER_STREAM)
    assert "ConnectionError" in dlq["failure_reason"]


async def test_unknown_event_is_retried(worker, redis_client):
    await xadd_notification(redis_client, IOS_STREAM, {**message(), "event_id": "missing"})
    worker.session_factory = FakeSessions(None)

    await worker.poll_once()

    assert await redis_client.xlen(RETRY_STREAM) == 1


async def test_unacked_message_is_reprocessed_from_pending(worker, redis_client, sessions):
    msg_id = await xadd_notification(redis_client, IOS_STREAM, message())
    await redis_client.xreadgroup(CONSUMER_GROUP, worker.worker_id, {IOS_STREAM: ">"}, count=10)
    assert await pending_count(redis_client, IOS_STREAM) == 1

    await worker.poll_once()

    assert sessions.notification.status == "DELIVERED"
    assert msg_id
    assert await pending_count(redis_client, IOS_STREAM) == 0


async def test_run_stops_after_stop_is_requested(worker, redis_client):
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
def test_channel_workers_use_their_own_stream(redis_client, cls, channel, stream):
    w = cls(redis_client=redis_client, session_factory=lambda: None)
    assert issubclass(cls, BaseWorker)
    assert (w.channel, w.stream) == (channel, stream)


@pytest.mark.parametrize(("n", "delay"), [(1, 1), (2, 2), (3, 4), (6, 32), (9, 32)])
def test_backoff_delay(n, delay):
    assert backoff_delay(n) == delay
