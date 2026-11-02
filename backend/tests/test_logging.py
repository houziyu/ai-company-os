"""Credential confidentiality at default and supported DEBUG log levels."""

import io
import json
import logging

import pytest
from sqlalchemy import text

from app.logging import JsonFormatter, configure_logging
from tests.conftest import setup_owner


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_logs_exclude_actual_credentials(client, engine, level):
    configure_logging(level)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        headers = setup_owner(client)
        raw_session = client.cookies.get("acos_session")
        raw_csrf = client.cookies.get("acos_csrf")
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert (
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": "owner",
                    "password": "correct-horse-battery",
                },
                headers={"Origin": "http://testserver"},
            ).status_code
            == 200
        )
        with engine.connect() as connection:
            secrets = list(
                connection.execute(
                    text(
                        "SELECT password_hash, token_hash, csrf_token_hash FROM users CROSS JOIN auth_sessions"
                    )
                ).all()
            )
        output = stream.getvalue()
        leaked = any(value in output for row in secrets for value in row)
        assert not leaked, "credential hashes leaked"
        assert raw_session not in output and raw_csrf not in output
        assert "correct-horse-battery" not in output
        assert "'token_hash':" not in output and "$argon2id$" not in output
        records = [json.loads(line) for line in output.splitlines()]
        assert any(record.get("status") == 201 for record in records)
        assert engine.hide_parameters is True
    finally:
        root.removeHandler(handler)


def test_formatter_rejects_arbitrary_extras_messages_and_exception_values():
    sentinel = "private-credential-sentinel"
    logger = logging.getLogger("app.test")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    try:
        try:
            raise RuntimeError(sentinel)
        except RuntimeError:
            logger.exception(
                "unhandled exception", extra={"password": sentinel, "cookie": sentinel}
            )
        logger.warning(
            "third-party SQL parameters %s", sentinel, extra={"csrf_token_hash": sentinel}
        )
        output = stream.getvalue()
        assert sentinel not in output
        first = json.loads(output.splitlines()[0])
        assert first["exception_type"] == "RuntimeError"
        assert first["traceback"]
        assert "password" not in first and "cookie" not in first
    finally:
        logger.removeHandler(handler)
