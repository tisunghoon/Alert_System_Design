from fastapi import FastAPI

from app.api import dead_letter, devices, mocks, monitoring, notifications, preferences, templates
from app.core.errors import register_exception_handlers

app = FastAPI(title="Alert System")
register_exception_handlers(app)

# 각 기능 PR에서 라우터 모듈을 만들고 아래 목록에 추가한다.
routers: list = [
    templates.router,
    devices.router,
    preferences.router,
    monitoring.router,
    dead_letter.router,
    mocks.router,
    notifications.router,
]

for router in routers:
    app.include_router(router)
