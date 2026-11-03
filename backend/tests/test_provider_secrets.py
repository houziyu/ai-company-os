"""Provider credentials typed in the UI and stored encrypted (ADR-0006).

These tests cover the rules that make "the operator can paste a key, but the key
never comes back" checkable:

* a typed key is stored as an AES-GCM envelope, never as plaintext;
* the probe sends the decrypted key and nothing else;
* omitting the field preserves the stored key, and clearing it really clears it
  (no fallback to an environment variable of the same reference name);
* a replaced or missing server encryption key produces an explicit
  "无法解密" state and a locally refused probe, not a silent success or a
  confusing auth failure;
* legacy ``ENV`` providers from Phase 02 keep working.

Every test that inspects the database uses the real PostgreSQL instance from
``conftest`` — the check is "no plaintext anywhere in the schema", which a
mock cannot answer.
"""

from __future__ import annotations

import base64
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import build_engine
from app.main import BACKEND_ROOT, create_app
from app.security.secrets_cipher import SecretCipher
from tests.conftest import TEST_SECRET_ENCRYPTION_KEY, setup_owner
from tests.test_organization import (
    create_company,
    create_model,
    create_provider,
    openai_ok_body,
)

CANARY = "sk-adr0006-canary-0123456789abcdef"
OTHER_KEY = base64.b64encode(b"fedcba9876543210fedcba9876543210").decode()


def owner_headers(client: TestClient) -> dict[str, str]:
    return setup_owner(client)


def create_provider_with_key(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "直接填写密钥的供应商",
    secret_value: str | None = CANARY,
    secret_ref: str | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {
        "name": name,
        "kind": "DEEPSEEK",
        "baseUrl": "https://api.deepseek.com",
        "status": "ENABLED",
        "timeoutSeconds": 5.0,
    }
    if secret_value is not None:
        payload["secretValue"] = secret_value
    if secret_ref is not None:
        payload["secretRef"] = secret_ref
    return client.post("/api/v1/providers", json=payload, headers=headers)


@pytest.fixture()
def probe_app(settings):
    """Build an app with a scripted provider transport."""

    def build(handler, *, encryption_key: str | None = TEST_SECRET_ENCRYPTION_KEY):
        scoped = settings.model_copy(update={"secret_encryption_key": encryption_key})
        engine = build_engine(scoped)
        app = create_app(
            scoped,
            engine=engine,
            backend_root=BACKEND_ROOT,
            probe_transport=httpx.MockTransport(handler),
        )
        return app, engine

    return build


# ------------------------------------------------------------------- storage
def test_typed_key_is_stored_encrypted_and_never_returned(client, engine):
    headers = owner_headers(client)
    created = create_provider_with_key(client, headers)
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["secretSource"] == "DB"
    assert body["secretState"] == "CONFIGURED"
    assert body["secretConfigured"] is True
    # A DB row has no environment reference at all.
    assert body["secretRef"] is None

    assert CANARY not in created.text
    centre = client.get("/api/v1/models/centre", headers=headers)
    assert CANARY not in centre.text
    assert CANARY not in client.get("/api/v1/audit/events", headers=headers).text

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT secret_ciphertext, secret_ref, secret_source FROM providers")
        ).one()
        ciphertext, secret_ref, source = row
        assert secret_ref is None
        assert source == "DB"
        assert ciphertext.startswith("v1.")
        assert CANARY not in ciphertext
        # A second encryption of the same key must differ (random nonce), so the
        # database cannot be used to detect that two providers share a key.
        assert SecretCipher.from_setting(TEST_SECRET_ENCRYPTION_KEY).decrypt(ciphertext) == CANARY

        shared = SecretCipher.from_setting(TEST_SECRET_ENCRYPTION_KEY)
        assert shared.encrypt(CANARY) != shared.encrypt(CANARY)


def test_plaintext_appears_in_no_table_or_log(client, engine, caplog):
    headers = owner_headers(client)
    caplog.set_level(logging.DEBUG)
    provider = create_provider_with_key(client, headers).json()
    model = create_model(client, headers, provider["id"]).json()
    create_company(client, headers, model_id=model["id"])

    with engine.connect() as connection:
        tables = (
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
            )
            .scalars()
            .all()
        )
        for table in tables:
            rows = connection.execute(text(f'SELECT * FROM "{table}"')).all()  # noqa: S608
            for row in rows:
                for value in row:
                    assert CANARY not in str(value), table

    assert CANARY not in caplog.text


def test_key_requires_a_configured_server_encryption_key(probe_app):
    """Without a data key the save is refused, never downgraded to plaintext."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(500)

    app, engine = probe_app(handler, encryption_key=None)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            response = create_provider_with_key(client, headers)
            assert response.status_code == 503
            assert response.json()["code"] == "SERVICE_UNAVAILABLE"
            assert "SECRET_ENCRYPTION_KEY" in response.json()["message"]
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM providers")) == 0
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM providers WHERE secret_ciphertext IS NOT NULL")
                    )
                    == 0
                )
    finally:
        engine.dispose()


def test_malformed_encryption_key_fails_at_startup(settings):
    with pytest.raises(Exception, match="SECRET_ENCRYPTION_KEY"):
        SecretCipher.from_setting("not-base64!!")
    with pytest.raises(Exception, match="SECRET_ENCRYPTION_KEY"):
        SecretCipher.from_setting(base64.b64encode(b"too-short").decode())


# -------------------------------------------------------------------- probes
def test_probe_uses_the_decrypted_key_as_the_bearer_token(probe_app):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=openai_ok_body())

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider_with_key(client, headers, secret_value="sk-typed-key-123456")
            model = create_model(client, headers, provider.json()["id"]).json()

            response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["outcome"] == "SUCCESS"
            assert len(calls) == 1
            assert calls[0].headers["authorization"] == "Bearer sk-typed-key-123456"
            assert b"sk-typed-key-123456" not in calls[0].content
            assert "sk-typed-key-123456" not in response.text
    finally:
        engine.dispose()


def test_replaced_encryption_key_makes_the_probe_refuse_locally(probe_app, settings):
    """A key the server can no longer read is a config error, not a paid call."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json=openai_ok_body())

    save_app, save_engine = probe_app(handler)
    provider_id: int
    model_id: int
    try:
        with TestClient(save_app) as client:
            headers = owner_headers(client)
            provider = create_provider_with_key(client, headers).json()
            provider_id = provider["id"]
            model_id = create_model(client, headers, provider_id).json()["id"]
    finally:
        save_engine.dispose()

    # Same database, different data key: the stored ciphertext is unreadable.
    rotated_app, rotated_engine = probe_app(handler, encryption_key=OTHER_KEY)
    try:
        with TestClient(rotated_app) as client:
            # The owner already exists in this database, so log in rather than
            # running first-time setup again.
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "correct-horse-battery"},
                headers={"Origin": "http://testserver"},
            )
            assert login.status_code == 200, login.text
            headers = {
                "X-CSRF-Token": login.json()["csrfToken"],
                "Origin": "http://testserver",
            }
            centre = client.get("/api/v1/models/centre", headers=headers).json()
            stored = next(p for p in centre["providers"] if p["id"] == provider_id)
            assert stored["secretState"] == "UNDECRYPTABLE"
            assert stored["secretConfigured"] is False

            response = client.post(f"/api/v1/models/{model_id}/probe", headers=headers)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["outcome"] == "FAILURE"
            assert body["errorCode"] == "SECRET_UNDECRYPTABLE"
            assert body["callCount"] == 0
            assert "重新填写密钥" in body["message"]
            assert calls == []
            with rotated_engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM provider_usage_records")) == 0
    finally:
        rotated_engine.dispose()
    assert settings.secret_encryption_key == TEST_SECRET_ENCRYPTION_KEY


# ------------------------------------------------------------ update semantics
def test_omitting_secret_value_on_update_preserves_the_stored_key(client, engine):
    headers = owner_headers(client)
    provider = create_provider_with_key(client, headers).json()
    with engine.connect() as connection:
        before = connection.scalar(text("SELECT secret_ciphertext FROM providers"))

    renamed = client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"expectedVersion": provider["version"], "name": "改名后的供应商"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["secretState"] == "CONFIGURED"
    with engine.connect() as connection:
        after = connection.scalar(text("SELECT secret_ciphertext FROM providers"))
    # An edit form that never shows the key cannot erase it.
    assert after == before


def test_replacing_the_key_invalidates_probe_evidence(client, engine):
    headers = owner_headers(client)
    provider = create_provider_with_key(client, headers).json()
    model = create_model(client, headers, provider["id"]).json()
    with engine.connect() as connection:
        fingerprint_before = connection.scalar(
            text("SELECT config_fingerprint FROM providers WHERE id=:id"), {"id": provider["id"]}
        )

    replaced = client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={
            "expectedVersion": provider["version"],
            "changeSecretValue": True,
            "secretValue": "sk-a-brand-new-key-9876",
        },
        headers=headers,
    )
    assert replaced.status_code == 200, replaced.text
    with engine.connect() as connection:
        fingerprint_after = connection.scalar(
            text("SELECT config_fingerprint FROM providers WHERE id=:id"), {"id": provider["id"]}
        )
        ciphertext = connection.scalar(
            text("SELECT secret_ciphertext FROM providers WHERE id=:id"), {"id": provider["id"]}
        )
    # New ciphertext, new fingerprint: old capability evidence no longer applies.
    assert fingerprint_after != fingerprint_before
    assert (
        SecretCipher.from_setting(TEST_SECRET_ENCRYPTION_KEY).decrypt(ciphertext)
        == "sk-a-brand-new-key-9876"
    )
    centre = client.get("/api/v1/models/centre", headers=headers).json()
    stored_model = next(m for m in centre["models"] if m["id"] == model["id"])
    assert stored_model["supportsJsonOutput"] is None


def test_clearing_the_key_leaves_no_fallback_to_the_environment(client, engine, monkeypatch):
    """A cleared key must not silently start working from server configuration."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-value-must-not-be-used")
    headers = owner_headers(client)
    provider = create_provider_with_key(client, headers).json()

    cleared = client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"expectedVersion": provider["version"], "changeSecretValue": True},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["secretState"] == "NONE"
    assert body["secretConfigured"] is False
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT secret_ciphertext, secret_ref FROM providers WHERE id=:id"),
            {"id": provider["id"]},
        ).one()
    assert tuple(row) == (None, None)


def test_legacy_env_provider_still_probes_from_the_environment(client, monkeypatch):
    """Phase 02 rows keep working: the upgrade must not orphan them."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-env-value")
    headers = owner_headers(client)
    provider = create_provider(client, headers).json()
    assert provider["secretSource"] == "ENV"
    assert provider["secretRef"] == "DEEPSEEK_API_KEY"
    assert provider["secretState"] == "CONFIGURED"

    monkeypatch.delenv("DEEPSEEK_API_KEY")
    centre = client.get("/api/v1/models/centre", headers=headers).json()
    stored = next(p for p in centre["providers"] if p["id"] == provider["id"])
    # Distinct from "never configured": the fix is to set the variable.
    assert stored["secretState"] == "MISSING_ENV"
    assert stored["secretConfigured"] is False


def test_submitting_both_sources_is_rejected(client):
    headers = owner_headers(client)
    response = create_provider_with_key(
        client, headers, secret_value="sk-typed", secret_ref="DEEPSEEK_API_KEY"
    )
    assert response.status_code == 422
    assert {item["field"] for item in response.json()["fieldErrors"]} == {"secretValue"}


def test_control_characters_in_a_pasted_key_are_rejected(client):
    headers = owner_headers(client)
    response = create_provider_with_key(client, headers, secret_value="sk-with\nnewline")
    assert response.status_code == 422
    assert {item["field"] for item in response.json()["fieldErrors"]} == {"secretValue"}


def test_audit_detail_names_the_source_but_never_the_key(client, engine):
    headers = owner_headers(client)
    create_provider_with_key(client, headers, secret_value="sk-audit-canary-1234")
    # The audit read API redacts any detail containing "secret", which is the
    # desired outcome for the browser. The stored detail is asserted directly so
    # the test still proves what the operator can look up in the database.
    with engine.connect() as connection:
        detail = connection.scalar(
            text("SELECT detail FROM audit_events WHERE action='PROVIDER_CREATED'")
        )
    assert detail is not None
    assert "sk-audit-canary-1234" not in detail
    assert "secret_source=DB" in detail
    assert "secret_configured=True" in detail
    # Never anything derived from the key itself.
    assert "len=" not in detail and "prefix" not in detail
    # And the browser view stays redacted.
    assert "sk-audit-canary-1234" not in client.get("/api/v1/audit/events", headers=headers).text


# ---------------------------------------------------------------- adapter unit
def test_adapter_never_falls_back_to_the_environment():
    """An explicitly supplied blank credential is authoritative."""
    from app.providers.adapter import resolve_credential

    assert resolve_credential("", "DEEPSEEK_API_KEY", {"DEEPSEEK_API_KEY": "env"}) is None
    assert resolve_credential("  ", "DEEPSEEK_API_KEY", {"DEEPSEEK_API_KEY": "env"}) is None
    # Only when no literal value is given at all does the reference resolve.
    assert resolve_credential(None, "DEEPSEEK_API_KEY", {"DEEPSEEK_API_KEY": "env"}) == "env"
    assert resolve_credential("typed", "DEEPSEEK_API_KEY", {"DEEPSEEK_API_KEY": "env"}) == "typed"


def test_cipher_rejects_a_blob_from_a_different_key():
    first = SecretCipher.from_setting(TEST_SECRET_ENCRYPTION_KEY)
    second = SecretCipher.from_setting(OTHER_KEY)
    blob = first.encrypt("sk-secret-value")
    assert first.decrypt(blob) == "sk-secret-value"
    # A different key id is recognised as foreign without raising.
    assert second.can_decrypt(blob) is False
    assert second.decrypt(blob) is None
    # A tampered blob fails authentication rather than decrypting to garbage.
    parts = blob.split(".")
    tampered = ".".join([parts[0], parts[1], parts[2], parts[3][:-4] + "AAAA"])
    assert first.decrypt(tampered) is None
    assert first.decrypt("garbage") is None
    assert first.decrypt(None) is None
