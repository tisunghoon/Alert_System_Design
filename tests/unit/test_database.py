from unittest.mock import AsyncMock, patch

import pytest

from app.core import database


@pytest.fixture
def session():
    s = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = s
    with patch.object(database, "SessionLocal", return_value=ctx):
        yield s


async def test_get_db_commits_on_success(session):
    gen = database.get_db()
    assert await gen.__anext__() is session
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_get_db_rolls_back_on_error(session):
    gen = database.get_db()
    await gen.__anext__()
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("boom"))
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
