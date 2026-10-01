import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    429: "RATE_LIMIT_EXCEEDED",
    503: "SERVICE_UNAVAILABLE",
}


def error_response(
    status_code: int,
    message: str,
    details: list[dict] | None = None,
    headers: dict[str, str] | None = None,
    code: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code or ERROR_CODES.get(status_code, "HTTP_ERROR"),
                "message": message,
                "details": details or [],
            },
            "request_id": f"req_{uuid.uuid4().hex[:12]}",
        },
        headers=headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        # detail에 code, message, details를 담아 던지면 그대로 응답 형식에 반영한다.
        return error_response(
            exc.status_code,
            exc.detail["message"],
            exc.detail.get("details"),
            exc.headers,
            exc.detail.get("code"),
        )
    return error_response(exc.status_code, str(exc.detail), headers=exc.headers)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in err["loc"][1:]), "reason": err["msg"]}
        for err in exc.errors()
    ]
    return error_response(400, "요청 필드 유효성 검증에 실패했습니다.", details)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
