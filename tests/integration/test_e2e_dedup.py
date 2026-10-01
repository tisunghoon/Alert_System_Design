from .e2e_helpers import post_notification


async def test_same_event_id_with_empty_cache_is_409(http, app_row, redis_client):
    assert (await post_notification(http, "evt-1")).status_code == 202
    await redis_client.delete("dedup:evt-1")

    assert (await post_notification(http, "evt-1")).status_code == 409
    assert await redis_client.xlen("ios_stream") == 1


async def test_delivered_event_id_with_empty_cache_is_409_via_db(
    http, app_row, make_worker, redis_client
):
    worker = await make_worker("ios")
    await post_notification(http, "evt-1")
    await worker.poll_once()
    await redis_client.delete("dedup:evt-1")

    assert (await post_notification(http, "evt-1")).status_code == 409


async def test_cached_event_id_is_409_before_any_db_record(http, app_row, redis_client):
    await redis_client.set("dedup:evt-cached", "1")
    resp = await post_notification(http, "evt-cached")
    assert resp.status_code == 409
    assert await redis_client.xlen("ios_stream") == 0


async def test_second_request_with_cache_is_409_and_not_enqueued_twice(http, app_row, redis_client):
    assert (await post_notification(http, "evt-1")).status_code == 202
    assert await redis_client.exists("dedup:evt-1")
    assert (await post_notification(http, "evt-1")).status_code == 409
    assert await redis_client.xlen("ios_stream") == 1
