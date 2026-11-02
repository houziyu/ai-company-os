"""Phase 01 review regressions, with real migrated PostgreSQL and commit faults."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import build_engine
from app.main import create_app
from app.repositories.auth_repository import PlatformRepository
from tests.conftest import setup_owner


class FailingCommitSession(Session):
    def commit(self):
        raise OperationalError("COMMIT", None, RuntimeError("injected commit failure"))


@pytest.mark.parametrize("operation", ["setup", "login", "logout"])
def test_commit_failure_never_reports_success(operation, client, app, engine):
    headers = {"Origin": "http://testserver"}
    if operation != "setup":
        headers = setup_owner(client)
    original_factory = app.state.session_factory
    app.state.session_factory = sessionmaker(bind=engine, class_=FailingCommitSession)
    payload = {
        "username": "owner",
        "displayName": "所有者",
        "password": "correct-horse-battery",
        "passwordConfirm": "correct-horse-battery",
    }
    response = client.post(
        f"/api/v1/auth/{operation}",
        headers=headers,
        json=payload if operation != "logout" else None,
    )
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_NOT_READY"
    assert "set-cookie" not in response.headers
    with engine.connect() as connection:
        users = connection.scalar(text("SELECT count(*) FROM users"))
        sessions = connection.scalar(text("SELECT count(*) FROM auth_sessions"))
        audits = connection.scalar(text("SELECT count(*) FROM audit_events"))
        revoked = connection.scalar(
            text("SELECT count(*) FROM auth_sessions WHERE revoked_at IS NOT NULL")
        )
        owner = connection.scalar(text("SELECT owner_user_id FROM platform_state"))
    assert (users, sessions, audits, revoked) == (
        (0, 0, 0, 0) if operation == "setup" else (1, 1, 1, 0)
    )
    assert (owner is None) == (operation == "setup")
    app.state.session_factory = original_factory
    if operation == "logout":
        # Failure did not revoke the original cookie. A retry must really revoke it.
        assert client.get("/api/v1/auth/session").status_code == 200
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401


@pytest.mark.parametrize("multiple_apps", [False, True])
@pytest.mark.parametrize("username", ["owner", "unknown-owner"])
def test_concurrent_login_respects_threshold(
    client, app, settings, engine, monkeypatch, multiple_apps, username
):
    setup_owner(client)
    payload = {"username": username, "password": "wrong-password-only"}
    headers = {"Origin": "http://testserver"}
    for _ in range(2):
        assert client.post("/api/v1/auth/login", json=payload, headers=headers).status_code == 401

    barrier = Barrier(4)
    original = PlatformRepository.lock_state

    def synchronized_lock(self, session):
        # Schedule contenders BEFORE the critical section. A barrier after
        # count_failures_since would deadlock correctly serialized requests.
        barrier.wait(timeout=20)
        return original(self, session)

    monkeypatch.setattr(PlatformRepository, "lock_state", synchronized_lock)

    def attempt(_):
        other_engine = build_engine(settings) if multiple_apps else None
        other_app = create_app(settings, engine=other_engine) if multiple_apps else app
        try:
            with TestClient(other_app) as other:
                return other.post("/api/v1/auth/login", json=payload, headers=headers).status_code
        finally:
            if other_engine is not None:
                other_engine.dispose()

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))
    assert statuses.count(401) == 1 and statuses.count(429) == 3, statuses
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM login_attempts")) == 3
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action='LOGIN_BLOCKED'")
            )
            == 3
        )
        assert connection.scalar(text("SELECT failed_login_count FROM users")) == (
            3 if username == "owner" else 0
        )


def test_expiration_persists_once_under_concurrent_requests(client, app, engine):
    setup_owner(client)
    cookie = client.cookies.get("acos_session")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE auth_sessions SET created_at=now()-interval '3 hours', expires_at=now()-interval '1 hour'"
            )
        )
    barrier = Barrier(4)

    def attempt(_):
        with TestClient(app) as other:
            other.cookies.set("acos_session", cookie)
            barrier.wait(timeout=20)
            return other.get("/api/v1/auth/session").status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(attempt, range(4))) == [401] * 4
    assert client.get("/api/v1/auth/session").status_code == 401
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revoke_reason FROM auth_sessions")) == "EXPIRED"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action='SESSION_EXPIRED'")
            )
            == 1
        )


def test_expiration_commit_failure_returns_database_error(client, app, engine):
    setup_owner(client)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE auth_sessions SET created_at=now()-interval '3 hours', expires_at=now()-interval '1 hour'"
            )
        )
    app.state.session_factory = sessionmaker(bind=engine, class_=FailingCommitSession)
    assert client.get("/api/v1/auth/session").status_code == 503
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revoke_reason FROM auth_sessions")) is None
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action='SESSION_EXPIRED'")
            )
            == 0
        )
