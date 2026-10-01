import httpx
from fastapi import APIRouter, FastAPI

from app.core.errors import register_exception_handlers


def make_client(*routers: APIRouter, overrides: dict | None = None) -> httpx.AsyncClient:
    api = FastAPI()
    register_exception_handlers(api)
    for router in routers:
        api.include_router(router)
    api.dependency_overrides.update(overrides or {})
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test")
