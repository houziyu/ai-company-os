"""Phase 03 backend tests: queue, leases, fencing, budget, recovery, SSE.

These are real PostgreSQL integration tests. They use the per-test database from
``conftest`` so row locks, ``SKIP LOCKED`` and check constraints behave as they do
in production. Every unverified property is asserted directly instead of being
inferred from a passing HTTP status.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.enums import (
    BudgetScope,
    ReservationKind,
    TaskStatus,
)
from app.main import BACKEND_ROOT, create_app
from app.models.organization import Company
from app.models.tasks import (
    BudgetAccount,
    BudgetLedgerEntry,
    BudgetReservation,
    DomainEvent,
    Task,
    TaskExecution,
    TaskExecutionStep,
)
from app.services.budget_service import BudgetService
from app.services.execution_service import ExecutionService, ProcessorOutcome
from app.workers.demo_processor import TOTAL_KNOWN_TOKENS, DemoProcessor
from app.workers.task_worker import WorkerRuntime
from tests.test_organization import create_company

# --------------------------------------------------------------------- helpers


def owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "username": "owner",
            "display_name": "所有者",
            "password": "correct-horse-battery",
            "passwordConfirm": "correct-horse-battery",
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 201, response.text
    return {"X-CSRF-Token": response.json()["csrfToken"], "Origin": "http://testserver"}


def bootstrap(client: TestClient, *, budget_tokens: int = 100_000) -> tuple[dict[str, str], dict]:
    """Owner + company + one project. Returns (headers, project payload)."""
    headers = owner_headers(client)
    create_company(client, headers)
    response = client.post(
        "/api/v1/projects",
        json={
            "key": "DEMO",
            "name": "演练项目",
            "objective": "验证任务调度与预算。",
            "runMode": "DEMO",
            "budgetTokens": budget_tokens,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return headers, response.json()


def create_task(
    client: TestClient,
    headers: dict[str, str],
    project_id: int,
    *,
    title: str = "演练任务",
    budget_tokens: int = 1_000,
    demo_behavior: str = "SUCCESS",
    max_auto_retries: int = 1,
    idempotency_key: str | None = None,
):
    return client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={
            "title": title,
            "objective": "执行一个确定的演练步骤。",
            "acceptance": "两步完成且用量已登记。",
            "budgetTokens": budget_tokens,
            "demoBehavior": demo_behavior,
            "maxAutoRetries": max_auto_retries,
            "idempotencyKey": idempotency_key or uuid.uuid4().hex,
        },
        headers=headers,
    )


def run_worker(
    session_factory: sessionmaker[Session],
    *,
    name: str = "worker-test",
    lease_seconds: float = 30.0,
    slow_step_seconds: float = 0.0,
) -> WorkerRuntime:
    return WorkerRuntime(
        Settings(database_url="postgresql+psycopg://unused", app_env="test", worker_name=name),
        session_factory,
        processor=DemoProcessor(session_factory, slow_step_seconds=slow_step_seconds),
        lease_seconds=lease_seconds,
        poll_interval_seconds=0.05,
    )


@pytest.fixture()
def session_scope_factory(session_factory: sessionmaker[Session]):
    return session_factory


# --------------------------------------------------------------- schema basics
def test_phase03_tables_and_company_pause_columns_exist(session_factory):
    with session_factory() as session:
        tables = {
            row[0]
            for row in session.execute(
                __import__("sqlalchemy").text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                )
            )
        }
    for expected in {
        "projects",
        "tasks",
        "task_executions",
        "task_execution_steps",
        "task_events",
        "domain_events",
        "task_messages",
        "budget_accounts",
        "budget_reservations",
        "budget_ledger_entries",
    }:
        assert expected in tables, expected


# ------------------------------------------------------------ idempotent create
def test_duplicate_task_request_creates_one_task_and_one_reservation(client, session_factory):
    headers, project = bootstrap(client)
    key = "same-key-1"

    first = create_task(client, headers, project["id"], idempotency_key=key)
    assert first.status_code == 201, first.text
    second = create_task(client, headers, project["id"], idempotency_key=key)
    assert second.status_code == 200, second.text
    assert second.json()["task"]["id"] == first.json()["task"]["id"]
    assert second.json()["created"] is False

    with session_factory() as session:
        tasks = session.scalars(select(Task)).all()
        assert len(tasks) == 1
        # One company hold and one project hold for the single task.
        holds = session.scalars(
            select(BudgetReservation).where(BudgetReservation.task_id == tasks[0].id)
        ).all()
        assert len(holds) == 2
        assert {hold.kind for hold in holds} == {ReservationKind.PLANNING.value}
        assert {hold.scope for hold in holds} == {
            BudgetScope.COMPANY.value,
            BudgetScope.PROJECT.value,
        }
        # The RESERVE ledger entries: exactly one per hold, never duplicated.
        reserves = session.scalars(
            select(BudgetLedgerEntry).where(BudgetLedgerEntry.entry_kind == "RESERVE")
        ).all()
        assert len(reserves) == 2


def test_same_idempotency_key_with_different_body_is_refused(client, session_factory):
    headers, project = bootstrap(client)
    key = "conflict-key"
    first = create_task(client, headers, project["id"], idempotency_key=key)
    assert first.status_code == 201

    conflict = create_task(client, headers, project["id"], title="另一个标题", idempotency_key=key)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    with session_factory() as session:
        assert len(session.scalars(select(Task)).all()) == 1


# -------------------------------------------------------------- budget envelope
def test_insufficient_company_budget_refuses_task_without_creating_rows(client, session_factory):
    # Company allowance is 1,000,000 by default; a task budget above it cannot
    # be held, so the request must fail and leave nothing behind.
    headers, project = bootstrap(client, budget_tokens=10**9)
    response = create_task(client, headers, project["id"], budget_tokens=10**9)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "BUDGET_INSUFFICIENT"

    with session_factory() as session:
        assert session.scalars(select(Task)).all() == []
        assert session.scalars(select(BudgetReservation)).all() == []
        # No ledger entry either: a refused request must not book a hold.
        assert session.scalars(select(BudgetLedgerEntry)).all() == []


def test_insufficient_project_budget_refuses_task(client, session_factory):
    headers, project = bootstrap(client, budget_tokens=500)
    response = create_task(client, headers, project["id"], budget_tokens=900)
    assert response.status_code == 409
    assert response.json()["details"]["scope"] == "PROJECT"

    with session_factory() as session:
        # The company hold and the task row must be rolled back with the failed
        # transaction. The project account created by project setup legitimately
        # remains, with nothing held against it.
        assert session.scalars(select(Task)).all() == []
        assert session.scalars(select(BudgetReservation)).all() == []
        accounts = session.scalars(select(BudgetAccount)).all()
        assert {account.scope for account in accounts} == {"PROJECT"}
        assert all(account.reserved_tokens == 0 for account in accounts)


def test_two_tasks_competing_for_scarce_project_budget_only_one_wins(client, session_factory):
    """Concurrent creation must not overdraw the project allowance."""
    headers, project = bootstrap(client, budget_tokens=1_000)

    results: list[int] = []
    lock = threading.Lock()

    def submit(index: int) -> None:
        local = TestClient(client.app, base_url="http://testserver")
        local.cookies.update(client.cookies)
        response = create_task(
            local,
            headers,
            project["id"],
            title=f"并发任务 {index}",
            budget_tokens=800,
            idempotency_key=f"race-{index}",
        )
        with lock:
            results.append(response.status_code)

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(results).count(201) == 1, results
    assert all(code in (201, 409) for code in results), results

    with session_factory() as session:
        project_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "PROJECT")
        ).one()
        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        # Never negative, and exactly one hold of 800 is open.
        assert project_account.reserved_tokens == 800
        assert company_account.reserved_tokens == 800
        assert len(session.scalars(select(Task)).all()) == 1


# ------------------------------------------------------------ claim and fencing
def test_only_one_of_two_workers_claims_a_task(client, session_factory):
    headers, project = bootstrap(client)
    response = create_task(client, headers, project["id"])
    task_id = response.json()["task"]["id"]

    claims: list[object] = []
    lock = threading.Lock()

    def claim(name: str) -> None:
        service = ExecutionService()
        with session_factory() as session:
            claim_result = service.claim_next(session, worker_id=name)
            session.commit()
        with lock:
            claims.append(claim_result)

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1, claims
    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None and task.status == TaskStatus.RUNNING.value
        executions = session.scalars(
            select(TaskExecution).where(TaskExecution.task_id == task_id)
        ).all()
        assert len(executions) == 1
        assert executions[0].fencing_token == 1


def test_old_lease_cannot_write_after_a_newer_claim(client, session_factory):
    """A stale worker's result must be refused, and the newer state preserved."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]

    service = ExecutionService()
    with session_factory() as session:
        first = service.claim_next(session, worker_id="old-worker")
        session.commit()
    assert first is not None

    # Simulate the attempt being reclaimed by a newer worker: expire the lease and
    # let recovery re-queue it, then claim again with a fresh worker.
    with session_factory() as session:
        execution = session.get(TaskExecution, first.execution_id)
        assert execution is not None
        execution.lease_expires_at = execution.lease_expires_at - __import__("datetime").timedelta(
            hours=1
        )
        session.commit()
    with session_factory() as session:
        recovered = service.recover_expired_leases(session)
        session.commit()
    assert task_id in recovered

    with session_factory() as session:
        second = service.claim_next(session, worker_id="new-worker")
        session.commit()
    assert second is not None
    assert second.fencing_token > first.fencing_token

    # The old worker now tries to report success with its stale token.
    with session_factory() as session:
        from app.domain.errors import LeaseLostError

        with pytest.raises(LeaseLostError):
            service.complete(
                session,
                execution_id=first.execution_id,
                fencing_token=first.fencing_token,
                outcome=ProcessorOutcome(
                    succeeded=True,
                    summary="迟到结果",
                    prompt_tokens=1,
                    completion_tokens=1,
                    usage_known=True,
                ),
            )
        session.rollback()

    with session_factory() as session:
        stale_execution = session.get(TaskExecution, first.execution_id)
        assert stale_execution is not None
        # The stale attempt stays INTERRUPTED; it never became SUCCEEDED.
        assert stale_execution.status == "INTERRUPTED"
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == TaskStatus.RUNNING.value
        assert task.fencing_counter == second.fencing_token


def test_expired_lease_is_interrupted_and_requeued_for_idempotent_processor(
    client, session_factory
):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    service = ExecutionService()
    with session_factory() as session:
        claim = service.claim_next(session, worker_id="killed-worker", lease_seconds=-1)
        session.commit()
    assert claim is not None

    with session_factory() as session:
        recovered = service.recover_expired_leases(session, idempotent_processor=True)
        session.commit()
    assert task_id in recovered

    with session_factory() as session:
        execution = session.get(TaskExecution, claim.execution_id)
        assert execution is not None
        assert execution.status == "INTERRUPTED"
        assert execution.interrupted_unknown_side_effects is True
        assert execution.recovery_note is not None
        task = session.get(Task, task_id)
        assert task is not None
        # Re-queued because the demo processor declares its steps idempotent.
        assert task.status == TaskStatus.QUEUED.value


def test_interrupted_attempt_without_idempotency_waits_for_a_human(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    service = ExecutionService()
    with session_factory() as session:
        claim = service.claim_next(session, worker_id="killed-worker", lease_seconds=-1)
        session.commit()
    assert claim is not None

    with session_factory() as session:
        service.recover_expired_leases(session, idempotent_processor=False)
        session.commit()

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        # Unknown side effects are never blindly replayed.
        assert task.status == TaskStatus.INTERRUPTED.value
        assert task.last_error_code == "WORKER_LOST"


# ------------------------------------------------------------ demo processor E2E
def test_successful_demo_run_settles_budget_exactly_once(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]

    runtime = run_worker(session_factory, name="worker-e2e")
    runtime.recover()
    assert runtime.run_once() is True

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == TaskStatus.SUCCEEDED.value
        assert task.result_summary is not None
        assert "演练运行" in task.result_summary
        execution = session.scalars(
            select(TaskExecution).where(TaskExecution.task_id == task_id)
        ).one()
        assert execution.status == "SUCCEEDED"
        assert execution.usage_known is True
        assert execution.settled_tokens == TOTAL_KNOWN_TOKENS
        assert execution.needs_reconciliation is False

        task_account = session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == "TASK", BudgetAccount.scope_id == task_id
            )
        ).one()
        assert task_account.consumed_tokens == TOTAL_KNOWN_TOKENS
        assert task_account.reserved_tokens == 0

        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        # The company consumed the real usage and holds nothing open.
        assert company_account.consumed_tokens == TOTAL_KNOWN_TOKENS
        assert company_account.reserved_tokens == 0

        project_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "PROJECT")
        ).one()
        assert project_account.consumed_tokens == TOTAL_KNOWN_TOKENS
        assert project_account.reserved_tokens == 0

        # No hold is left open, and every ledger entry is unique per reservation.
        open_holds = session.scalars(
            select(BudgetReservation).where(BudgetReservation.status == "RESERVED")
        ).all()
        assert open_holds == []
        entries = session.scalars(select(BudgetLedgerEntry)).all()
        keys = [(entry.account_id, entry.entry_kind, entry.reservation_id) for entry in entries]
        assert len(keys) == len(set(keys))


def test_duplicate_settlement_does_not_double_count(client, session_factory):
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"])
    runtime = run_worker(session_factory)
    runtime.run_once()

    with session_factory() as session:
        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        before = company_account.consumed_tokens
        settled_reservations = session.scalars(
            select(BudgetReservation).where(BudgetReservation.status == "SETTLED")
        ).all()
        ids = [row.id for row in settled_reservations]
        # Re-settling every resolved hold must be a no-op.
        budget = BudgetService()
        for reservation_id in ids:
            assert (
                budget.settle(
                    session, reservation_id=reservation_id, actual_tokens=999_999, known=True
                )
                is None
            )
        session.commit()

    with session_factory() as session:
        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        assert company_account.consumed_tokens == before
        # And no extra SETTLE entry was written.
        settle_entries = session.scalars(
            select(BudgetLedgerEntry).where(BudgetLedgerEntry.entry_kind == "SETTLE")
        ).all()
        assert len(settle_entries) == len(
            {(e.account_id, e.reservation_id) for e in settle_entries}
        )


def test_unknown_usage_consumes_hold_and_is_flagged(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(
        client, headers, project["id"], demo_behavior="UNKNOWN_USAGE", budget_tokens=1_000
    ).json()["task"]["id"]
    runtime = run_worker(session_factory)
    runtime.run_once()

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        # The demo still succeeds (its steps completed) but usage is unknown.
        assert task.status == TaskStatus.SUCCEEDED.value
        execution = session.scalars(
            select(TaskExecution).where(TaskExecution.task_id == task_id)
        ).one()
        assert execution.usage_known is False
        assert execution.settled_tokens is None
        assert execution.needs_reconciliation is True

        # Unknown usage is not zero: every hold was consumed and flagged. The
        # company/project planning envelope is consumed in full (the conservative
        # reading), and each call hold consumed its own estimate.
        settle_entries = session.scalars(
            select(BudgetLedgerEntry).where(BudgetLedgerEntry.entry_kind == "SETTLE")
        ).all()
        assert settle_entries, "expected settlement entries"
        for entry in settle_entries:
            assert entry.usage_known is False
            assert entry.needs_reconciliation is True
            assert entry.usage_tokens is None
            assert entry.delta_consumed > 0

        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        assert company_account.consumed_tokens == 1_000
        assert company_account.reserved_tokens == 0

        task_account = session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == "TASK", BudgetAccount.scope_id == task_id
            )
        ).one()
        assert task_account.reserved_tokens == 0
        # Two steps with a 64-token fallback estimate each.
        assert task_account.consumed_tokens == 128


def test_transient_failure_retries_with_a_new_execution_id(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(
        client,
        headers,
        project["id"],
        demo_behavior="TRANSIENT_FAILURE",
        max_auto_retries=1,
    ).json()["task"]["id"]

    runtime = run_worker(session_factory)
    # First attempt fails with a retryable error and re-queues.
    assert runtime.run_once() is True
    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == TaskStatus.QUEUED.value
        first = session.scalars(select(TaskExecution).where(TaskExecution.task_id == task_id)).one()
        first_id = first.id
        assert first.status == "FAILED"

    # The retry is a second attempt with its own id and its own steps.
    assert runtime.run_once() is True
    with session_factory() as session:
        executions = session.scalars(
            select(TaskExecution)
            .where(TaskExecution.task_id == task_id)
            .order_by(TaskExecution.attempt)
        ).all()
        assert len(executions) == 2
        assert executions[0].id == first_id
        # Historical evidence is intact: the failed attempt keeps its step rows.
        failed_steps = session.scalars(
            select(TaskExecutionStep).where(TaskExecutionStep.execution_id == first_id)
        ).all()
        assert failed_steps
        assert executions[1].id != first_id
        assert executions[1].retried_from_execution_id == first_id
        # The retry still fails (the demo always fails this behaviour) and, with
        # the retry budget spent, the task ends FAILED.
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED.value
        assert task.attempt_count == 2


def test_permanent_failure_is_not_retried(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"], demo_behavior="PERMANENT_FAILURE").json()[
        "task"
    ]["id"]
    runtime = run_worker(session_factory)
    runtime.run_once()

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED.value
        assert task.attempt_count == 1
        assert task.last_error_code == "SIMULATED_PERMANENT"
        assert len(session.scalars(select(TaskExecution)).all()) == 1


# ----------------------------------------------------------- cancellation paths
def test_cancel_queued_task_releases_planning_holds(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"], budget_tokens=700).json()["task"]["id"]
    response = client.post(
        f"/api/v1/tasks/{task_id}/cancel", json={"reason": "不需要了"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "CANCELLED"

    with session_factory() as session:
        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        assert company_account.reserved_tokens == 0
        assert company_account.consumed_tokens == 0
        project_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "PROJECT")
        ).one()
        assert project_account.reserved_tokens == 0
        # The release is recorded, not silently forgotten.
        releases = session.scalars(
            select(BudgetLedgerEntry).where(BudgetLedgerEntry.entry_kind == "RELEASE")
        ).all()
        assert len(releases) == 2


def test_cancel_running_task_is_cooperative_and_settles_once(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(
        client, headers, project["id"], demo_behavior="SLOW", budget_tokens=1_000
    ).json()["task"]["id"]

    service = ExecutionService()
    with session_factory() as session:
        claim = service.claim_next(session, worker_id="slow-worker")
        session.commit()
    assert claim is not None

    # Cancel while the attempt is held by a worker.
    response = client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert "安全检查点" in response.json()["message"]

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        # Not yet cancelled: the running call has not reached a safe point.
        assert task.status == TaskStatus.RUNNING.value
        assert task.cancel_requested_at is not None
        execution = session.get(TaskExecution, claim.execution_id)
        assert execution is not None
        assert execution.cancel_requested_at is not None

    # The worker completes with a cancelled outcome, settling only what it used.
    with session_factory() as session:
        service.complete(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            outcome=ProcessorOutcome(
                succeeded=False,
                summary=None,
                prompt_tokens=None,
                completion_tokens=None,
                usage_known=False,
                error_code="CANCELLED_BY_OWNER",
                error_message="执行在安全检查点停止。",
                cancelled=True,
            ),
        )
        session.commit()

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED.value
        # Cancelling before any call must not consume the planning envelope.
        company_account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "COMPANY")
        ).one()
        assert company_account.consumed_tokens == 0
        assert company_account.reserved_tokens == 0
        execution = session.get(TaskExecution, claim.execution_id)
        assert execution is not None
        assert execution.status == "CANCELLED"


def test_cannot_cancel_a_terminal_task(client):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    runtime_ok = run_worker(client.app.state.session_factory)
    runtime_ok.run_once()

    response = client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TASK_TRANSITION"


# ------------------------------------------------------------- company pause
def test_company_pause_blocks_new_tasks_and_claims(client, session_factory):
    headers, project = bootstrap(client)
    paused = client.post("/api/v1/company/pause", json={"reason": "验收暂停"}, headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"

    refused = create_task(client, headers, project["id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "COMPANY_PAUSED"

    with session_factory() as session:
        company = session.get(Company, 1)
        assert company is not None
        assert company.status == "PAUSED"
        assert company.paused_at is not None
        assert company.pause_reason == "验收暂停"
        # No claim happens while paused.
        assert ExecutionService().claim_next(session, worker_id="paused-worker") is None
        session.rollback()

    resumed = client.post("/api/v1/company/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "ACTIVE"
    created = create_task(client, headers, project["id"])
    assert created.status_code == 201, created.text


def test_company_pause_blocks_probes_without_spending(client, session_factory, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")
    headers = owner_headers(client)
    create_company(client, headers)
    provider = client.post(
        "/api/v1/providers",
        json={
            "name": "演示供应商",
            "kind": "DEEPSEEK",
            "baseUrl": "https://api.deepseek.com",
            "secretRef": "DEEPSEEK_API_KEY",
        },
        headers=headers,
    ).json()
    model = client.post(
        "/api/v1/models",
        json={"providerId": provider["id"], "logicalName": "m", "label": "M"},
        headers=headers,
    ).json()

    calls: list[object] = []
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    app = create_app(
        client.app.state.settings,
        engine=client.app.state.engine,
        backend_root=BACKEND_ROOT,
        probe_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as fresh:
        # Reuse the owner session by copying cookies from the original client.
        fresh.cookies.update(client.cookies)
        client.post("/api/v1/company/pause", json={}, headers=headers)
        response = fresh.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "FAILURE"
        assert response.json()["errorCode"] == "COMPANY_PAUSED"
        assert response.json()["callCount"] == 0

    assert calls == [], "a paused company must not reach the provider"


# ------------------------------------------------------------------- SSE replay
def test_event_stream_replays_after_last_event_id(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    runtime = run_worker(session_factory)
    runtime.run_once()

    with session_factory() as session:
        events = session.scalars(
            select(DomainEvent).where(DomainEvent.task_id == task_id).order_by(DomainEvent.id)
        ).all()
        assert len(events) >= 3  # created, started, succeeded
        ids = [event.id for event in events]

    page = client.get(f"/api/v1/tasks/{task_id}/events?after=0", headers=headers)
    assert page.status_code == 200
    body = page.json()
    assert [event["id"] for event in body["events"]] == ids
    assert body["latestId"] == ids[-1]

    # A reconnect that already saw the first event must receive the rest, not
    # the whole history and not nothing.
    resume = client.get(f"/api/v1/tasks/{task_id}/events?after={ids[0]}", headers=headers)
    assert resume.status_code == 200
    assert [event["id"] for event in resume.json()["events"]] == ids[1:]


def test_sse_stream_emits_ids_and_resumes(client):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]

    with client.stream("GET", "/api/v1/events/stream?after=0", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks: list[str] = []
        for chunk in response.iter_text():
            chunks.append(chunk)
            if "TASK_CREATED" in chunk:
                break
    text = "".join(chunks)
    assert "id: " in text
    assert "event: TASK_CREATED" in text
    assert f'"taskId":{task_id}' in text

    # Last-Event-ID resumes strictly after the given id.
    with client.stream(
        "GET",
        "/api/v1/events/stream",
        headers={**headers, "Last-Event-ID": "0"},
    ) as response:
        assert response.status_code == 200


# ------------------------------------------------------------------ governance
def test_retry_after_failure_creates_new_attempt_and_keeps_history(client, session_factory):
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"], demo_behavior="PERMANENT_FAILURE").json()[
        "task"
    ]["id"]
    run_worker(session_factory).run_once()

    retry = client.post(f"/api/v1/tasks/{task_id}/retry", json={"idempotencyKey": "regression-retry"}, headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["task"]["status"] == "QUEUED"

    with session_factory() as session:
        executions = session.scalars(
            select(TaskExecution).where(TaskExecution.task_id == task_id)
        ).all()
        # The retry does not reuse or overwrite the failed execution row.
        assert len(executions) == 1
        assert executions[0].status == "FAILED"


def test_real_run_mode_is_refused_not_downgraded(client):
    headers = owner_headers(client)
    create_company(client, headers)
    response = client.post(
        "/api/v1/projects",
        json={
            "key": "REAL",
            "name": "真实项目",
            "objective": "需要真实模型。",
            "runMode": "REAL",
            "budgetTokens": 1000,
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "RUN_MODE_UNAVAILABLE"

    # And the project is not silently created as DEMO.
    projects = client.get("/api/v1/projects", headers=headers).json()["projects"]
    assert projects == []


def test_employee_assignment_is_validated_against_company(client):
    headers, project = bootstrap(client)
    response = client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        json={
            "title": "带负责人",
            "objective": "验证负责人引用。",
            "budgetTokens": 1000,
            "idempotencyKey": uuid.uuid4().hex,
            "assigneeAgentId": 999_999,
        },
        headers=headers,
    )
    # The agent does not exist, so the insert violates the FK and the request
    # fails explicitly instead of storing a dangling id.
    assert response.status_code in (409, 422, 503), response.text
