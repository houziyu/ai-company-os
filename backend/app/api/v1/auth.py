"""Authentication routes: setup, login, logout, current session.

Route notes:
* ``GET /auth/setup-status`` is public because the SPA must decide between the
  setup page and the login page before it has a session.
* ``POST /auth/setup`` is only accepted when no owner exists, and is guarded by
  the same origin checks as other writes plus a one-time row lock.
* ``POST /auth/login`` and ``POST /auth/logout`` are CSRF-protected through the
  double-submit header once a session exists; login itself relies on the origin
  guard because there is no session yet.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.context import client_ip, get_request_id
from app.api.cookies import clear_session_cookies, set_session_cookies
from app.api.deps import (
    AuthenticatedDep,
    AuthServiceDep,
    DbSession,
    SettingsDep,
    WriteGuard,
)
from app.api.schemas import (
    CurrentSessionResponse,
    LoginRequest,
    OwnerProfile,
    SessionResponse,
    SetupRequest,
    SetupStatus,
)
from app.domain.errors import FieldError, SetupRequiredError, ValidationFailedError
from app.repositories.organization_repository import CompanyRepository

router = APIRouter(prefix="/auth", tags=["auth"])


def _profile(user) -> OwnerProfile:  # type: ignore[no-untyped-def]
    return OwnerProfile(
        id=user.id, username=user.username, display_name=user.display_name, role=user.role
    )


def _session_body(issued) -> SessionResponse:  # type: ignore[no-untyped-def]
    return SessionResponse(
        user=_profile(issued.user),
        expires_at=issued.expires_at,
        csrf_token=issued.csrf_token,
    )


@router.get("/setup-status", response_model=SetupStatus)
def setup_status(
    session: DbSession, auth_service: AuthServiceDep, settings: SettingsDep
) -> SetupStatus:
    """Public: the SPA must know whether to show setup, login or the wizard.

    ``companyInitialized`` routes an authenticated owner who has not created a
    company to the wizard instead of an empty workbench. It reveals only whether
    a company exists — no company content.
    """
    company = CompanyRepository().get(session)
    return SetupStatus(
        setup_complete=auth_service.is_setup_complete(session),
        app_version=settings.app_version,
        password_min_length=settings.password_min_length,
        company_initialized=company is not None,
        company_name=company.name if company else None,
    )


@router.post(
    "/setup",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
)
def setup(
    payload: SetupRequest,
    request: Request,
    response: Response,
    session: DbSession,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> SessionResponse:
    if payload.password != payload.password_confirm:
        raise ValidationFailedError([FieldError("passwordConfirm", "两次输入的密码不一致。")])
    issued = auth_service.complete_setup(
        session,
        username=payload.username,
        display_name=payload.display_name,
        password=payload.password,
        request_id=get_request_id(),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    ttl_seconds = settings.session_ttl_hours * 3600
    set_session_cookies(
        response,
        settings,
        session_token=issued.token,
        csrf_token=issued.csrf_token,
        max_age_seconds=ttl_seconds,
    )
    response.headers["Cache-Control"] = "no-store"
    return _session_body(issued)


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> SessionResponse:
    if not auth_service.is_setup_complete(session):
        raise SetupRequiredError()
    issued = auth_service.login(
        session,
        username=payload.username,
        password=payload.password,
        request_id=get_request_id(),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    ttl_seconds = settings.session_ttl_hours * 3600
    set_session_cookies(
        response,
        settings,
        session_token=issued.token,
        csrf_token=issued.csrf_token,
        max_age_seconds=ttl_seconds,
    )
    response.headers["Cache-Control"] = "no-store"
    return _session_body(issued)


@router.get("/session", response_model=CurrentSessionResponse)
def current_session(authenticated: AuthenticatedDep) -> CurrentSessionResponse:
    return CurrentSessionResponse(
        user=_profile(authenticated.user),
        expires_at=authenticated.session.expires_at,
        session_created_at=authenticated.session.created_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    authenticated: WriteGuard,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
    session: DbSession,
) -> Response:
    auth_service.logout(
        session,
        authenticated.session,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    clear_session_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    response.headers["Cache-Control"] = "no-store"
    return response
