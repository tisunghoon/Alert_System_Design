import fakeredis
import pytest


@pytest.fixture
async def fake_redis():
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    yield client
    await client.aclose()
