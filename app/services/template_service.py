import uuid

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_cache, set_cache
from app.models.notification_template import NotificationTemplate
from app.schemas.template import PLACEHOLDER_PATTERN

TEMPLATE_CACHE_TTL = 600


class TemplateVariableMismatchError(Exception):
    def __init__(self, missing: list[str], extra: list[str]):
        self.missing = missing
        self.extra = extra
        super().__init__(f"템플릿 변수가 일치하지 않습니다. 누락: {missing}, 초과: {extra}")


class TemplateNotFoundError(Exception):
    def __init__(self, template_id: uuid.UUID | str):
        self.template_id = str(template_id)
        super().__init__(f"템플릿을 찾을 수 없습니다: {self.template_id}")


def _placeholders(template: dict) -> set[str]:
    text = f"{template.get('title') or ''}\n{template['body']}"
    return set(PLACEHOLDER_PATTERN.findall(text))


def render_template(template: dict, variables: dict[str, str]) -> dict:
    expected = _placeholders(template)
    provided = set(variables)
    if expected != provided:
        raise TemplateVariableMismatchError(
            missing=sorted(expected - provided), extra=sorted(provided - expected)
        )

    def substitute(text: str) -> str:
        return PLACEHOLDER_PATTERN.sub(lambda m: variables[m.group(1)], text)

    title = template.get("title")
    return {
        "title": substitute(title) if title is not None else None,
        "body": substitute(template["body"]),
    }


async def load_template(session: AsyncSession, template_id: uuid.UUID) -> dict:
    row = await session.get(NotificationTemplate, template_id)
    if row is None or row.is_deleted:
        raise TemplateNotFoundError(template_id)
    return {
        "id": str(row.id),
        "name": row.name,
        "title": row.title,
        "body": row.body,
        "placeholders": row.placeholders,
    }


async def get_template(
    session: AsyncSession, client: aioredis.Redis, template_id: uuid.UUID
) -> dict:
    key = f"template:{template_id}"
    cached = await get_cache(client, key)
    if cached is not None:
        return cached

    template = await load_template(session, template_id)
    await set_cache(client, key, template, TEMPLATE_CACHE_TTL)
    return template
