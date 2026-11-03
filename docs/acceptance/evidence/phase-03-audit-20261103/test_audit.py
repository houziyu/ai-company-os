"""Independent correct-behaviour assertions; failures demonstrate review findings.

Run from backend with an isolated TEST_ADMIN_DATABASE_URL and -p tests.conftest.
No real provider is called; httpx.MockTransport exercises the production adapter.
"""

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.enums import StepStatus
from app.domain.errors import AppError
from app.main import BACKEND_ROOT, create_app
from app.models.tasks import BudgetAccount, BudgetReservation, DomainEvent, TaskExecutionStep
from app.services.execution_service import ExecutionService
from app.services.model_centre_service import ModelCentreService
from app.services.task_service import CompanyGovernanceService
from tests.test_phase03_review_fixes import _claim, claimed
from tests.test_tasks import bootstrap, create_task, run_worker


def configured_model(client, headers, secret_ref=None):
    provider = client.post(
        "/api/v1/providers",
        json={"name": "Audit provider", "kind": "OPENAI_COMPATIBLE",
              "baseUrl": "https://audit.invalid/v1", "secretRef": secret_ref},
        headers=headers,
    )
    assert provider.status_code == 201, provider.text
    model = client.post(
        "/api/v1/models",
        json={"providerId": provider.json()["id"], "logicalName": "audit-model"},
        headers=headers,
    )
    assert model.status_code == 201, model.text
    return model.json()["id"]


def transport_client(client, handler):
    app = create_app(client.app.state.settings, engine=client.app.state.engine,
                     backend_root=BACKEND_ROOT, probe_transport=httpx.MockTransport(handler))
    fresh = TestClient(app)
    fresh.cookies.update(client.cookies)
    return fresh


def provider_response(usage):
    return httpx.Response(200, json={
        "model": "audit-model", "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": usage,
    })


def test_missing_secret_does_not_leave_budget_reserved(client, session_factory):
    headers, _ = bootstrap(client)
    model_id = configured_model(client, headers)
    response = client.post(f"/api/v1/models/{model_id}/probe", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["callCount"] == 0
    assert response.json()["errorCode"] == "MISSING_SECRET"
    with session_factory() as session:
        account = session.scalar(select(BudgetAccount).where(BudgetAccount.scope == "COMPANY"))
        assert account.reserved_tokens == 0, f"No provider call, but reserved={account.reserved_tokens}"


def test_partial_probe_usage_is_conservative(client, session_factory, monkeypatch):
    headers, _ = bootstrap(client)
    monkeypatch.setenv("AUDIT_MODEL_KEY", "test-only")
    model_id = configured_model(client, headers, "AUDIT_MODEL_KEY")
    with transport_client(client, lambda request: provider_response({"prompt_tokens": 7})) as fresh:
        response = fresh.post(f"/api/v1/models/{model_id}/probe", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["callCount"] == 1
    assert response.json()["usageKnown"] is False
    with session_factory() as session:
        hold = session.scalar(select(BudgetReservation).where(BudgetReservation.kind == "PROBE"))
        assert (hold.settled_tokens, hold.needs_reconciliation) == (hold.amount_tokens, True), (
            f"partial usage settled={hold.settled_tokens}, hold={hold.amount_tokens}, "
            f"reconciliation={hold.needs_reconciliation}"
        )


def test_pause_committed_before_probe_reservation_blocks_call(client, session_factory, monkeypatch):
    headers, _ = bootstrap(client)
    monkeypatch.setenv("AUDIT_MODEL_KEY", "test-only")
    model_id = configured_model(client, headers, "AUDIT_MODEL_KEY")
    original = ModelCentreService._commit_probe_intent

    def pause_before_intent(self, *args, **kwargs):
        # Arrange the legitimate interleaving after the unlocked read but before
        # the reservation transaction acquires its company lock.
        with session_factory() as pause_session:
            CompanyGovernanceService().pause(pause_session, owner_user_id=1, reason="audit pause")
            pause_session.commit()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ModelCentreService, "_commit_probe_intent", pause_before_intent)
    calls = []

    def handler(request):
        calls.append(request)
        return provider_response({"prompt_tokens": 7, "completion_tokens": 3})

    with transport_client(client, handler) as fresh:
        response = fresh.post(f"/api/v1/models/{model_id}/probe", headers=headers)
    assert response.status_code == 200, response.text
    assert not calls, f"Provider called {len(calls)} time(s) after pause committed"


def open_step(session_factory, claim, sequence=0):
    with session_factory() as session:
        step = ExecutionService().start_step(
            session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
            sequence=sequence, name="audit step", estimated_tokens=64)
        session.commit()
        return step.id


def finish_step(session_factory, claim, step_id, tokens):
    with session_factory() as session:
        ExecutionService().finish_step(
            session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
            step_id=step_id, status=StepStatus.SUCCEEDED,
            prompt_tokens=tokens, completion_tokens=0, usage_known=True)
        session.commit()


def test_fence_cannot_modify_step_of_another_execution(client, session_factory):
    headers, first = claimed(client, session_factory)
    step_id = open_step(session_factory, first)
    assert create_task(client, headers, first.project_id).status_code == 201
    second = _claim(session_factory, worker_id="second-worker")
    try:
        finish_step(session_factory, second, step_id, 7)
    except AppError:
        pass
    with session_factory() as session:
        step = session.get(TaskExecutionStep, step_id)
        assert step.status == "RUNNING", f"Unrelated execution changed first step to {step.status}"


def test_duplicate_step_result_keeps_usage_consistent(client, session_factory):
    _, claim = claimed(client, session_factory)
    step_id = open_step(session_factory, claim)
    finish_step(session_factory, claim, step_id, 40)
    try:
        finish_step(session_factory, claim, step_id, 7)
    except AppError:
        pass
    with session_factory() as session:
        step = session.get(TaskExecutionStep, step_id)
        hold = session.scalar(select(BudgetReservation).where(BudgetReservation.step_id == step_id))
        assert step.prompt_tokens == hold.settled_tokens == 40, (
            f"step={step.prompt_tokens}, settled ledger={hold.settled_tokens}"
        )


def test_step_update_publishes_progress_event(client, session_factory):
    _, claim = claimed(client, session_factory)
    step_id = open_step(session_factory, claim)
    finish_step(session_factory, claim, step_id, 40)
    with session_factory() as session:
        events = session.scalars(select(DomainEvent).where(
            DomainEvent.task_id == claim.task_id,
            DomainEvent.event_type == "TASK_STEP_UPDATED")).all()
        assert events, "Step committed, but no progress event exists for SSE to deliver"


def test_repeated_retry_command_does_not_schedule_another_attempt(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"],
                          demo_behavior="PERMANENT_FAILURE", max_auto_retries=0).json()["task"]["id"]
    runtime = run_worker(session_factory)
    assert runtime.run_once()
    # Same request key represents the same logical retry even when the response
    # was lost and its newly scheduled execution has already finished.
    request_headers = {**headers, "Idempotency-Key": "audit-logical-retry"}
    url = f"/api/v1/tasks/{task_id}/retry"
    assert client.post(url, json={}, headers=request_headers).status_code == 200
    assert runtime.run_once()
    response = client.post(url, json={}, headers=request_headers)
    assert response.status_code == 200, response.text
    assert runtime.run_once() is False, "Duplicate retry scheduled a third execution"


def test_execution_page_projection_keeps_old_attempt_usage(client, session_factory):
    headers, project = bootstrap(client)
    runtime = run_worker(session_factory)
    first_execution_id = None
    for index in range(26):
        task_id = create_task(client, headers, project["id"], title=f"ledger-{index}").json()["task"]["id"]
        assert runtime.run_once()
        if first_execution_id is None:
            first_execution_id = client.get(f"/api/v1/tasks/{task_id}").json()["executions"][0]["id"]
    execution = client.get(f"/api/v1/executions/{first_execution_id}").json()
    assert execution["settledTokens"] == 500
    overview = client.get("/api/v1/budgets?ledgerLimit=200").json()
    # Exact projection used by ExecutionDetailView.vue: limit first, filter later.
    entries = [entry for entry in overview["ledger"] if entry["executionId"] == first_execution_id]
    shown_consumed = sum(entry["deltaConsumed"] for entry in entries if entry["entryKind"] == "SETTLE")
    assert shown_consumed == execution["settledTokens"], (
        f"Old execution actually used {execution['settledTokens']}, page projection shows {shown_consumed}"
    )
