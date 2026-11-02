"""Independent second-version review; correct-behaviour assertions, no app edits."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tests.conftest import *  # noqa: F403
from tests.test_review_fixes import prepare
from tests.test_organization import create_company, openai_ok_body
from app.domain.enums import ProviderKind
from app.main import BACKEND_ROOT
from app.providers.adapter import AdapterError, ProbeRequest, probe
from app.services.model_centre_service import ModelCentreService


def observe(case, **facts):
    print('RECHECK ' + json.dumps({'case': case, **facts}, ensure_ascii=True))


def test_late_old_probe_cannot_overwrite_new_probe(client, app, monkeypatch, engine):
    headers, provider, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body()))
    create_company(client, headers)
    agent = client.get('/api/v1/organization', headers=headers).json()['agents'][0]
    client.post(f"/api/v1/agents/{agent['id']}/model-assignment", headers=headers, json={'modelId': model['id']})
    service = ModelCentreService(session_factory=app.state.session_factory, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=openai_ok_body('not JSON'))))

    def old_response(_request):
        # Independent committed transaction, like another authenticated owner's session.
        with app.state.session_factory() as other:
            service.update_provider(other, provider_id=provider['id'], name=None, kind=None,
                base_url='https://new.example.test', secret_ref=None, change_secret_ref=False,
                status=None, notes=None, notes_provided=False, timeout_seconds=None,
                expected_version=provider['version'], actor_user_id=1)
            other.commit()
        with app.state.session_factory() as other:
            newer = service.probe_model(other, model_id=model['id'], actor_user_id=1)
            assert newer.probe.outcome == 'FAILURE'
            other.commit()
        return httpx.Response(200, json=openai_ok_body())

    app.state.model_centre_service._transport = httpx.MockTransport(old_response)
    assert client.post(f"/api/v1/models/{model['id']}/probe", headers=headers).status_code == 200
    overview = client.get('/api/v1/models/centre', headers=headers).json()
    with engine.connect() as connection:
        stored = connection.execute(text('SELECT supports_json_output, last_probe_outcome FROM provider_models')).one()
    assigned = next(a for a in overview['assignments'] if a['agentId'] == agent['id'])
    observe('late-old-probe', stored_capability=stored[0], stored_outcome=stored[1],
        displayed_model_capability=overview['models'][0]['supportsJsonOutput'],
        assigned_capability=assigned['jsonVerified'], stale=overview['models'][0]['probeEvidenceStale'])
    assert tuple(stored) == (False, 'FAILURE'), 'old result overwrote newer configuration evidence'
    assert assigned['jsonVerified'] is False


def test_upgrade_marks_unknown_evidence_stale_and_accepts_new_probe(client, app, monkeypatch, database_url):
    headers, _, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(200, json=openai_ok_body()))
    assert client.post(f"/api/v1/models/{model['id']}/probe", headers=headers).status_code == 200
    # Only this fixture's random cloned database is downgraded/upgraded.
    monkeypatch.setenv('DATABASE_URL', database_url)
    cfg = Config(str(BACKEND_ROOT / 'alembic.ini'))
    command.downgrade(cfg, '7b41c9d2f0aa')
    command.upgrade(cfg, 'head')
    before = client.get('/api/v1/models/centre', headers=headers).json()['models'][0]
    reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    after = client.get('/api/v1/models/centre', headers=headers).json()['models'][0]
    observe('migration-evidence', old_capability=before['supportsJsonOutput'], old_stale=before['probeEvidenceStale'],
        new_probe_outcome=reply.json()['outcome'], new_capability=after['supportsJsonOutput'], new_stale=after['probeEvidenceStale'])
    assert before['supportsJsonOutput'] is None, 'pre-fingerprint evidence must not be trusted after upgrade'
    assert after['supportsJsonOutput'] is True and after['probeEvidenceStale'] is False


def test_surrogate_model_metadata_does_not_lose_known_usage(client, app, monkeypatch, engine):
    body = openai_ok_body()
    body['model'] = '\ud800'
    headers, _, model = prepare(client, app, monkeypatch, lambda _: httpx.Response(
        200, content=json.dumps(body).encode('ascii'), headers={'Content-Type': 'application/json'}))
    error = None
    status = None
    try:
        reply = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
        status = reply.status_code
    except Exception as exc:
        error = type(exc).__name__
    with engine.connect() as connection:
        state = connection.execute(text("SELECT outcome, (SELECT count(*) FROM provider_usage_records),"
            " (SELECT count(*) FROM audit_events WHERE action LIKE 'MODEL_PROBE_%') FROM model_probes")).one()
    observe('surrogate-model', status=status, exception=error, outcome=state[0], usage_rows=state[1], audit_rows=state[2])
    assert status == 200 and error is None, 'untrusted model metadata escaped validation'
    assert state[0] != 'PENDING' and state[1] == 1 and state[2] == 1


def test_total_deadline_covers_slow_response_headers():
    stop = threading.Event()
    body = json.dumps(openai_ok_body()).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            try:
                self.wfile.write(b'HTTP/1.1 200 OK\r\n')
                self.wfile.flush()
                for i in range(12):
                    self.wfile.write(f'X-Drip-{i}: value\r\n'.encode())
                    self.wfile.flush()
                    if stop.wait(0.3):
                        return
                self.wfile.write(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = httpx.HTTPTransport()
    error_code = None
    started = time.perf_counter()
    try:
        probe(ProbeRequest(base_url=f'http://127.0.0.1:{server.server_port}', model_name='test',
            kind=ProviderKind.OPENAI_COMPATIBLE, secret_ref='TEST_KEY', timeout_seconds=1,
            environ={'TEST_KEY': 'dummy'}, transport=transport))
    except AdapterError as exc:
        error_code = exc.code.value
    finally:
        elapsed = time.perf_counter() - started
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    observe('slow-headers', configured=1, elapsed=round(elapsed, 3), error_code=error_code)
    assert elapsed < 1.5, 'total deadline does not cancel header I/O'
