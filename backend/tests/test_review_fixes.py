"""Phase 02 review regressions (R1–R9).

These are the independent review's probes, promoted to permanent tests. The
original probes and their failing output stay in
``docs/acceptance/evidence/phase-02-review/``; nothing there was edited.

Four probes needed their *request shape* updated because the fix changed the
contract, not their assertions:

* R3 and R9 send ``expectedVersion`` now, because every provider update requires
  the version it was based on (R7's fix). The assertions are unchanged: capability
  must be invalidated, and clearing notes must clear them.
* R7 additionally asserts the *positive* path (a fresh form still succeeds) and
  that a missing version is refused rather than silently treated as fresh.
* R5 keeps all three malformed-input cases and adds the requirement that the
  audit trail still records the call.

Everything runs against a real migrated PostgreSQL. Provider calls use
``httpx.MockTransport``; R6 uses a real loopback HTTP server that drips bytes.
No real credential and no external provider is involved.
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
from app.providers.adapter import (
    AdapterError,
    ProbeRequest,
    probe,
    sanitize_provider_text,
)
from tests.conftest import setup_owner
from tests.test_organization import (
    create_company,
    create_model,
    create_provider,
    openai_ok_body,
)

CANARY = "review-canary-no-real-credential"
SECRET_REF = "REVIEW_PROVIDER_KEY"


def observed(case: str, **facts: object) -> None:
    print("REVIEW " + json.dumps({"case": case, **facts}, ensure_ascii=False))


def prepare(client, app, monkeypatch, handler):
    headers = setup_owner(client)
    monkeypatch.setenv(SECRET_REF, CANARY)
    app.state.model_centre_service._transport = httpx.MockTransport(handler)
    provider = create_provider(client, headers, secret_ref=SECRET_REF).json()
    model = create_model(client, headers, provider["id"]).json()
    return headers, provider, model


def provider_version(client, headers, provider_id: int) -> int:
    """Read the current version, as the UI does before rendering a form."""
    centre = client.get("/api/v1/models/centre", headers=headers).json()
    return next(p for p in centre["providers"] if p["id"] == provider_id)["version"]


# ------------------------------------------------------------------------ R1
def test_r1_provider_model_echo_must_not_persist_or_return_secret(client, app, engine, monkeypatch):
    """A provider that echoes the Authorization value in `model` must not leak it.

    The value is untrusted input: it must not reach the browser or the database.
    The call itself is still recorded, so the operator keeps the evidence.
    """

    def handler(request):
        body = openai_ok_body()
        body["model"] = request.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(200, json=body)

    headers, _, model = prepare(client, app, monkeypatch, handler)
    reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    overview = client.get("/api/v1/models/centre", headers=headers)
    with engine.connect() as connection:
        stored = connection.scalar(text("SELECT resolved_name FROM provider_models"))
        probe_name = connection.scalar(text("SELECT resolved_model_name FROM model_probes"))
    observed(
        "R1",
        status=reply.status_code,
        in_probe_response=CANARY in reply.text,
        in_overview_response=CANARY in overview.text,
        in_database=stored == CANARY,
    )
    assert CANARY not in reply.text
    assert CANARY not in overview.text
    assert stored is None and probe_name is None
    # The call is still evidenced: dropping metadata must not hide the probe.
    assert reply.json()["outcome"] == "SUCCESS"
    assert reply.json()["callCount"] == 1
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM model_probes")) == 1


def test_r1_partial_credential_echo_is_also_dropped():
    """A truncated echo is still a leak; screening compares a character window."""
    secret = "sk-live-0123456789abcdefghij"
    assert sanitize_provider_text(secret, secret=secret, max_length=160) is None
    assert sanitize_provider_text(f"model-{secret[3:20]}", secret=secret, max_length=160) is None
    # A legitimate model name is untouched.
    assert (
        sanitize_provider_text("deepseek-v4.1-flash", secret=secret, max_length=160)
        == "deepseek-v4.1-flash"
    )


# ------------------------------------------------------------------------ R2
def test_r2_probe_intent_is_committed_before_the_network_call(client, app, engine, monkeypatch):
    """The intent row must be visible to another connection during the call.

    A flush inside the request transaction is not enough: process death would
    erase the fact that a paid request may have happened.
    """
    visible: list[tuple[int, str | None, int | None]] = []

    def handler(_request):
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT count(*), min(outcome), min(call_count) FROM model_probes")
            ).one()
        visible.append((row[0], row[1], row[2]))
        return httpx.Response(200, json=openai_ok_body())

    headers, _, model = prepare(client, app, monkeypatch, handler)
    reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    observed("R2", status=reply.status_code, committed_rows_during_provider_call=visible)
    assert len(visible) == 1
    count, outcome, call_count = visible[0]
    assert count == 1, "the intent must be committed before the provider call"
    assert outcome == "PENDING", "the pre-call row must be PENDING, not a claimed result"
    assert call_count == 1, "the attempt is a call until proven otherwise"


def test_r2_interrupted_probe_stays_pending_and_is_read_as_unknown(
    client, app, engine, monkeypatch
):
    """If the result is never written, the row must not claim success or zero cost."""

    class SimulatedProcessStop(Exception):
        """Stands in for the process dying between the call and the result."""

    def handler(_request):
        # Not an AdapterError, so no result and no usage are recorded; the
        # committed PENDING intent is all that remains.
        raise SimulatedProcessStop("simulated process stop")

    headers, _, model = prepare(client, app, monkeypatch, handler)
    with pytest.raises(SimulatedProcessStop):
        client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT outcome, call_count, finished_at, error_code,"
                " (SELECT count(*) FROM provider_usage_records)"
                " FROM model_probes"
            )
        ).one()
    observed(
        "R2-interrupted",
        outcome=row[0],
        call_count=row[1],
        finished_at=row[2],
        error_code=row[3],
        usage_rows=row[4],
    )
    assert row[0] == "PENDING"
    assert row[1] == 1
    assert row[2] is None, "no result means no completion timestamp"
    assert row[3] is None
    # Usage genuinely unknown: no row is invented, and certainly not a zero row.
    assert row[4] == 0

    # The UI must present it as unknown rather than as a success.
    centre = client.get("/api/v1/models/centre", headers=headers).json()
    pending = [entry for entry in centre["recentProbes"] if entry["outcome"] == "PENDING"]
    assert len(pending) == 1
    assert pending[0]["usageKnown"] is False
    # And the summary must count it, so an interrupted call is never invisible.
    assert centre["usage"]["pendingProbes"] == 1
    assert centre["usage"]["measuredCalls"] == 0


# ------------------------------------------------------------------------ R3
def test_r3_provider_endpoint_change_must_invalidate_capability(client, app, monkeypatch):
    headers, provider, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    first = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    assert first.status_code == 200
    assert first.json()["outcome"] == "SUCCESS"

    version = provider_version(client, headers, provider["id"])
    reply = client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"baseUrl": "https://different-provider.example.test", "expectedVersion": version},
        headers=headers,
    )
    assert reply.status_code == 200, reply.text
    current = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
    observed(
        "R3",
        base_url=reply.json()["baseUrl"],
        capability=current["supportsJsonOutput"],
        last_result=current["lastProbeOutcome"],
        stale=current["probeEvidenceStale"],
    )
    assert current["supportsJsonOutput"] is None, "new endpoint has never been probed"
    assert current["lastProbeOutcome"] is None
    # The old probe row is still there: invalidation clears current state only.
    with client.app.state.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM model_probes")) == 1


def test_r3_renaming_a_provider_keeps_its_probe_evidence(client, app, monkeypatch):
    """Only call-affecting fields invalidate evidence: a rename must not."""
    headers, provider, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    version = provider_version(client, headers, provider["id"])
    renamed = client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"name": "renamed-provider", "expectedVersion": version},
        headers=headers,
    )
    assert renamed.status_code == 200
    current = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
    assert current["supportsJsonOutput"] is True
    assert current["probeEvidenceStale"] is False


# ------------------------------------------------------------------------ R4
def test_r4_partial_usage_does_not_turn_unknown_completion_into_zero(client, app, monkeypatch):
    body = openai_ok_body()
    body["usage"] = {"prompt_tokens": 21}
    headers, _, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(200, json=body))
    reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    usage = client.get("/api/v1/models/centre", headers=headers).json()["usage"]
    observed("R4", response_completion=reply.json()["completionTokens"], summary=usage)
    assert reply.json()["completionTokens"] is None
    assert reply.json()["usageKnown"] is False
    assert usage["completionTokens"] is None, "unreported completion tokens are unknown, not zero"
    assert usage["promptTokens"] == 21
    assert usage["measuredCalls"] == 0
    assert usage["partiallyMeasuredCalls"] == 1
    assert usage["unmeasuredCalls"] == 0
    assert usage["completionTokensUnreportedCalls"] == 1
    assert usage["promptTokensUnreportedCalls"] == 0

    with client.app.state.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT usage_known, prompt_tokens_known, completion_tokens_known"
                " FROM provider_usage_records"
            )
        ).one()
    assert tuple(row) == (False, True, False)


def test_r4_fully_reported_usage_counts_as_measured(client, app, monkeypatch):
    headers, _, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    usage = client.get("/api/v1/models/centre", headers=headers).json()["usage"]
    assert usage["measuredCalls"] == 1
    assert usage["partiallyMeasuredCalls"] == 0
    assert usage["promptTokens"] == 21
    assert usage["completionTokens"] == 7


# ------------------------------------------------------------------------ R5
@pytest.mark.parametrize("invalid_field", ["model_length", "usage_overflow", "bad_envelope"])
def test_r5_untrusted_response_preserves_probe_and_usage(
    client, app, engine, monkeypatch, invalid_field
):
    body = openai_ok_body()
    if invalid_field == "model_length":
        body["model"] = "m" * 161
    elif invalid_field == "usage_overflow":
        body["usage"] = {"prompt_tokens": 2**31, "completion_tokens": 7}
    else:
        body["choices"] = []
    headers, _, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(200, json=body))
    reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM model_probes),"
                " (SELECT count(*) FROM provider_usage_records),"
                " (SELECT max(prompt_tokens) FROM provider_usage_records),"
                " (SELECT count(*) FROM audit_events WHERE action LIKE 'MODEL_PROBE_%')"
            )
        ).one()
    observed(
        "R5-" + invalid_field,
        status=reply.status_code,
        probes=rows[0],
        usage_rows=rows[1],
        prompt_tokens=rows[2],
        audit_rows=rows[3],
    )
    # A hostile payload is a provider problem, never a database failure.
    assert reply.status_code == 200, "untrusted metadata must not surface as a 5xx"
    assert rows[0] == 1 and rows[1] == 1, "bad provider metadata rolled back call evidence"
    assert rows[3] == 1, "the call must stay auditable"
    if invalid_field == "model_length":
        assert reply.json()["resolvedModelName"] is None
        assert reply.json()["outcome"] == "SUCCESS", "the JSON output itself was valid"
    elif invalid_field == "usage_overflow":
        # The out-of-range count is unknowable; the usable dimension is kept.
        assert reply.json()["promptTokens"] is None
        assert reply.json()["completionTokens"] == 7
    else:
        assert rows[2] == 21, "response validation discarded separately valid reported usage"
        assert reply.json()["outcome"] == "FAILURE"
        assert reply.json()["errorCode"] == "INVALID_RESPONSE"


# ------------------------------------------------------------------------ R6
def test_r6_timeout_bounds_total_elapsed_time():
    """Real loopback fault injection: each chunk arrives within the read timeout."""
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
                    self.wfile.write(body[offset : offset + width])
                    self.wfile.flush()
                    if stop.wait(0.24):
                        return
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    request = ProbeRequest(
        base_url=base_url,
        model_name="review",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        secret_ref=SECRET_REF,
        timeout_seconds=1,
        environ={SECRET_REF: "dummy"},
    )
    error_code = None
    try:
        # A fast request first: its duration is this host's fixed client/connect
        # cost, which must not be counted as "the provider kept us waiting".
        fast_started = time.perf_counter()
        _time_out_fast(base_url)
        overhead = time.perf_counter() - fast_started

        started = time.perf_counter()
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

    observed(
        "R6",
        configured_timeout_seconds=1,
        elapsed_seconds=round(elapsed, 3),
        same_host_fixed_overhead_seconds=round(overhead, 3),
        error_code=error_code,
    )
    assert error_code == "TIMEOUT", "the configured value must be a total deadline"
    # The drip would need ~2.4s of body; the 1s budget must cut it short well
    # before that, allowing only the fixed client/connect cost measured above.
    assert elapsed < overhead + 1.0 + 0.7


def test_r6_oversized_response_is_rejected():
    """A response larger than the cap must fail as invalid, not be buffered."""

    def handler(_request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{" + b"0" * (400 * 1024) + b"}",
        )

    request = ProbeRequest(
        base_url="https://api.example.test",
        model_name="review",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        secret_ref=SECRET_REF,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
        environ={SECRET_REF: "dummy"},
    )
    with pytest.raises(AdapterError) as caught:
        probe(request)
    assert caught.value.code.value == "INVALID_RESPONSE"


def _time_out_fast(base_url: str) -> None:
    """One cheap request used only to measure this host's fixed setup cost."""

    class FastHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps(openai_ok_body()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FastHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A separate port on purpose: the drip server above is already running.
        probe(
            ProbeRequest(
                base_url=f"http://127.0.0.1:{server.server_port}",
                model_name="review",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                secret_ref=SECRET_REF,
                timeout_seconds=10,
                environ={SECRET_REF: "dummy"},
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ------------------------------------------------------------------------ R7
def test_r7_stale_provider_form_is_rejected(client):
    headers = setup_owner(client)
    original = create_provider(client, headers).json()
    pid = original["id"]

    first = client.patch(
        f"/api/v1/providers/{pid}",
        json={
            "name": "new-name",
            "baseUrl": original["baseUrl"],
            "expectedVersion": original["version"],
        },
        headers=headers,
    )
    stale = client.patch(
        f"/api/v1/providers/{pid}",
        json={
            "name": original["name"],
            "baseUrl": "https://new.example.test",
            "expectedVersion": original["version"],
        },
        headers=headers,
    )
    observed(
        "R7",
        first_status=first.status_code,
        stale_status=stale.status_code,
        first_name=first.json()["name"],
        stale_code=stale.json().get("code"),
    )
    assert first.status_code == 200
    assert first.json()["version"] == original["version"] + 1
    assert stale.status_code == 409, "a stale form must not silently overwrite"
    assert stale.json()["code"] == "CONCURRENT_UPDATE"

    # The newer value survived: the stale write changed nothing.
    current = client.get("/api/v1/models/centre", headers=headers).json()["providers"][0]
    assert current["name"] == "new-name"
    assert current["baseUrl"] == original["baseUrl"]

    # A form that cannot state its version is refused rather than assumed fresh.
    no_version = client.patch(
        f"/api/v1/providers/{pid}",
        json={"name": "no-version"},
        headers=headers,
    )
    assert no_version.status_code == 422


def test_r7_stale_model_form_is_rejected(client):
    headers = setup_owner(client)
    provider = create_provider(client, headers).json()
    model = create_model(client, headers, provider["id"]).json()

    first = client.patch(
        f"/api/v1/models/{model['id']}",
        json={"label": "first-label", "expectedVersion": model["version"]},
        headers=headers,
    )
    assert first.status_code == 200
    stale = client.patch(
        f"/api/v1/models/{model['id']}",
        json={"label": "stale-label", "expectedVersion": model["version"]},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "CONCURRENT_UPDATE"
    current = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
    assert current["label"] == "first-label"


# ------------------------------------------------------------------------ R8
def test_r8_profile_save_then_binding_failure_leaves_stale_retry(client):
    """The API must make the partial-success state discoverable.

    The UI fix (updating its local version after step 1 succeeds) is covered by
    the frontend suite. This test pins the server contract the UI relies on: after
    step 1 the agent's version has advanced, so replaying the old version is a
    conflict and the client must re-read rather than guess.
    """
    headers = setup_owner(client)
    assert create_company(client, headers).status_code == 201
    agent = client.get("/api/v1/organization", headers=headers).json()["agents"][0]
    payload = {"displayName": "changed-before-binding-fails", "expectedVersion": agent["version"]}
    saved = client.patch(f"/api/v1/agents/{agent['id']}", json=payload, headers=headers)
    failed_binding = client.post(
        f"/api/v1/agents/{agent['id']}/model-assignment",
        json={"modelId": 999999},
        headers=headers,
    )
    retry = client.patch(f"/api/v1/agents/{agent['id']}", json=payload, headers=headers)
    observed(
        "R8",
        profile_status=saved.status_code,
        binding_status=failed_binding.status_code,
        retry_status=retry.status_code,
        persisted_name=saved.json()["displayName"],
        new_version=saved.json()["version"],
    )
    assert saved.status_code == 200
    assert failed_binding.status_code == 404
    # The successful profile save advanced the version and reported the new one,
    # which is what lets the UI recover instead of resubmitting a stale number.
    assert saved.json()["version"] == agent["version"] + 1
    assert retry.status_code == 409
    assert retry.json()["code"] == "CONCURRENT_UPDATE"

    # Re-reading and retrying with the returned version works.
    refreshed = client.get("/api/v1/organization", headers=headers).json()["agents"][0]
    recovered = client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"displayName": "recovered", "expectedVersion": refreshed["version"]},
        headers=headers,
    )
    assert recovered.status_code == 200
    assert recovered.json()["displayName"] == "recovered"


# ------------------------------------------------------------------------ R9
def test_r9_provider_notes_can_be_cleared(client):
    headers = setup_owner(client)
    provider = create_provider(client, headers).json()
    pid = provider["id"]
    set_notes = client.patch(
        f"/api/v1/providers/{pid}",
        json={"notes": "old-note", "expectedVersion": provider["version"]},
        headers=headers,
    )
    assert set_notes.status_code == 200
    cleared = client.patch(
        f"/api/v1/providers/{pid}",
        json={"notes": None, "expectedVersion": set_notes.json()["version"]},
        headers=headers,
    )
    observed("R9", status=cleared.status_code, saved_notes=cleared.json()["notes"])
    assert cleared.status_code == 200
    assert cleared.json()["notes"] is None, "explicit null must clear, not keep, the text"

    # Omitting the field still means "leave it alone".
    set_again = client.patch(
        f"/api/v1/providers/{pid}",
        json={"notes": "keep-me", "expectedVersion": cleared.json()["version"]},
        headers=headers,
    )
    untouched = client.patch(
        f"/api/v1/providers/{pid}",
        json={"name": "renamed", "expectedVersion": set_again.json()["version"]},
        headers=headers,
    )
    assert untouched.status_code == 200
    assert untouched.json()["notes"] == "keep-me"


# ------------------------------------------------------------------------ R10
def test_r11_unbind_ends_binding_and_keeps_history(client, monkeypatch):
    """Selecting "未绑定" must end the binding, not silently keep the old model."""
    monkeypatch.setenv(SECRET_REF, "test-key-value")
    headers = setup_owner(client)
    provider = create_provider(client, headers, secret_ref=SECRET_REF).json()
    model = create_model(client, headers, provider["id"]).json()
    create_company(client, headers)
    agent = client.get("/api/v1/organization", headers=headers).json()["agents"][0]

    bound = client.post(
        f"/api/v1/agents/{agent['id']}/model-assignment",
        json={"modelId": model["id"]},
        headers=headers,
    )
    assert bound.status_code == 200
    assert bound.json()["model"] is not None

    unbound = client.post(
        f"/api/v1/agents/{agent['id']}/model-unbind",
        json={"note": "暂时不绑定"},
        headers=headers,
    )
    assert unbound.status_code == 200, unbound.text
    summaries = [entry["summary"] for entry in unbound.json()["entries"]]
    assert any("已结束" in summary for summary in summaries)

    org = client.get("/api/v1/organization", headers=headers).json()
    current = next(item for item in org["agents"] if item["id"] == agent["id"])
    assert current["model"] is None, "the binding must actually be gone from the current state"

    with client.app.state.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT ended_at FROM agent_model_assignments ORDER BY id")
        ).all()
    assert len(rows) == 1, "unbinding must keep the historical row"
    assert rows[0][0] is not None

    # History is reachable and shows both the binding and its end.
    history = client.get(f"/api/v1/agents/{agent['id']}/history", headers=headers).json()
    kinds = [entry["kind"] for entry in history["entries"]]
    assert "MODEL_ASSIGNMENT" in kinds

    # Unbinding again is a no-op, not an error.
    again = client.post(f"/api/v1/agents/{agent['id']}/model-unbind", json={}, headers=headers)
    assert again.status_code == 200
