"""Application dependencies: settings, DB session, auth guard, CSRF guard."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.context import client_ip
from app.config import Settings
from app.db.session import session_scope
from app.domain.errors import CsrfValidationError, NotAuthenticatedError
from app.services.auth_service import AuthenticatedSession, AuthService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_db(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    with session_scope(factory) as session:
        yield session


# Finish (including COMMIT) before FastAPI sends status, body or Set-Cookie.
DbSession = Annotated[Session, Depends(get_db, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_optional_session(
    request: Request,
    session: DbSession,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> AuthenticatedSession | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    try:
        return auth_service.authenticate(session, token)
    except NotAuthenticatedError:
        return None


def require_owner(
    request: Request,
    session: DbSession,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> AuthenticatedSession:
    """Guards every business API: 401 without a valid server-side session."""
    token = request.cookies.get(settings.session_cookie_name)
    return auth_service.authenticate(session, token)


AuthenticatedDep = Annotated[AuthenticatedSession, Depends(require_owner)]


def require_csrf(
    request: Request,
    authenticated: AuthenticatedDep,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> AuthenticatedSession:
    """State-changing requests need the session-bound CSRF header.

    The token is delivered in a readable cookie and must be echoed in
    ``X-CSRF-Token``. Because a cross-site attacker cannot read the cookie, this
    blocks forged writes even when the browser attaches the session cookie.
    """
    header_token = request.headers.get("x-csrf-token")
    if not header_token:
        raise CsrfValidationError()
    auth_service.verify_csrf(authenticated.session, header_token)
    return authenticated


WriteGuard = Annotated[AuthenticatedSession, Depends(require_csrf)]


def get_client_ip(request: Request) -> str | None:
    return client_ip(request)
