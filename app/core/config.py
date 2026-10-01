from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://alert:alert@localhost:5432/alert"
    REDIS_URL: str = "redis://localhost:6379/0"
    RATE_LIMIT_DEFAULT: int = 60
    QUEUE_ALERT_THRESHOLD: int = 1000


settings = Settings()
