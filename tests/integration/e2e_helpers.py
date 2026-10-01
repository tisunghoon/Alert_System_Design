AUTH = {"app_key": "test-key", "app_secret": "test-secret"}


async def post_notification(http, event_id=None, channel="ios", recipient_id="u1", auth=AUTH, **extra):
    body = {**auth, "channel": channel, "recipient_id": recipient_id, "body": "hello", **extra}
    if event_id is not None:
        body["event_id"] = event_id
    return await http.post("/notifications", json=body)


async def get_notification(http, event_id, auth=AUTH):
    return await http.request("GET", f"/notifications/{event_id}", json=auth)
