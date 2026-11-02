"""Request-scoped context: request id, client ip, client origin."""

from __future__ import annotations

import contextvars
import uuid

from starlette.requests import Request

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str:
    return request_id_var.get()


def client_ip(request: Request) -> str | None:
    """Return the peer address.

    ``X-Forwarded-For`` is deliberately ignored: the deployment publishes only
    the reverse proxy, which sets its own forwarding header, but trusting a
    client-controlled header would let an attacker forge audit records.
    """
    if request.client is None:
        return None
    return request.client.host
