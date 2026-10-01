from .e2e_helpers import post_notification


async def test_61st_request_is_rejected_with_retry_after(http, app_row, redis_client):
    for i in range(60):
        resp = await post_notification(http, f"evt-{i}", "ios", "u1")
        assert resp.status_code == 202, i

    resp = await post_notification(http, "evt-61", "ios", "u1")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert await redis_client.xlen("ios_stream") == 60

    assert (await post_notification(http, "evt-sms", "sms", "u1")).status_code == 202
    assert (await post_notification(http, "evt-u2", "ios", "u2")).status_code == 202


async def test_user_specific_limit_overrides_default(http, app_row, redis_client):
    from app.services.rate_limiter import set_user_limit

    await set_user_limit("u1", "ios", 2, client=redis_client)
    statuses = [(await post_notification(http, f"evt-{i}", "ios", "u1")).status_code for i in range(3)]
    assert statuses == [202, 202, 429]
