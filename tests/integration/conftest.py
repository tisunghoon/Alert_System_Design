import os
import subprocess
import sys
import uuid

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import App


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/integration" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.integration)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"통합 테스트에는 {name} 환경변수가 필요합니다.", pytrace=False)
    return value


def _require_disposable_database(url: str) -> None:
    # 마이그레이션 fixture가 downgrade base와 TRUNCATE를 실행하므로 개발 DB를 실수로 지우지 않게 막는다
    name = make_url(url).database or ""
    if "test" not in name and os.environ.get("ALLOW_DESTRUCTIVE_TESTS") != "1":
        pytest.fail(
            f"통합 테스트는 테이블을 삭제합니다. DB 이름('{name}')에 'test'가 없으면 "
            "ALLOW_DESTRUCTIVE_TESTS=1이 필요합니다.",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def migrated_database() -> str:
    url = _require_env("DATABASE_URL")
    _require_disposable_database(url)
    for command in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *command], check=True)
    return url


@pytest.fixture
async def engine(migrated_database):
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE notification_status_history, notifications, notification_templates, "
                "devices, user_preferences, users, apps CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
async def redis_client(migrated_database):
    client = aioredis.from_url(_require_env("REDIS_URL"), decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def app_row(db: AsyncSession) -> App:
    app = App(id=uuid.uuid4(), app_key="test-key", app_secret="test-secret", name="test")
    db.add(app)
    await db.commit()
    return app


@pytest.fixture
async def http(engine, redis_client):
    import httpx

    from app.core.database import get_db
    from app.core.redis import get_redis
    from app.main import app

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis_client
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()
