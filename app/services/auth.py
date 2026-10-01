import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import App

MAX_CREDENTIAL_LENGTH = 256


def _valid_credential(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_CREDENTIAL_LENGTH


async def authenticate_app(app_key: str, app_secret: str, db: AsyncSession) -> App | None:
    if not (_valid_credential(app_key) and _valid_credential(app_secret)):
        return None
    result = await db.execute(select(App).where(App.app_key == app_key))
    app = result.scalar_one_or_none()
    if app is None or not app.is_active:
        return None
    if not hmac.compare_digest(app.app_secret.encode(), app_secret.encode()):
        return None
    return app


async def get_current_app(request: Request, db: AsyncSession = Depends(get_db)) -> App:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and ("app_key" in body or "app_secret" in body):
        app_key, app_secret = body.get("app_key"), body.get("app_secret")
    else:
        # 본문이 없는 요청(GET 등)은 헤더로 인증한다.
        app_key, app_secret = request.headers.get("X-App-Key"), request.headers.get("X-App-Secret")
    app = await authenticate_app(app_key, app_secret, db)
    if app is None:
        raise HTTPException(status_code=401, detail="appKey 또는 appSecret 인증에 실패했습니다.")
    return app
