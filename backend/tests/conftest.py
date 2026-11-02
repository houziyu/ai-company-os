"""Test fixtures.

Every test module gets its own PostgreSQL database created from a template so
tests can run in isolation without SQLite (SQLite would not exercise row locks,
check constraints or transactional DDL).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import build_engine, build_session_factory
from app.main import BACKEND_ROOT, create_app

ADMIN_DATABASE_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://acos:devpass@127.0.0.1:55432/postgres",
)


def _admin_engine() -> Engine:
    return create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def database_template() -> Iterator[str]:
    """Create one migrated template database for the whole session."""
    name = f"acos_test_template_{uuid.uuid4().hex[:8]}"
    engine = _admin_engine()
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"

    os.environ["DATABASE_URL"] = url
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")

    yield name

    with engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


@pytest.fixture()
def database_url(database_template: str) -> Iterator[str]:
    """Clone the template into a per-test database (fast and isolated)."""
    name = f"acos_test_{uuid.uuid4().hex[:10]}"
    engine = _admin_engine()
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{database_template}"'))
    url = ADMIN_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    try:
        yield url
    finally:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        engine.dispose()


@pytest.fixture()
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        app_env="test",
        public_base_url="http://testserver",
        extra_allowed_origins=["http://localhost:5173"],
        enable_api_docs=True,
        cookie_secure=False,
        session_ttl_hours=1,
        password_min_length=12,
        lockout_threshold=3,
        lockout_minutes=5,
    )


@pytest.fixture()
def engine(settings: Settings) -> Iterator[Engine]:
    engine = build_engine(settings)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(engine: Engine):
    return build_session_factory(engine)


@pytest.fixture()
def db_session(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def app(settings: Settings, engine: Engine):
    return create_app(settings, engine=engine, backend_root=BACKEND_ROOT)


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


def setup_owner(
    client: TestClient,
    *,
    username: str = "owner",
    display_name: str = "所有者",
    password: str = "correct-horse-battery",  # noqa: S107 - test fixture
) -> dict[str, str]:
    """Run first-time setup and return CSRF headers for later writes."""
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": username,
            "displayName": display_name,
            "password": password,
            "passwordConfirm": password,
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text
    csrf_token = response.json()["csrfToken"]
    return {"X-CSRF-Token": csrf_token, "Origin": "http://testserver"}
