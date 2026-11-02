"""Owner setup, login, logout and session tests against real PostgreSQL."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import setup_owner

VALID_PASSWORD = "correct-horse-battery"


def test_setup_status_reports_missing_owner(client: TestClient) -> None:
    response = client.get("/api/v1/auth/setup-status")
    assert response.status_code == 200
    body = response.json()
    assert body["setupComplete"] is False
    assert body["passwordMinLength"] == 12


def test_first_setup_creates_owner_and_session(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "displayName": "所有者",
            "password": VALID_PASSWORD,
            "passwordConfirm": VALID_PASSWORD,
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["username"] == "owner"
    assert body["user"]["role"] == "OWNER"
    assert body["csrfToken"]

    cookies = response.cookies
    assert cookies.get("acos_session")
    assert cookies.get("acos_csrf")
    assert "httponly" in response.headers["set-cookie"].lower()

    current = client.get("/api/v1/auth/session")
    assert current.status_code == 200
    assert current.json()["user"]["displayName"] == "所有者"


def test_setup_is_rejected_after_completion(client: TestClient) -> None:
    setup_owner(client)
    second = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "intruder",
            "displayName": "入侵者",
            "password": VALID_PASSWORD,
            "passwordConfirm": VALID_PASSWORD,
        },
        headers={"Origin": "http://testserver"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "SETUP_ALREADY_COMPLETED"


def test_concurrent_setup_only_succeeds_once(database_url: str) -> None:
    """Two racing setup requests must produce exactly one owner.

    Uses two separate app instances sharing one database so the lock is a real
    PostgreSQL row lock rather than a shared object.
    """
    from app.config import Settings
    from app.db.session import build_engine
    from app.main import create_app

    settings = Settings(
        database_url=database_url,
        app_env="test",
        public_base_url="http://testserver",
        password_min_length=12,
    )
    payload = {
        "username": "owner",
        "displayName": "所有者",
        "password": VALID_PASSWORD,
        "passwordConfirm": VALID_PASSWORD,
    }

    results: list[int] = []
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        engine = build_engine(settings)
        app = create_app(settings, engine=engine)
        try:
            with TestClient(app, base_url="http://testserver") as client:
                barrier.wait(timeout=20)
                response = client.post(
                    "/api/v1/auth/setup",
                    json=payload,
                    headers={"Origin": "http://testserver"},
                )
                results.append(response.status_code)
                if response.status_code not in (201, 409):
                    errors.append(response.text)
        except Exception as exc:  # pragma: no cover - surfaced via errors list
            errors.append(repr(exc))
        finally:
            engine.dispose()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert sorted(results) == [201, 409], results

    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            count = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
            owner = connection.execute(
                text("SELECT owner_user_id FROM platform_state WHERE id = 1")
            ).scalar_one()
        assert count == 1
        assert owner is not None
    finally:
        engine.dispose()


def test_setup_password_confirmation_mismatch(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "displayName": "所有者",
            "password": VALID_PASSWORD,
            "passwordConfirm": VALID_PASSWORD + "x",
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == "passwordConfirm"


@pytest.mark.parametrize(
    ("password", "expected_fragment"),
    [
        ("short", "至少需要 12 个字符"),
        ("            ", "空白"),
        ("1234567890123", "不能全是数字"),
        ("aaaaaaaaaaaa", "字符种类太少"),
    ],
)
def test_setup_password_policy(client: TestClient, password: str, expected_fragment: str) -> None:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "displayName": "所有者",
            "password": password,
            "passwordConfirm": password,
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422, response.text
    messages = [item["message"] for item in response.json()["fieldErrors"]]
    assert any(expected_fragment in message for message in messages), messages


def test_weak_setup_does_not_create_user(client: TestClient, db_session) -> None:
    client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "displayName": "所有者",
            "password": "short",
            "passwordConfirm": "short",
        },
        headers={"Origin": "http://testserver"},
    )
    count = db_session.execute(text("SELECT count(*) FROM users")).scalar_one()
    assert count == 0


def test_login_rejects_wrong_password_without_revealing_account(client: TestClient) -> None:
    setup_owner(client)
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": ""})

    wrong = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "wrong-password-here"},
        headers={"Origin": "http://testserver"},
    )
    missing = client.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "wrong-password-here"},
        headers={"Origin": "http://testserver"},
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["code"] == missing.json()["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["message"] == missing.json()["message"]


def test_login_before_setup_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": VALID_PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SETUP_REQUIRED"


def test_login_success_sets_cookies_and_returns_session(client: TestClient) -> None:
    setup_owner(client)
    headers = {"X-CSRF-Token": client.cookies.get("acos_csrf") or "", "Origin": "http://testserver"}
    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 204

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": VALID_PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["username"] == "owner"
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200


def test_logout_invalidates_old_session(client: TestClient, db_session) -> None:
    csrf_headers = setup_owner(client)
    old_cookie = client.cookies.get("acos_session")
    assert old_cookie

    logout = client.post("/api/v1/auth/logout", headers=csrf_headers)
    assert logout.status_code == 204

    # Replaying the captured cookie must fail: the server-side record is revoked.
    replay = client.get("/api/v1/auth/session", cookies={"acos_session": old_cookie})
    assert replay.status_code == 401
    assert replay.json()["code"] == "NOT_AUTHENTICATED"

    revoked = db_session.execute(
        text("SELECT revoke_reason FROM auth_sessions ORDER BY id DESC LIMIT 1")
    ).scalar_one()
    assert revoked == "LOGOUT"


def test_expired_session_is_rejected(client: TestClient, db_session) -> None:
    setup_owner(client)
    db_session.execute(
        text(
            "UPDATE auth_sessions SET created_at = now() - interval '3 hours',"
            " expires_at = now() - interval '1 hour'"
        )
    )
    db_session.commit()

    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401


def test_logout_requires_csrf_header(client: TestClient) -> None:
    setup_owner(client)
    response = client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"
    # The session must still be usable after the rejected logout.
    assert client.get("/api/v1/auth/session").status_code == 200


def test_login_lockout_after_repeated_failures(client: TestClient, db_session) -> None:
    setup_owner(client)
    csrf = client.cookies.get("acos_csrf") or ""
    client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )

    for _ in range(3):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "definitely-wrong"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "definitely-wrong"},
        headers={"Origin": "http://testserver"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "LOGIN_BLOCKED"

    failures = db_session.execute(
        text("SELECT count(*) FROM login_attempts WHERE succeeded = false")
    ).scalar_one()
    assert failures == 3


def test_successful_login_clears_failure_counter(client: TestClient, db_session) -> None:
    setup_owner(client)
    csrf = client.cookies.get("acos_csrf") or ""
    client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "wrong-one-here"},
        headers={"Origin": "http://testserver"},
    )
    ok = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": VALID_PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    assert ok.status_code == 200
    cleared = db_session.execute(text("SELECT count(*) FROM login_attempts")).scalar_one()
    assert cleared == 0


def test_password_is_not_stored_in_plaintext(client: TestClient, db_session) -> None:
    setup_owner(client)
    stored = db_session.execute(text("SELECT password_hash FROM users")).scalar_one()
    assert VALID_PASSWORD not in stored
    assert stored.startswith("$argon2id$")


def test_session_token_is_not_stored_in_plaintext(client: TestClient, db_session) -> None:
    setup_owner(client)
    raw = client.cookies.get("acos_session")
    assert raw
    hashes = db_session.execute(text("SELECT token_hash FROM auth_sessions")).scalars().all()
    assert raw not in hashes
    assert all(len(value) == 64 for value in hashes)


def test_audit_events_record_auth_lifecycle(client: TestClient, db_session) -> None:
    csrf_headers = setup_owner(client)
    client.post("/api/v1/auth/logout", headers=csrf_headers)
    client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "nope-nope-nope"},
        headers={"Origin": "http://testserver"},
    )
    actions = set(db_session.execute(text("SELECT action FROM audit_events")).scalars().all())
    assert {"OWNER_SETUP_COMPLETED", "LOGOUT", "LOGIN_FAILED"} <= actions


def test_audit_detail_never_contains_password(client: TestClient, db_session) -> None:
    setup_owner(client)
    details = db_session.execute(text("SELECT detail FROM audit_events")).scalars().all()
    for detail in details:
        if detail:
            assert VALID_PASSWORD not in detail
