"""Review probes: assert intended behavior against isolated PostgreSQL only.

Run from backend with TEST_ADMIN_DATABASE_URL pointing to a disposable DB:
  python -m pytest ../docs/acceptance/evidence/phase-01-review/backend_probes.py -q
These assertions intentionally fail on the reviewed implementation.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.auth_repository import LoginAttemptRepository
from tests.conftest import setup_owner

pytest_plugins = ["tests.conftest"]


def test_default_logs_exclude_credential_hashes(client, caplog):
    setup_owner(client)
    output = "\n".join(record.getMessage() for record in caplog.records)
    leaked = {
        "password_hash": "$argon2id$" in output,
        "session_hash_parameters": "'token_hash':" in output,
        "csrf_hash_parameters": "'csrf_token_hash':" in output,
    }
    assert not any(leaked.values()), leaked


def test_expiration_persists_revocation_and_audit(client, db_session):
    setup_owner(client)
    db_session.execute(text(
        "UPDATE auth_sessions SET created_at=now()-interval '3 hours', "
        "expires_at=now()-interval '1 hour'"
    ))
    db_session.commit()
    assert client.get("/api/v1/auth/session").status_code == 401
    reason = db_session.execute(text("SELECT revoke_reason FROM auth_sessions")).scalar_one()
    count = db_session.execute(text(
        "SELECT count(*) FROM audit_events WHERE action='SESSION_EXPIRED'"
    )).scalar_one()
    assert (reason, count) == ("EXPIRED", 1), {"reason": reason, "audit_count": count}


def test_failed_commit_does_not_report_setup_success(app, engine):
    class FailingCommitSession(Session):
        def commit(self):
            raise OperationalError("COMMIT", None, RuntimeError("review injected commit failure"))

    app.state.session_factory = sessionmaker(bind=engine, class_=FailingCommitSession)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/auth/setup", json={
            "username": "owner", "displayName": "Review owner",
            "password": "review-password-only", "passwordConfirm": "review-password-only",
        }, headers={"Origin": "http://testserver"})
    with engine.connect() as connection:
        users = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
    assert response.status_code >= 500, {
        "http_status": response.status_code,
        "persisted_users": users,
        "issued_session_cookie": "acos_session" in response.cookies,
    }


def test_single_api_concurrent_login_respects_threshold(client, app, monkeypatch, db_session):
    setup_owner(client)
    # Place account one failure below its configured limit of 3.
    for _ in range(2):
        assert client.post("/api/v1/auth/login", json={
            "username": "owner", "password": "wrong-password-only",
        }, headers={"Origin": "http://testserver"}).status_code == 401

    original = LoginAttemptRepository.count_failures_since
    barrier = Barrier(4)

    def synchronized_count(self, session, username, since):
        count = original(self, session, username, since)
        # Only synchronize scheduling; real count, password hash, DB and policy remain intact.
        barrier.wait(timeout=20)
        return count

    monkeypatch.setattr(LoginAttemptRepository, "count_failures_since", synchronized_count)

    def attempt(_):
        with TestClient(app) as other:
            return other.post("/api/v1/auth/login", json={
                "username": "owner", "password": "wrong-password-only",
            }, headers={"Origin": "http://testserver"}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))
    count = db_session.execute(text("SELECT count(*) FROM login_attempts")).scalar_one()
    assert statuses.count(401) == 1 and statuses.count(429) == 3, {
        "statuses": statuses, "persisted_failures": count, "threshold": 3,
    }
