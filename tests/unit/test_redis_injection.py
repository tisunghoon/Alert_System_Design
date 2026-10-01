import fakeredis
import pytest

from app.api import dead_letter, mocks, monitoring
from app.core.database import get_db
from app.core.redis import DEAD_LETTER_STREAM, get_redis
from tests.support.http import make_client

ROUTES = [
    ("/monitoring/queues", monitoring.router),
    ("/dead-letter", dead_letter.router),
    ("/mocks/sms/records", mocks.router),
]


@pytest.mark.parametrize(("path", "router"), ROUTES)
async def test_redis_comes_only_from_dependency_override(path, router):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    await redis.xadd(DEAD_LETTER_STREAM, {"notification_id": "e1", "retry_count": 3})
    client = make_client(router, overrides={get_redis: lambda: redis, get_db: lambda: None})

    async with client as c:
        resp = await c.get(path)

    assert resp.status_code == 200
