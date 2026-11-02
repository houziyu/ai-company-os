"""Unified error responses.

Every error the API returns has the same envelope so the frontend can render a
Chinese message and map field errors:

.. code-block:: json

    {"code": "...", "message": "...", "requestId": "...", "fieldErrors": []}

Keys are camelCase because this payload is returned directly through
``JSONResponse`` and therefore bypasses Pydantic aliasing. Internal exception
text is logged but never returned.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.context import get_request_id, new_request_id
from app.domain.errors import AppError, FieldError

logger = logging.getLogger(__name__)

FIELD_ALIASES = {
    "display_name": "displayName",
    "password_confirm": "passwordConfirm",
    "session_ttl_hours": "sessionTtlHours",
}


def error_payload(
    code: str,
    message: str,
    *,
    field_errors: list[FieldError] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = get_request_id()
    if request_id in ("-", ""):
        # An exception can escape before RequestContextMiddleware set the id;
        # never return a placeholder, since support needs a correlatable value.
        request_id = new_request_id()
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "requestId": request_id,
        "fieldErrors": [
            {"field": item.field, "message": item.message} for item in (field_errors or [])
        ],
    }
    if details:
        payload["details"] = details
    return payload


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("application error", extra={"code": exc.code})
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            exc.code, exc.message, field_errors=exc.field_errors, details=exc.details
        ),
        headers={"Cache-Control": "no-store"},
    )


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    field_errors: list[FieldError] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        field = ".".join(location) or "body"
        field = FIELD_ALIASES.get(field, field)
        field_errors.append(FieldError(field=field, message="请检查该字段的格式。"))
    return JSONResponse(
        status_code=422,
        content=error_payload(
            "VALIDATION_FAILED", "提交的内容有误，请检查后重试。", field_errors=field_errors
        ),
        headers={"Cache-Control": "no-store"},
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    messages = {
        400: "请求格式不正确，请检查后重试。",
        401: "登录已失效，请重新登录。",
        403: "没有权限执行该操作。",
        404: "请求的资源不存在。",
        405: "该请求方法不被允许。",
        409: "当前状态不允许该操作，请刷新后重试。",
        413: "请求内容过大。",
        415: "不支持的请求内容类型。",
        429: "请求过于频繁，请稍后重试。",
    }
    codes = {
        400: "BAD_REQUEST",
        401: "NOT_AUTHENTICATED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        429: "RATE_LIMITED",
    }
    # Framework detail strings are English internals ("There was an error parsing
    # the body"); never surface them as the user-facing message.
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            codes.get(exc.status_code, f"HTTP_{exc.status_code}"),
            messages.get(exc.status_code, "请求无法完成。"),
        ),
        headers=getattr(exc, "headers", None),
    )


async def database_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Translate database failures into an explicit 503.

    An un-migrated or unreachable database is an operational state the operator
    must fix, not a programming error. Returning 503 with a distinct code lets
    the frontend explain it and keeps it out of "unexpected 500" alerts.

    This handler must be registered for ``SQLAlchemyError`` specifically: a
    handler registered for bare ``Exception`` is invoked by Starlette's outermost
    middleware, which re-raises, so the response would never reach the client.
    """
    logger.error(
        "database error handling request",
        extra={"error": type(exc).__name__, "request_id": get_request_id()},
    )
    return JSONResponse(
        status_code=503,
        content=error_payload(
            "DATABASE_NOT_READY",
            "数据库尚未就绪或迁移未完成，请检查服务状态后重试。",
            details={"kind": type(exc).__name__},
        ),
        headers={"Cache-Control": "no-store"},
    )


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception", extra={"error": type(exc).__name__})
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "服务器处理请求时出错，请稍后重试。"),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(SQLAlchemyError, database_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
