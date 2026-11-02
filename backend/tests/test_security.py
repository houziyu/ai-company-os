"""Browser write protection and API access guards."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import setup_owner

VALID_PASSWORD = "correct-horse-battery"


def test_business_api_requires_session(client: TestClient) -> None:
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "NOT_AUTHENTICATED"
    assert body["requestId"]


def test_system_status_requires_session(client: TestClient) -> None:
    assert client.get("/api/v1/system/status").status_code == 401


def test_system_status_never_leaks_secrets(client: TestClient) -> None:
    setup_owner(client)
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    text = response.text.lower()
    for marker in ("password", "secret", "postgresql+psycopg", "devpass", "token"):
        assert marker not in text
    body = response.json()
    assert body["databaseStatus"] == "ok"
    assert body["workerStatus"] in {"ok", "stale", "missing"}


def test_cross_site_setup_write_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "displayName": "所有者",
            "password": VALID_PASSWORD,
            "passwordConfirm": VALID_PASSWORD,
        },
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


def test_cross_site_login_write_is_rejected(client: TestClient) -> None:
    setup_owner(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": VALID_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_cross_site_referer_is_rejected(client: TestClient) -> None:
    setup_owner(client)
    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Referer": "https://evil.example/attack.html",
            "X-CSRF-Token": client.cookies.get("acos_csrf") or "",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"


def test_forged_csrf_token_is_rejected(client: TestClient) -> None:
    setup_owner(client)
    response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "not-the-token", "Origin": "http://testserver"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


def test_csrf_token_from_another_session_is_rejected(database_url: str) -> None:
    """A CSRF token issued to one session must not authorise another."""
    from app.config import Settings
    from app.db.session import build_engine
    from app.main import create_app

    settings = Settings(
        database_url=database_url,
        app_env="test",
        public_base_url="http://testserver",
        password_min_length=12,
    )
    engine = build_engine(settings)
    app = create_app(settings, engine=engine, backend_root=_backend_root())
    try:
        with TestClient(app, base_url="http://testserver") as first:
            setup_owner(first, username="owner")
            first_session_cookie = first.cookies.get("acos_session")

        with TestClient(app, base_url="http://testserver") as second:
            second.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": VALID_PASSWORD},
                headers={"Origin": "http://testserver"},
            )
            # Pair session cookie #1 with CSRF token #2 -> must fail.
            response = second.post(
                "/api/v1/auth/logout",
                cookies={"acos_session": first_session_cookie},
                headers={
                    "X-CSRF-Token": second.cookies.get("acos_csrf") or "",
                    "Origin": "http://testserver",
                },
            )
            assert response.status_code == 403
            assert response.json()["code"] == "CSRF_VALIDATION_FAILED"
    finally:
        engine.dispose()


def test_unauthenticated_write_returns_401_before_csrf(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"})
    assert response.status_code == 401


def test_request_id_is_echoed_and_generated(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "abc123"})
    assert response.headers["x-request-id"] == "abc123"

    generated = client.get("/api/v1/health/live")
    assert len(generated.headers["x-request-id"]) == 32


def test_unknown_error_envelope_shape(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body) >= {"code", "message", "requestId", "fieldErrors"}
    assert body["code"] == "NOT_FOUND"


def test_openapi_available_in_test_env(client: TestClient) -> None:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/auth/setup" in schema["paths"]


def test_password_value_never_echoed_in_validation_error(client: TestClient) -> None:
    """Field names may appear; the submitted password value must not."""
    secret_value = "sup3r-secret-value-that-must-not-leak"
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "displayName": "",
            "password": secret_value,
            "passwordConfirm": secret_value,
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422
    assert secret_value not in response.text


def _backend_root():
    from app.main import BACKEND_ROOT

    return BACKEND_ROOT
