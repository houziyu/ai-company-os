"""Independent review probes. Failing assertions describe required behavior.

Run from backend with PYTHONPATH=. and TEST_ADMIN_DATABASE_URL pointing ONLY
to an isolated PostgreSQL. No real provider or credential is used.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import text

from app.domain.enums import ProviderKind
from app.providers.adapter import AdapterError, ProbeRequest, probe
from tests.conftest import setup_owner
from tests.test_organization import create_company, create_model, create_provider, openai_ok_body

pytest_plugins = ["tests.conftest"]


def observed(case: str, **facts: object) -> None:
    print("REVIEW " + json.dumps({"case": case, **facts}, ensure_ascii=False))


def prepare(client, app, monkeypatch, handler):
    headers = setup_owner(client)
    monkeypatch.setenv("REVIEW_PROVIDER_KEY", "review-canary-no-real-credential")
    app.state.model_centre_service._transport = httpx.MockTransport(handler)
    provider = create_provider(client, headers, secret_ref="REVIEW_PROVIDER_KEY").json()
    model = create_model(client, headers, provider["id"]).json()
    return headers, provider, model


def test_r1_provider_model_echo_must_not_persist_or_return_secret(client, app, engine, monkeypatch):
    def handler(request):
        body = openai_ok_body()
        body["model"] = request.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(200, json=body)

    headers, _, model = prepare(client, app, monkeypatch, handler)
    reply = client.post(f'/api/v1/models/{model["id"]}/probe', headers=headers)
    overview = client.get("/api/v1/models/centre", headers=headers)
    canary = "review-canary-no-real-credential"
    with engine.connect() as connection:
        stored = connection.scalar(text("SELECT resolved_name FROM provider_models"))
    leaked = canary in reply.text or canary in overview.text or stored == canary
    observed("R1", status=reply.status_code, in_probe_response=canary in reply.text,
             in_overview_response=canary in overview.text, in_database=stored == canary)
    assert not leaked, "provider-controlled model field leaks the actual Authorization credential"


def test_r2_probe_intent_must_be_committed_before_network_call(client, app, engine, monkeypatch):
    visible = []

    def handler(_request):
        with engine.connect() as connection:
            visible.append(connection.scalar(text("SELECT count(*) FROM model_probes")))
        return httpx.Response(200, json=openai_ok_body())

    headers, _, model = prepare(client, app, monkeypatch, handler)
    reply = client.post(f'/api/v1/models/{model["id"]}/probe', headers=headers)
    observed("R2", status=reply.status_code, committed_rows_during_provider_call=visible)
    assert visible == [1], "flush is not commit; process death loses all evidence of paid call"


def test_r3_provider_endpoint_change_must_invalidate_capability(client, app, monkeypatch):
    headers, provider, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    assert client.post(f'/api/v1/models/{model["id"]}/probe', headers=headers).status_code == 200
    reply = client.patch(f'/api/v1/providers/{provider["id"]}',
                         json={"baseUrl": "https://different-provider.example.test"}, headers=headers)
    assert reply.status_code == 200
    current = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
    observed("R3", base_url=reply.json()["baseUrl"],
             capability=current["supportsJsonOutput"], last_result=current["lastProbeOutcome"])
    assert current["supportsJsonOutput"] is None, "new endpoint has never been probed"


def test_r4_partial_usage_must_not_turn_unknown_completion_into_zero(client, app, monkeypatch):
    body = openai_ok_body()
    body["usage"] = {"prompt_tokens": 21}
    headers, _, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(200, json=body))
    reply = client.post(f'/api/v1/models/{model["id"]}/probe', headers=headers)
    usage = client.get("/api/v1/models/centre", headers=headers).json()["usage"]
    observed("R4", response_completion=reply.json()["completionTokens"], summary=usage)
    assert usage["completionTokens"] is None, "unreported completion tokens are unknown, not zero"


@pytest.mark.parametrize("invalid_field", ["model_length", "usage_overflow", "bad_envelope"])
def test_r5_untrusted_response_must_preserve_probe_and_usage(client, app, engine, monkeypatch,
                                                           invalid_field):
    body = openai_ok_body()
    if invalid_field == "model_length":
        body["model"] = "m" * 161
    elif invalid_field == "usage_overflow":
        body["usage"] = {"prompt_tokens": 2**31, "completion_tokens": 7}
    else:
        body["choices"] = []
    headers, _, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(200, json=body))
    reply = client.post(f'/api/v1/models/{model["id"]}/probe', headers=headers)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT (SELECT count(*) FROM model_probes),"
            " (SELECT count(*) FROM provider_usage_records),"
            " (SELECT max(prompt_tokens) FROM provider_usage_records)"
        )).one()
    observed("R5-" + invalid_field, status=reply.status_code, probes=rows[0], usage_rows=rows[1],
             prompt_tokens=rows[2])
    if invalid_field == "bad_envelope":
        assert rows[2] == 21, "response validation discarded separately valid reported usage"
    else:
        assert rows[0] == rows[1] == 1, "bad provider metadata rolled back all call evidence"


def test_r6_timeout_must_bound_total_elapsed_time():
    """Real loopback HTTP fault injection: each chunk arrives within read timeout."""
    body = json.dumps(openai_ok_body()).encode()
    stop = threading.Event()

    class DripHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            width = (len(body) + 9) // 10
            try:
                for offset in range(0, len(body), width):
                    self.wfile.write(body[offset:offset + width])
                    self.wfile.flush()
                    if stop.wait(0.24):
                        return
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = ProbeRequest(base_url=f"http://127.0.0.1:{server.server_port}", model_name="review",
                           kind=ProviderKind.OPENAI_COMPATIBLE, secret_ref="REVIEW_PROVIDER_KEY",
                           timeout_seconds=1, environ={"REVIEW_PROVIDER_KEY": "dummy"})
    started = time.perf_counter()
    error_code = None
    try:
        try:
            probe(request)
        except AdapterError as error:
            error_code = error.code.value
        elapsed = time.perf_counter() - started
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    observed("R6", configured_timeout_seconds=1, elapsed_seconds=round(elapsed, 3),
             error_code=error_code)
    assert error_code == "TIMEOUT" and elapsed < 1.6, "httpx inactivity timeout is not a total deadline"


def test_r7_two_stale_provider_forms_must_not_silently_overwrite(client):
    headers = setup_owner(client)
    original = create_provider(client, headers).json()
    pid = original["id"]
    first = client.patch(f"/api/v1/providers/{pid}",
                         json={"name": "new-name", "baseUrl": original["baseUrl"]}, headers=headers)
    stale = client.patch(f"/api/v1/providers/{pid}",
                         json={"name": original["name"], "baseUrl": "https://new.example.test"},
                         headers=headers)
    observed("R7", first_status=first.status_code, stale_status=stale.status_code,
             first_name=first.json()["name"], final_name=stale.json().get("name"))
    assert stale.status_code == 409, "row lock serializes writes but cannot detect a stale form"


def test_r8_profile_save_then_binding_failure_leaves_stale_retry(client):
    headers = setup_owner(client)
    assert create_company(client, headers).status_code == 201
    agent = client.get("/api/v1/organization", headers=headers).json()["agents"][0]
    payload = {"displayName": "changed-before-binding-fails", "expectedVersion": agent["version"]}
    saved = client.patch(f'/api/v1/agents/{agent["id"]}', json=payload, headers=headers)
    failed_binding = client.post(f'/api/v1/agents/{agent["id"]}/model-assignment',
                                 json={"modelId": 999999}, headers=headers)
    retry = client.patch(f'/api/v1/agents/{agent["id"]}', json=payload, headers=headers)
    observed("R8", profile_status=saved.status_code, binding_status=failed_binding.status_code,
             retry_status=retry.status_code, persisted_name=saved.json()["displayName"])
    # Exact HTTP sequence in OrganizationView.saveProfile: no reload on binding failure.
    assert retry.status_code != 409, "the UI reuses pre-save agent.version after partial success"


def test_r9_provider_notes_can_be_cleared(client):
    headers = setup_owner(client)
    provider = create_provider(client, headers).json()
    pid = provider["id"]
    assert client.patch(f"/api/v1/providers/{pid}", json={"notes": "old-note"},
                        headers=headers).status_code == 200
    reply = client.patch(f"/api/v1/providers/{pid}", json={"notes": None}, headers=headers)
    observed("R9", status=reply.status_code, saved_notes=reply.json()["notes"])
    assert reply.json()["notes"] is None, "the UI sends null for cleared text but service treats null as absent"
