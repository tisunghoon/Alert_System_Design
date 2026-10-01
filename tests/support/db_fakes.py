from unittest.mock import AsyncMock, MagicMock


def make_db(found: bool):
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = ("id",) if found else None
    db.execute.return_value = result
    return db
