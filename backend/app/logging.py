"""Structured JSON logging.

Only whitelisted fields are logged. Request bodies, cookies, authorization
headers and passwords are never passed to the logger.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

_FIELDS = frozenset(
    {
        "request_id",
        "method",
        "path",
        "status",
        "duration_ms",
        "client_ip",
        "user_id",
        "code",
        "error",
        "version",
        "environment",
        "signal",
        "worker",
        "port",
    }
)
# Event text is code-owned. Never interpolate third-party messages, SQL,
# request data or exception strings into the JSON sink, even at DEBUG.
_EVENTS = frozenset(
    {
        "request",
        "api starting",
        "api stopped",
        "application error",
        "database error handling request",
        "unhandled exception",
        "rejected cross-origin write",
        "readiness: database unreachable",
        "owner setup prepared",
        "login prepared",
        "worker heartbeat failed",
        "worker received signal",
        "worker starting (health loop only)",
        "worker stopped",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.msg
            if isinstance(record.msg, str) and record.msg in _EVENTS and not record.args
            else "log event",
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key in _FIELDS and isinstance(value, (str, int, float, bool, type(None)))
        }
        payload.update(extras)
        if record.exc_info:
            # Preserve diagnostic locations without exception values, source
            # lines or locals (all of which may contain credentials).
            payload["exception_type"] = type(record.exc_info[1]).__name__
            payload["traceback"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)

    for name in ("sqlalchemy", "sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.WARNING)
