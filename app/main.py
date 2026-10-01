from fastapi import FastAPI

app = FastAPI(title="Alert System")

# 각 기능 PR에서 라우터 모듈을 만들고 아래 목록에 추가한다.
routers: list = []

for router in routers:
    app.include_router(router)
