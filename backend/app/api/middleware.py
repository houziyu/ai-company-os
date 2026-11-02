"""HTTP middleware: request id echo and browser-origin enforcement."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.context import client_ip, new_request_id, request_id_var
from app.config import Settings, normalize_origin

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Browsers send Origin on all cross-origin and same-origin non-GET requests.
# Requests without Origin (curl, server-to-server) are still protected by the
# session cookie plus CSRF header requirement on writes.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

API_PREFIX = "/api/"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id")
        if not request_id or len(request_id) > 64 or not request_id.isascii():
            request_id = new_request_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip(request),
            },
        )
        return response


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject cross-site writes to the API before any handler runs."""

    def __init__(self, app, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in UNSAFE_METHODS and request.url.path.startswith(API_PREFIX):
            origin_header = request.headers.get("origin")
            if origin_header is not None:
                origin = normalize_origin(origin_header)
                if origin not in self._settings.allowed_origins:
                    logger.warning(
                        "rejected cross-origin write",
                        extra={
                            "request_id": request_id_var.get(),
                            "origin": origin_header[:120],
                            "path": request.url.path,
                        },
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "code": "ORIGIN_NOT_ALLOWED",
                            "message": "该请求来源未被允许。",
                            "requestId": request_id_var.get(),
                            "fieldErrors": [],
                        },
                    )
            else:
                referer = request.headers.get("referer")
                if referer:
                    referer_origin = normalize_origin(referer)
                    if referer_origin not in self._settings.allowed_origins:
                        return JSONResponse(
                            status_code=403,
                            content={
                                "code": "ORIGIN_NOT_ALLOWED",
                                "message": "该请求来源未被允许。",
                                "requestId": request_id_var.get(),
                                "fieldErrors": [],
                            },
                        )
        return await call_next(request)
