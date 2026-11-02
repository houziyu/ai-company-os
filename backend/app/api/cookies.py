"""Cookie helpers shared by auth routes."""

from __future__ import annotations

from fastapi import Response

from app.config import Settings

SESSION_PATH = "/"
CSRF_PATH = "/"


def set_session_cookies(
    response: Response,
    settings: Settings,
    *,
    session_token: str,
    csrf_token: str,
    max_age_seconds: int,
) -> None:
    """Write the session cookie (HttpOnly) and the CSRF cookie (readable).

    ``SameSite=Lax`` blocks the cookie on cross-site POSTs; ``HttpOnly`` keeps
    the session secret away from JavaScript. The CSRF cookie must be readable so
    the SPA can echo it in a header — that is the double-submit pattern and is
    only meaningful together with the server-side hash comparison.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=max_age_seconds,
        path=SESSION_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age_seconds,
        path=CSRF_PATH,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=SESSION_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path=CSRF_PATH,
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )
