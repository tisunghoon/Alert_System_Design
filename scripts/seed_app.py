import asyncio
import os

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.models import App

APP_KEY = os.getenv("SEED_APP_KEY", "dev-app-key")
APP_SECRET = os.getenv("SEED_APP_SECRET", "dev-app-secret")


async def seed() -> None:
    async with SessionLocal() as session:
        exists = await session.scalar(select(App.id).where(App.app_key == APP_KEY))
        if exists is None:
            session.add(App(app_key=APP_KEY, app_secret=APP_SECRET, name="dev-app"))
            await session.commit()
            print(f"개발용 앱을 생성했습니다: app_key={APP_KEY}")
        else:
            print(f"개발용 앱이 이미 있습니다: app_key={APP_KEY}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
