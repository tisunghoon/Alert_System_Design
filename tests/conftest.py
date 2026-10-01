import fakeredis
import pytest


@pytest.fixture
async def fake_redis():
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def redis_down():
    from app.core.redis import get_redis
    from app.main import app

    down = fakeredis.FakeAsyncRedis(connected=False, decode_responses=True)
    app.dependency_overrides[get_redis] = lambda: down
    return down
