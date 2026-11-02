"""Second-review regressions: real DB transactions and cancellable loopback I/O.

Original failing probes are preserved in docs/acceptance/evidence/phase-02-recheck.
HTTP transport fixtures now use AsyncHTTPTransport, like the production adapter.
The config race uses independent threads rather than a nested synchronous call
inside an async transport callback. Correctness assertions are retained/extended.
"""

import json
import select
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.domain.enums import ProviderKind
from app.main import BACKEND_ROOT
from app.providers.adapter import AdapterError, ProbeRequest, probe
from app.services.model_centre_service import ModelCentreService
from tests.test_organization import create_company, openai_ok_body
from tests.test_review_fixes import prepare


def observe(case, **facts):
    print("RECHECK " + json.dumps({"case": case, **facts}, ensure_ascii=True))


def test_late_old_probe_cannot_overwrite_new_probe(client, app, monkeypatch, engine):
    headers, provider, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    create_company(client, headers)
    agent = client.get("/api/v1/organization", headers=headers).json()["agents"][0]
    client.post(
        f"/api/v1/agents/{agent['id']}/model-assignment",
        headers=headers,
        json={"modelId": model["id"]},
    )
    service = ModelCentreService(
        session_factory=app.state.session_factory,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=openai_ok_body("not JSON"))
        ),
    )

    entered, release = threading.Event(), threading.Event()

    def old_response(_request):
        entered.set()
        assert release.wait(5), "test did not release old response"
        return httpx.Response(200, json=openai_ok_body())

    app.state.model_centre_service._transport = httpx.MockTransport(old_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(client.post, f"/api/v1/models/{model['id']}/probe", headers=headers)
        try:
            assert entered.wait(5)
            with app.state.session_factory() as other:
                service.update_provider(
                    other,
                    provider_id=provider["id"],
                    name=None,
                    kind=None,
                    base_url="https://new.example.test",
                    secret_ref=None,
                    change_secret_ref=False,
                    status=None,
                    notes=None,
                    notes_provided=False,
                    timeout_seconds=None,
                    expected_version=provider["version"],
                    actor_user_id=1,
                )
                other.commit()
            with app.state.session_factory() as other:
                newer = service.probe_model(other, model_id=model["id"], actor_user_id=1)
                assert newer.probe.outcome == "FAILURE"
                other.commit()
        finally:
            release.set()
        assert old.result(timeout=5).status_code == 200
    overview = client.get("/api/v1/models/centre", headers=headers).json()
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT supports_json_output, last_probe_outcome FROM provider_models")
        ).one()
    assigned = next(a for a in overview["assignments"] if a["agentId"] == agent["id"])
    observe(
        "late-old-probe",
        stored_capability=stored[0],
        stored_outcome=stored[1],
        displayed_model_capability=overview["models"][0]["supportsJsonOutput"],
        assigned_capability=assigned["jsonVerified"],
        stale=overview["models"][0]["probeEvidenceStale"],
    )
    assert tuple(stored) == (False, "FAILURE"), "old result overwrote newer configuration evidence"
    assert assigned["jsonVerified"] is False
    org = client.get("/api/v1/organization", headers=headers).json()
    assert org["agents"][0]["model"]["supportsJsonOutput"] is False
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM provider_usage_records")) == 2
        assert connection.scalar(text("SELECT count(*) FROM model_probes")) == 2


@pytest.mark.parametrize("old_revision", ["7b41c9d2f0aa", "3f8d2c6a91b4"])
def test_upgrade_marks_unknown_evidence_stale_and_accepts_new_probe(
    client, app, monkeypatch, database_url, engine, old_revision
):
    headers, _, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    assert client.post(f"/api/v1/models/{model['id']}/probe", headers=headers).status_code == 200
    # Only this fixture's random cloned database is downgraded/upgraded.
    monkeypatch.setenv("DATABASE_URL", database_url)
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.downgrade(cfg, old_revision)
    if old_revision == "3f8d2c6a91b4":
        # Existing installations can already have the faulty additive migration.
        with engine.begin() as connection:
            connection.execute(text("UPDATE providers SET config_fingerprint=''"))
            connection.execute(text("UPDATE provider_models SET probe_config_fingerprint=NULL"))
    command.upgrade(cfg, "head")
    before = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
    reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    after = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
    observe(
        "migration-evidence",
        old_capability=before["supportsJsonOutput"],
        old_stale=before["probeEvidenceStale"],
        new_probe_outcome=reply.json()["outcome"],
        new_capability=after["supportsJsonOutput"],
        new_stale=after["probeEvidenceStale"],
    )
    assert before["supportsJsonOutput"] is None, (
        "pre-fingerprint evidence must not be trusted after upgrade"
    )
    assert after["supportsJsonOutput"] is True and after["probeEvidenceStale"] is False


@pytest.mark.parametrize("model_name", ["\ud800", "\udfff"])
@pytest.mark.parametrize("content", ['{"ok":true}', "invalid JSON"])
def test_surrogate_model_metadata_does_not_lose_known_usage(
    client, app, monkeypatch, engine, model_name, content
):
    body = openai_ok_body(content)
    body["model"] = model_name
    headers, _, model = prepare(
        client,
        app,
        monkeypatch,
        lambda _: httpx.Response(
            200,
            content=json.dumps(body).encode("ascii"),
            headers={"Content-Type": "application/json"},
        ),
    )
    error = None
    status = None
    try:
        reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
        status = reply.status_code
    except Exception as exc:
        error = type(exc).__name__
    with engine.connect() as connection:
        state = connection.execute(
            text(
                "SELECT outcome, (SELECT count(*) FROM provider_usage_records),"
                " (SELECT count(*) FROM audit_events WHERE action LIKE 'MODEL_PROBE_%') FROM model_probes"
            )
        ).one()
    observe(
        "surrogate-model",
        status=status,
        exception=error,
        outcome=state[0],
        usage_rows=state[1],
        audit_rows=state[2],
    )
    assert status == 200 and error is None, "untrusted model metadata escaped validation"
    assert state[0] != "PENDING" and state[1] == 1 and state[2] == 1
    assert reply.json()["resolvedModelName"] is None
    assert (reply.json()["promptTokens"], reply.json()["completionTokens"]) == (21, 7)


def test_total_deadline_covers_slow_response_headers():
    stop = threading.Event()
    disconnected = threading.Event()
    body = json.dumps(openai_ok_body()).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                self.wfile.flush()
                for i in range(12):
                    self.wfile.write(f"X-Drip-{i}: value\r\n".encode())
                    self.wfile.flush()
                    # EOF is a direct observation of client closure. A write
                    # error can be delayed by TCP buffering (notably Windows).
                    readable, _, _ = select.select([self.connection], [], [], 0.3)
                    if readable and self.connection.recv(1) == b"":
                        disconnected.set()
                        return
                    if stop.is_set():
                        return
                self.wfile.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = httpx.AsyncHTTPTransport()
    error_code = None
    started = time.perf_counter()
    try:
        probe(
            ProbeRequest(
                base_url=f"http://127.0.0.1:{server.server_port}",
                model_name="test",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                secret_ref="TEST_KEY",
                timeout_seconds=1,
                environ={"TEST_KEY": "dummy"},
                transport=transport,
            )
        )
    except AdapterError as exc:
        error_code = exc.code.value
    finally:
        elapsed = time.perf_counter() - started
        saw_disconnect = disconnected.wait(1.2)
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    observe(
        "slow-headers",
        configured=1,
        elapsed=round(elapsed, 3),
        error_code=error_code,
        peer_saw_disconnect=saw_disconnect,
    )
    assert error_code == "TIMEOUT"
    assert elapsed < 1.5, "total deadline does not cancel header I/O"
    assert saw_disconnect, "timed-out network work survived the call"


def test_deadline_cancels_a_body_read_with_only_remaining_budget():
    disconnected = threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(1)
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            # Consume most of the total budget before starting the next read.
            time.sleep(0.7)
            self.wfile.write(b"{")
            self.wfile.flush()
            self.connection.settimeout(2)
            if self.connection.recv(1) == b"":
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = httpx.AsyncHTTPTransport()
    started = time.perf_counter()
    try:
        with pytest.raises(AdapterError) as caught:
            probe(
                ProbeRequest(
                    base_url=f"http://127.0.0.1:{server.server_port}",
                    model_name="test",
                    kind=ProviderKind.OPENAI_COMPATIBLE,
                    secret_ref="TEST_KEY",
                    timeout_seconds=1,
                    environ={"TEST_KEY": "dummy"},
                    transport=transport,
                )
            )
        elapsed = time.perf_counter() - started
        assert caught.value.code.value == "TIMEOUT"
        assert elapsed < 1.5
        assert disconnected.wait(1)
        assert len(calls) == 1
        observe("body-stall", elapsed=round(elapsed, 3), calls=len(calls), peer_saw_disconnect=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_data_migration_preserves_matching_evidence_pending_usage_and_audit(
    client, app, monkeypatch, database_url, engine
):
    headers, _, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)

    def interrupted(_request):
        raise RuntimeError("injected interruption")

    app.state.model_centre_service._transport = httpx.MockTransport(interrupted)
    with pytest.raises(RuntimeError, match="injected interruption"):
        client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)

    def history_snapshot():
        with engine.connect() as connection:
            return [
                connection.execute(text(query)).all()
                for query in (
                    "SELECT * FROM model_probes ORDER BY id",
                    "SELECT * FROM provider_usage_records ORDER BY id",
                    "SELECT * FROM audit_events ORDER BY id",
                )
            ]

    before = history_snapshot()
    monkeypatch.setenv("DATABASE_URL", database_url)
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    # Data-only downgrade is intentionally non-destructive, even with PENDING.
    command.downgrade(cfg, "3f8d2c6a91b4")
    command.upgrade(cfg, "head")
    assert history_snapshot() == before
    current = client.get("/api/v1/models/centre", headers=headers).json()
    assert current["models"][0]["supportsJsonOutput"] is True
    assert current["models"][0]["probeEvidenceStale"] is False
    assert current["usage"]["pendingProbes"] == 1
    observe("migration-preservation", history_unchanged=True, capability=True, pending=1)


def test_stale_capability_is_hidden_at_every_read_boundary(client, app, monkeypatch, engine):
    headers, _, model = prepare(
        client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body())
    )
    create_company(client, headers, model_id=model["id"])
    client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    with engine.begin() as connection:
        connection.execute(text("UPDATE provider_models SET probe_config_fingerprint='superseded'"))
    centre = client.get("/api/v1/models/centre", headers=headers).json()
    assert centre["models"][0]["supportsJsonOutput"] is None
    assert centre["models"][0]["lastProbeOutcome"] is None
    assert all(a["jsonVerified"] is None for a in centre["assignments"])
    org = client.get("/api/v1/organization", headers=headers).json()
    assert all(a["model"]["supportsJsonOutput"] is None for a in org["agents"])
