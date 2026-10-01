from app.core.config import Settings
from app.main import app


def test_settings_defaults(monkeypatch):
    for key in ("DATABASE_URL", "REDIS_URL", "RATE_LIMIT_DEFAULT", "QUEUE_ALERT_THRESHOLD"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.RATE_LIMIT_DEFAULT == 60
    assert s.QUEUE_ALERT_THRESHOLD == 1000
    assert s.DATABASE_URL.startswith("postgresql+asyncpg://")
    assert s.REDIS_URL.startswith("redis://")


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "10")
    assert Settings(_env_file=None).RATE_LIMIT_DEFAULT == 10


def test_app_import():
    assert app.title == "Alert System"
