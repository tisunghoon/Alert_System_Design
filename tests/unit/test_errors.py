import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core.errors import register_exception_handlers


class Body(BaseModel):
    recipient_id: str
    channel: str


def make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    async def echo(body: Body):
        return body

    @app.get("/limited")
    async def limited():
        raise HTTPException(429, "요청 한도를 초과했습니다.", headers={"Retry-After": "30"})

    @app.get("/structured")
    async def structured():
        detail = {"code": "DUPLICATE_EVENT", "message": "중복", "details": [{"field": "event_id", "reason": "중복"}]}
        raise HTTPException(409, detail)

    @app.get("/unauthorized")
    async def unauthorized():
        raise HTTPException(401, "인증에 실패했습니다.")

    return app


async def request(method, path, **kwargs):
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def test_validation_error_uses_standard_format():
    res = await request("POST", "/echo", json={"channel": "push"})

    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["request_id"].startswith("req_")
    assert [d["field"] for d in body["error"]["details"]] == ["recipient_id"]
    assert body["error"]["details"][0]["reason"]


async def test_http_exception_uses_standard_format():
    res = await request("GET", "/unauthorized")

    assert res.status_code == 401
    assert res.json()["error"] == {
        "code": "UNAUTHORIZED",
        "message": "인증에 실패했습니다.",
        "details": [],
    }


async def test_retry_after_header_is_preserved():
    res = await request("GET", "/limited")

    assert res.status_code == 429
    assert res.headers["Retry-After"] == "30"
    assert res.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


async def test_unknown_route_uses_standard_format():
    res = await request("GET", "/missing")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


async def test_dict_detail_sets_code_message_and_details():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://test") as c:
        resp = await c.get("/structured")
    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "DUPLICATE_EVENT",
        "message": "중복",
        "details": [{"field": "event_id", "reason": "중복"}],
    }
