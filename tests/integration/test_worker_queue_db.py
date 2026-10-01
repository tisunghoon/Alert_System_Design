from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.redis import xadd_notification
from app.mocks import config_store
from app.services import notification_log as log
from app.workers.sms_worker import SMSWorker


async def enqueue(db, redis_client, app_row, event_id):
    await log.create_queued_log(event_id, app_row.id, "sms", "u1", "t", "hello", None, db)
    await db.commit()
    await xadd_notification(
        redis_client,
        "sms_stream",
        {"event_id": event_id, "channel": "sms", "recipient_id": "u1", "title": "t", "body": "hello", "retry_count": 0},
    )


async def queue_sizes(http):
    return {q["channel"]: q["size"] for q in (await http.get("/monitoring/queues")).json()["queues"]}


async def test_queue_size_drops_to_zero_after_worker_processes(http, db, engine, app_row, redis_client):
    worker = SMSWorker(redis_client=redis_client, session_factory=async_sessionmaker(engine, expire_on_commit=False))
    await worker.setup()
    await enqueue(db, redis_client, app_row, "evt-q1")
    await enqueue(db, redis_client, app_row, "evt-q2")
    assert (await queue_sizes(http))["sms"] == 2

    await worker.poll_once()

    assert (await queue_sizes(http))["sms"] == 0
    db.expire_all()
    assert (await log.get_by_event_id("evt-q1", db)).status == "DELIVERED"
    assert (await log.get_by_event_id("evt-q2", db)).status == "DELIVERED"


async def test_failed_message_leaves_channel_queue_but_stays_in_retry_stream(
    http, db, engine, app_row, redis_client
):
    await config_store.save_config(redis_client, "sms", success_rate=0)
    worker = SMSWorker(redis_client=redis_client, session_factory=async_sessionmaker(engine, expire_on_commit=False))
    await worker.setup()
    await enqueue(db, redis_client, app_row, "evt-q3")

    await worker.poll_once()

    assert (await queue_sizes(http))["sms"] == 0
    assert await redis_client.xlen("retry_stream") == 1
