import time

from sqlalchemy import select

from app.models import Notification
from .e2e_helpers import get_notification, post_notification


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


async def test_always_failing_mock_retries_three_times_then_dead_letter(
    http, app_row, make_worker, redis_client, db
):
    clock = Clock()
    worker = await make_worker("ios", clock=clock)
    config = await http.put("/mocks/ios/config", json={"success_rate": 0})
    assert config.status_code == 200
    await post_notification(http, "evt-fail", "ios", "u1")

    await worker.poll_once()
    assert await redis_client.xlen("retry_stream") == 1
    assert await redis_client.xlen("dead_letter_stream") == 0

    # 백오프(1, 2, 4초)가 지나기 전에는 재시도가 처리되지 않는다
    await worker.poll_once()
    assert (await http.get("/mocks/ios/records")).json()["count"] == 1

    for expected_attempts in (2, 3, 4):
        clock.now += 40
        await worker.poll_once()
        assert (await http.get("/mocks/ios/records")).json()["count"] == expected_attempts

    detail = (await get_notification(http, "evt-fail")).json()
    assert detail["status"] == "FAILED"
    assert detail["retry_count"] == 3
    assert detail["failed_at"] is not None
    notes = [h["note"] for h in detail["history"] if h["status"] == "QUEUED" and h["note"]]
    assert len(notes) == 3 and "재시도 3/3" in notes[-1]
    assert [h["status"] for h in detail["history"]].count("PROCESSING") == 4

    assert await redis_client.xlen("dead_letter_stream") == 1
    dead = (await http.get("/dead-letter")).json()["items"]
    assert len(dead) == 1
    assert dead[0]["notification_id"] == "evt-fail"
    assert dead[0]["retry_count"] == 3
    assert dead[0]["failure_reason"] == "Third_Party_Mock 전송 실패"
    assert dead[0]["failed_at"]

    # 더 이상 retry_stream에서 처리되지 않는다
    clock.now += 100
    await worker.poll_once()
    assert (await http.get("/mocks/ios/records")).json()["count"] == 4
    assert await db.scalar(select(Notification.status).where(Notification.event_id == "evt-fail")) == "FAILED"


async def test_retry_succeeds_when_mock_recovers(http, app_row, make_worker):
    clock = Clock()
    worker = await make_worker("ios", clock=clock)
    await http.put("/mocks/ios/config", json={"success_rate": 0})
    await post_notification(http, "evt-recover", "ios", "u1")
    await worker.poll_once()

    await http.put("/mocks/ios/config", json={"success_rate": 100})
    clock.now += 40
    await worker.poll_once()

    detail = (await get_notification(http, "evt-recover")).json()
    assert detail["status"] == "DELIVERED"
    assert detail["retry_count"] == 1
    assert (await http.get("/dead-letter")).json()["items"] == []
