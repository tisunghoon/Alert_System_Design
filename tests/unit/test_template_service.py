import json
import uuid
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.models.notification_template import NotificationTemplate
from app.services.template_service import (
    TemplateNotFoundError,
    TemplateVariableMismatchError,
    get_template,
    render_template,
)

TEMPLATE = {"title": "{{name}}님 안녕하세요", "body": "{{item}} 주문이 {{name}}님께 발송되었습니다."}


def make_row(template_id, *, is_deleted=False):
    return NotificationTemplate(
        id=template_id,
        name="order",
        title="{{name}}님",
        body="{{item}}",
        placeholders=["{{name}}", "{{item}}"],
        is_deleted=is_deleted,
    )


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def test_render_replaces_title_and_body():
    result = render_template(TEMPLATE, {"name": "철수", "item": "책"})
    assert result == {"title": "철수님 안녕하세요", "body": "책 주문이 철수님께 발송되었습니다."}


def test_render_without_title():
    result = render_template({"title": None, "body": "{{a}}"}, {"a": "x"})
    assert result == {"title": None, "body": "x"}


def test_render_does_not_reinterpret_substituted_values():
    result = render_template({"title": None, "body": "{{a}} {{b}}"}, {"a": "{{b}}", "b": "z"})
    assert result["body"] == "{{b}} z"


def test_missing_variable_is_reported():
    with pytest.raises(TemplateVariableMismatchError) as exc:
        render_template(TEMPLATE, {"name": "철수"})
    assert exc.value.missing == ["item"]
    assert exc.value.extra == []


def test_extra_variable_is_reported():
    with pytest.raises(TemplateVariableMismatchError) as exc:
        render_template(TEMPLATE, {"name": "a", "item": "b", "coupon": "c"})
    assert exc.value.missing == []
    assert exc.value.extra == ["coupon"]


def test_missing_and_extra_reported_together():
    with pytest.raises(TemplateVariableMismatchError) as exc:
        render_template(TEMPLATE, {"name": "a", "coupon": "c"})
    assert exc.value.missing == ["item"]
    assert exc.value.extra == ["coupon"]


async def test_get_template_miss_reads_db_and_caches(redis_client):
    template_id = uuid.uuid4()
    session = AsyncMock()
    session.get.return_value = make_row(template_id)

    template = await get_template(session, redis_client, template_id)

    assert template["body"] == "{{item}}"
    assert await redis_client.ttl(f"template:{template_id}") == pytest.approx(600, abs=2)


async def test_get_template_hit_skips_db(redis_client):
    template_id = uuid.uuid4()
    session = AsyncMock()
    session.get.return_value = make_row(template_id)

    first = await get_template(session, redis_client, template_id)
    second = await get_template(session, redis_client, template_id)

    assert first == second
    assert session.get.await_count == 1


async def test_unknown_template_raises_not_found(redis_client):
    session = AsyncMock()
    session.get.return_value = None

    with pytest.raises(TemplateNotFoundError):
        await get_template(session, redis_client, uuid.uuid4())


async def test_deleted_template_raises_not_found_and_is_not_cached(redis_client):
    template_id = uuid.uuid4()
    session = AsyncMock()
    session.get.return_value = make_row(template_id, is_deleted=True)

    with pytest.raises(TemplateNotFoundError):
        await get_template(session, redis_client, template_id)
    assert await redis_client.exists(f"template:{template_id}") == 0


async def test_cache_entry_without_name_is_treated_as_miss(redis_client):
    template_id = uuid.uuid4()
    legacy = {"id": str(template_id), "title": None, "body": "old", "placeholders": []}
    await redis_client.set(f"template:{template_id}", json.dumps(legacy))
    session = AsyncMock()
    session.get.return_value = make_row(template_id)

    template = await get_template(session, redis_client, template_id)

    assert template["name"] == "order"
    session.get.assert_awaited_once()
