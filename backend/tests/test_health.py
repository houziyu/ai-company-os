"""Readiness/liveness and migration gating tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.config import Settings
from app.db.session import build_session_factory
from app.main import BACKEND_ROOT, create_app
from app.services.health_service import HealthService, expected_head_revision


def test_liveness_does_not_require_database(settings: Settings) -> None:
    broken = settings.model_copy(
        update={"database_url": "postgresql+psycopg://nouser:nopass@127.0.0.1:1/none"}
    )
    app = create_app(broken)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/live")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"
    finally:
        app.state.engine.dispose()


def test_readiness_ok_when_migrated(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    checks = {check["name"]: check["status"] for check in body["checks"]}
    assert checks["database"] == "ok"
    assert checks["migrations"] == "ok"


def test_readiness_fails_when_database_unreachable(settings: Settings) -> None:
    broken = settings.model_copy(
        update={"database_url": "postgresql+psycopg://nouser:nopass@127.0.0.1:1/none"}
    )
    app = create_app(broken)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/ready")
            assert response.status_code == 503
            body = response.json()
            assert body["ready"] is False
            checks = {check["name"]: check["status"] for check in body["checks"]}
            assert checks["database"] == "fail"
            assert checks["migrations"] == "skip"
    finally:
        app.state.engine.dispose()


def test_readiness_fails_when_migrations_not_applied(settings: Settings, engine: Engine) -> None:
    """A reachable but un-migrated database must not report ready."""
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE auth_sessions CASCADE"))
        connection.execute(text("ALTER TABLE worker_heartbeats RENAME TO wh_backup"))
        connection.execute(text("DROP TABLE audit_events CASCADE"))
        connection.execute(text("DROP TABLE login_attempts CASCADE"))
        connection.execute(text("DROP TABLE platform_state CASCADE"))
        connection.execute(text("DROP TABLE users CASCADE"))
        connection.execute(text("DROP TABLE alembic_version CASCADE"))

    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        checks = {check["name"]: check for check in body["checks"]}
        assert checks["database"]["status"] == "ok"
        assert checks["migrations"]["status"] == "fail"
        assert "尚未执行迁移" in checks["migrations"]["detail"]


def test_readiness_fails_on_revision_mismatch(settings: Settings, engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'deadbeef0000'"))
    app = create_app(settings, engine=engine)
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        checks = {check["name"]: check for check in response.json()["checks"]}
        assert checks["migrations"]["status"] == "fail"
        assert "不一致" in checks["migrations"]["detail"]


def test_worker_check_skipped_for_api_but_gating_for_worker(
    settings: Settings, engine: Engine
) -> None:
    service = HealthService(settings, build_session_factory(engine), BACKEND_ROOT)
    api_report = service.readiness(require_worker=False)
    assert api_report.ready is True
    worker_check = next(c for c in api_report.checks if c.name == "worker")
    assert worker_check.status == "skip"

    worker_report = service.readiness(require_worker=True)
    assert worker_report.ready is False


def test_stale_worker_heartbeat_reported(settings: Settings, engine: Engine, db_session) -> None:
    from app.repositories.auth_repository import WorkerHeartbeatRepository

    repo = WorkerHeartbeatRepository()
    repo.upsert(db_session, worker_name="worker-1", version="test")
    db_session.commit()

    service = HealthService(settings, build_session_factory(engine), BACKEND_ROOT)
    report = service.readiness(require_worker=True)
    assert report.ready is True, report.as_dict()

    # Move the heartbeat into the past and require it to be flagged stale.
    db_session.execute(
        text("UPDATE worker_heartbeats SET last_heartbeat_at = now() - interval '10 minutes'")
    )
    db_session.commit()
    report = service.readiness(require_worker=True)
    assert report.ready is False
    worker_check = next(c for c in report.checks if c.name == "worker")
    assert worker_check.status == "fail"
    assert "超过阈值" in (worker_check.detail or "")


def test_expected_head_revision_is_single(settings: Settings) -> None:
    head = expected_head_revision(BACKEND_ROOT)
    assert len(head) >= 8


@pytest.mark.parametrize("path", ["/api/v1/health/live", "/api/v1/health/ready"])
def test_health_endpoints_are_not_cached(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.headers.get("cache-control") == "no-store"


def test_unmigrated_database_returns_503_not_500(settings: Settings, engine: Engine) -> None:
    """A reachable but un-migrated database must produce an explicit 503.

    Regression test: this previously surfaced as a bare 500 INTERNAL_ERROR,
    which hides the actual operational cause from the operator.
    """
    with engine.begin() as connection:
        for table in (
            "auth_sessions",
            "audit_events",
            "login_attempts",
            "platform_state",
            "worker_heartbeats",
            "users",
            "alembic_version",
        ):
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))

    app = create_app(settings, engine=engine, backend_root=BACKEND_ROOT)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/setup",
            json={
                "username": "owner",
                "displayName": "所有者",
                "password": "correct-horse-battery",
                "passwordConfirm": "correct-horse-battery",
            },
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 503, response.text
        body = response.json()
        assert body["code"] == "DATABASE_NOT_READY"
        assert body["requestId"] not in ("-", "")


def test_error_payload_never_returns_placeholder_request_id(settings: Settings) -> None:
    from app.api.errors import error_payload

    payload = error_payload("X", "y")
    assert payload["requestId"] not in ("-", "")
    assert len(str(payload["requestId"])) == 32
