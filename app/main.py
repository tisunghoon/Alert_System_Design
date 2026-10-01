from fastapi import FastAPI

from app.api import templates
from app.core.errors import register_exception_handlers

app = FastAPI(title="Alert System")
register_exception_handlers(app)

# 각 기능 PR에서 라우터 모듈을 만들고 아래 목록에 추가한다.
routers: list = [templates.router]

for router in routers:
    app.include_router(router)
