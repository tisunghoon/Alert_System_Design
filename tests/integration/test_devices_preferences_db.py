import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Device, User, UserPreference

AUTH = {"app_key": "test-key", "app_secret": "test-secret"}


async def post_device(http, user_id, channel, token):
    return await http.post(f"/users/{user_id}/devices", json={**AUTH, "channel": channel, "token": token})


async def test_register_duplicate_device_is_409(http, app_row):
    assert (await post_device(http, "u1", "ios", "tok")).status_code == 201
    assert (await post_device(http, "u1", "ios", "tok")).status_code == 409
    assert (await post_device(http, "u1", "android", "tok")).status_code == 201


async def test_device_limit_is_10_per_user(http, db, app_row):
    for i in range(10):
        assert (await post_device(http, "u1", "sms", f"tok{i}")).status_code == 201
    resp = await post_device(http, "u1", "sms", "tok10")
    assert resp.status_code == 400
    assert (await post_device(http, "u2", "sms", "tok0")).status_code == 201
    count = await db.scalar(select(func.count()).select_from(Device))
    assert count == 11


async def test_list_and_delete_device(http, app_row):
    device = (await post_device(http, "u1", "email", "a@b.c")).json()
    listed = await http.request("GET", "/users/u1/devices", json=AUTH)
    assert [d["token"] for d in listed.json()] == ["a@b.c"]

    deleted = await http.request("DELETE", f"/users/u1/devices/{device['id']}", json=AUTH)
    assert deleted.status_code == 204
    assert (await http.request("GET", "/users/u1/devices", json=AUTH)).json() == []


async def test_device_channel_check_and_unique_constraints(db):
    user = User(id=uuid.uuid4(), external_id="u1")
    user_id = user.id
    db.add(user)
    await db.commit()

    db.add(Device(user_id=user_id, channel="push", token="t"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()

    db.add_all([Device(user_id=user_id, channel="ios", token="t") for _ in range(2)])
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_deleting_user_cascades_to_devices_and_preferences(db):
    user = User(id=uuid.uuid4(), external_id="u1")
    db.add_all(
        [
            user,
            Device(user_id=user.id, channel="ios", token="t"),
            UserPreference(user_id=user.id, channel="ios", is_enabled=False),
        ]
    )
    await db.commit()

    await db.delete(user)
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Device)) == 0
    assert await db.scalar(select(func.count()).select_from(UserPreference)) == 0


async def test_preferences_upsert_roundtrip_and_unique(http, db, app_row):
    resp = await http.put("/users/u1/preferences", json={**AUTH, "preferences": {"sms": False}})
    assert resp.status_code == 200
    assert resp.json() == {"ios": True, "android": True, "sms": False, "email": True}

    await http.put("/users/u1/preferences", json={**AUTH, "preferences": {"sms": True, "email": False}})
    got = (await http.request("GET", "/users/u1/preferences", json=AUTH)).json()
    assert got == {"ios": True, "android": True, "sms": True, "email": False}
    assert await db.scalar(select(func.count()).select_from(UserPreference)) == 2

    user = await db.scalar(select(User))
    db.add(UserPreference(user_id=user.id, channel="sms", is_enabled=False))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
