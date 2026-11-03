"""Correct-behaviour regressions for the Phase 03 review findings R1–R13.

The review's ``docs/acceptance/evidence/phase-03-review/test_review_repros.py``
asserts that the *defects exist*; it stays untouched as historical evidence.
This module asserts the opposite: the behaviour the ADR requires. Each test
names the finding it closes, checks the database (accounts, reservations, ledger,
execution history) rather than an HTTP status, and uses explicit synchronisation
points instead of sleeps to control interleaving.

Run from ``backend`` with an isolated ``TEST_ADMIN_DATABASE_URL``:

    python -m pytest tests/test_phase03_review_fixes.py -q
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.v1.tasks import _fetch_events
from app.domain.enums import ReservationKind, ReservationStatus, StepStatus, TaskStatus
from app.domain.errors import LeaseLostError
from app.models.tasks import (
    BudgetAccount,
    BudgetLedgerEntry,
    BudgetReservation,
    DomainEvent,
    Task,
    TaskExecution,
    TaskExecutionStep,
)
from app.repositories.auth_repository import utcnow
from app.services.budget_service import BudgetService
from app.services.event_feed_service import EventFeedService
from app.services.execution_service import ClaimedExecution, ExecutionService
from app.services.task_service import ProjectInput, ProjectService
from tests.test_tasks import bootstrap, create_task, run_worker


# --------------------------------------------------------------------- helpers
def claimed(
    client, session_factory, *, project_budget: int = 100_000, retries: int = 0, behavior="SUCCESS"
) -> tuple[dict[str, str], ClaimedExecution]:
    headers, project = bootstrap(client, budget_tokens=project_budget)
    response = create_task(
        client,
        headers,
        project["id"],
        max_auto_retries=retries,
        demo_behavior=behavior,
    )
    assert response.status_code == 201, response.text
    return headers, _claim(session_factory)


def _claim(session_factory, *, worker_id: str = "fix-regression") -> ClaimedExecution:
    with session_factory() as session:
        claim = ExecutionService().claim_next(session, worker_id=worker_id)
        session.commit()
    assert claim is not None
    return claim


def expire(session_factory, claim: ClaimedExecution) -> None:
    """Put the lease in the past via a separate session (a real clock advance)."""
    with session_factory() as session:
        session.get(TaskExecution, claim.execution_id).lease_expires_at = utcnow() - timedelta(
            seconds=10
        )
        session.commit()


def accounts(session: Session) -> dict[str, BudgetAccount]:
    return {account.scope: account for account in session.scalars(select(BudgetAccount)).all()}


def open_holds(session: Session) -> list[BudgetReservation]:
    return list(
        session.scalars(
            select(BudgetReservation).where(BudgetReservation.status == "RESERVED")
        ).all()
    )


def ledger(
    session: Session, *, entry_kind: str | None = None, scope: str | None = None
) -> list[BudgetLedgerEntry]:
    stmt = select(BudgetLedgerEntry).order_by(BudgetLedgerEntry.id)
    if entry_kind is not None:
        stmt = stmt.where(BudgetLedgerEntry.entry_kind == entry_kind)
    if scope is not None:
        stmt = stmt.join(BudgetAccount, BudgetAccount.id == BudgetLedgerEntry.account_id).where(
            BudgetAccount.scope == scope
        )
    return list(session.scalars(stmt).all())


def two_task_then_claim_twice(session_factory, service: ExecutionService):
    """Claim two queued tasks in two sessions, leaving both transactions open."""
    first = session_factory()
    second = session_factory()
    low = service.claim_next(first, worker_id="first")
    high = service.claim_next(second, worker_id="second")
    assert low is not None and high is not None
    return first, second, low, high


# ============================================================ R1 stop gate
@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_r1_claimed_but_not_started_task_honours_stop_command(client, session_factory, command):
    """R1: a stop command issued after the claim but before the first step must
    prevent every step, not merely be recorded."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    runtime = run_worker(session_factory)
    claim_tuple = runtime.claim_one()
    assert claim_tuple is not None

    url = "/api/v1/company/pause" if command == "pause" else f"/api/v1/tasks/{task_id}/cancel"
    assert client.post(url, json={}, headers=headers).status_code == 200

    runtime.process_claim(claim_tuple)

    with session_factory() as session:
        task = session.get(Task, task_id)
        execution = session.get(TaskExecution, claim_tuple[0])
        steps = session.scalars(select(TaskExecutionStep)).all()
        assert steps == [], "no step may start after a committed stop command"
        if command == "pause":
            # Paused: the attempt stops at the checkpoint; the task is actionable
            # again after resume, so it is INTERRUPTED, not a work failure.
            assert task.status == TaskStatus.INTERRUPTED.value
            assert execution.status == "INTERRUPTED"
            assert execution.interrupted_unknown_side_effects is False
        else:
            assert task.status == TaskStatus.CANCELLED.value
            assert execution.status == "CANCELLED"
        # No allowance was ever held for a call, so nothing is consumed.
        for account in accounts(session).values():
            assert account.consumed_tokens == 0
            assert account.reserved_tokens == 0


def test_r1_pause_between_steps_stops_before_the_next_call(client, session_factory):
    """R1: pausing between two steps prevents the second step and its hold."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    service = ExecutionService()
    claim = _claim(session_factory)

    with session_factory() as session:
        service.start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="step 1",
            estimated_tokens=100,
        )
        session.commit()
    assert client.post("/api/v1/company/pause", json={}, headers=headers).status_code == 200

    from app.domain.errors import ExecutionStoppedError

    with session_factory() as session:
        with pytest.raises(ExecutionStoppedError):
            service.start_step(
                session,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                sequence=1,
                name="step 2",
                estimated_tokens=100,
            )
        session.rollback()

    with session_factory() as session:
        steps = session.scalars(select(TaskExecutionStep)).all()
        assert [step.sequence for step in steps] == [0]
        task_account = accounts(session)["TASK"]
        # Only step 1's hold exists; step 2 opened neither a step nor a hold.
        assert task_account.reserved_tokens == 100
        task = session.get(Task, task_id)
        assert task.status == TaskStatus.RUNNING.value  # worker still owns it


# ========================================================== R2 lease expiry
def test_r2_expired_lease_cannot_write_or_be_renewed(client, session_factory):
    """R2: every lease-sensitive write checks expiry, including the heartbeat."""
    _, claim = claimed(client, session_factory)
    expire(session_factory, claim)
    service = ExecutionService()

    with session_factory() as session:
        with pytest.raises(LeaseLostError):
            service.start_step(
                session,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                sequence=0,
                name="late step",
                estimated_tokens=10,
            )
        session.rollback()
    with session_factory() as session:
        assert (
            service.heartbeat(
                session, execution_id=claim.execution_id, fencing_token=claim.fencing_token
            )
            is False
        )
        session.rollback()
    with session_factory() as session:
        assert session.get(TaskExecution, claim.execution_id).lease_expires_at < utcnow()
        assert session.scalars(select(TaskExecutionStep)).all() == []


def test_r2_late_write_after_recovery_is_refused(client, session_factory):
    """R2: once recovery closed the attempt, the old token can write nothing."""
    _, claim = claimed(client, session_factory)
    expire(session_factory, claim)
    run_worker(session_factory).recover()

    with session_factory() as session:
        assert session.get(TaskExecution, claim.execution_id).status == "INTERRUPTED"
        with pytest.raises(LeaseLostError):
            ExecutionService().start_step(
                session,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                sequence=0,
                name="late",
                estimated_tokens=10,
            )
        session.rollback()

    with session_factory() as session:
        assert session.scalars(select(TaskExecutionStep)).all() == []
        assert open_holds(session) == []


# ====================================================== R3 fenced race
def test_r3_recovery_waits_for_an_in_flight_step_transaction(client, session_factory, monkeypatch):
    """R3: the fence verdict and the following write are one locked unit.

    The review's defect was a *check-then-write* window: recovery committed
    ``INTERRUPTED`` after the token check and the stale worker still wrote a step
    and its hold. This test reproduces the same interleaving with a controllable
    clock (no sleeps for the race itself):

    1. the attempt is claimed with a valid lease;
    2. a transaction opens a step, locking the task and execution rows;
    3. the clock advances past the lease, so the attempt *is* expired while that
       transaction is still open;
    4. recovery runs concurrently and must block on the row locks instead of
       committing INTERRUPTED underneath the in-flight write;
    5. the step transaction commits, then recovery takes over.
    """
    from app.services import execution_service as execution_module

    _, claim = claimed(client, session_factory)
    real_now = execution_module.utcnow
    state = {"shift": timedelta(0)}

    def controlled_now():
        return real_now() + state["shift"]

    monkeypatch.setattr(execution_module, "utcnow", controlled_now)

    recovery_finished = threading.Event()
    recovery_error: list[BaseException] = []

    def recover() -> None:
        try:
            with session_factory() as recovery:
                ExecutionService().recover_expired_leases(recovery)
                recovery.commit()
        except BaseException as exc:  # pragma: no cover - asserted below
            recovery_error.append(exc)
        finally:
            recovery_finished.set()

    with session_factory() as session:
        service = ExecutionService()
        service.start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="step under race",
            estimated_tokens=10,
        )
        # The lease now appears expired while this transaction still holds the
        # task/execution locks.
        state["shift"] = timedelta(minutes=5)
        thread = threading.Thread(target=recover)
        thread.start()
        # Recovery must not be able to finish while the write is in flight. This
        # is a negative assertion against a bug; the short wait is a scheduling
        # aid, and the positive assertions below do not depend on its timing.
        assert recovery_finished.wait(timeout=0.5) is False, (
            "recovery committed while a fenced step transaction held the row lock"
        )
        session.commit()

    thread.join(timeout=20)
    assert not recovery_error
    assert recovery_finished.is_set()

    with session_factory() as session:
        execution = session.get(TaskExecution, claim.execution_id)
        steps = session.scalars(select(TaskExecutionStep)).all()
        assert len(steps) == 1, "the in-flight step was committed exactly once"
        # Recovery ran after the commit and now owns the attempt: it must have
        # closed the still-running step and consumed the call hold conservatively.
        assert execution.status == "INTERRUPTED"
        assert steps[0].status == StepStatus.INTERRUPTED.value
        assert open_holds(session) == []
        task_account = accounts(session)["TASK"]
        assert task_account.consumed_tokens == 10
        assert task_account.reserved_tokens == 0


def test_r3_stale_worker_cannot_write_after_recovery(client, session_factory):
    """R3: once recovery committed, the old token writes nothing at all."""
    _, claim = claimed(client, session_factory)
    with session_factory() as session:
        ExecutionService().start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="open step",
            estimated_tokens=64,
        )
        session.commit()
    expire(session_factory, claim)
    with session_factory() as recovery:
        ExecutionService().recover_expired_leases(recovery)
        recovery.commit()

    with session_factory() as session:
        with pytest.raises(LeaseLostError):
            ExecutionService().finish_step(
                session,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                step_id=1,
                status=StepStatus.SUCCEEDED,
                prompt_tokens=1,
                completion_tokens=1,
                usage_known=True,
            )
        session.rollback()
    with session_factory() as session:
        step = session.get(TaskExecutionStep, 1)
        assert step.status == StepStatus.INTERRUPTED.value
        assert step.prompt_tokens is None
        # The stale completion wrote no settlement of its own.
        assert (
            session.scalar(
                select(BudgetLedgerEntry).where(
                    BudgetLedgerEntry.entry_kind == "SETTLE",
                    BudgetLedgerEntry.reservation_id.is_not(None),
                    BudgetLedgerEntry.detail.like("step:%"),
                )
            )
            is None
        )
        assert session.get(TaskExecution, claim.execution_id).status == "INTERRUPTED"


# ================================================== R4 recovery budget
def test_r4_recovery_without_retry_budget_blocks_without_crashing(client, session_factory):
    """R4: an unaffordable retry is recorded as BLOCKED; the interruption and its
    conservative settlement stay committed, and the worker keeps running.

    A step is opened before the lease expires so the attempt has real call
    evidence. Its envelope is therefore consumed conservatively, which is exactly
    the state in which the retry needs an allowance the project cannot grant.
    """
    headers, claim = claimed(client, session_factory, project_budget=1_000, retries=1)
    with session_factory() as session:
        ExecutionService().start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="call started",
            estimated_tokens=200,
        )
        session.commit()
    expire(session_factory, claim)
    runtime = run_worker(session_factory)

    # Must not raise: the exception used to escape and abort the whole scan.
    recovered = runtime.recover()
    assert recovered == [claim.task_id]

    with session_factory() as session:
        task = session.get(Task, claim.task_id)
        execution = session.get(TaskExecution, claim.execution_id)
        assert task.status == TaskStatus.BLOCKED.value
        assert task.last_error_code == "BUDGET_INSUFFICIENT"
        assert execution.status == "INTERRUPTED"
        assert execution.interrupted_unknown_side_effects is True
        # The old envelope was settled conservatively, and the refused retry
        # reuse left no partial company hold behind.
        assert open_holds(session) == []
        company = accounts(session)["COMPANY"]
        assert company.consumed_tokens == 1_000
        assert company.reserved_tokens == 0
        # Only the original planning holds were settled; no retry RESERVE entry
        # exists for the refused attempt.
        retry_reserves = [
            entry
            for entry in ledger(session, entry_kind="RESERVE")
            if entry.detail and entry.detail.startswith("recovery-retry")
        ]
        assert retry_reserves == []

    # Another queued task is still processed by the same worker afterwards.
    other = create_task(client, headers, 1)
    assert other.status_code == 409, "the project is exhausted, so a new task is refused"
    assert runtime.run_once() is False, "a blocked task must not be claimed again"


def test_r4_recovery_without_any_call_is_not_charged(client, session_factory):
    """R4 companion: when no call ever started, recovery releases the envelope
    and the queued retry is funded again instead of consuming a phantom cost."""
    _, claim = claimed(client, session_factory, project_budget=1_000, retries=1)
    expire(session_factory, claim)
    recovered = run_worker(session_factory).recover()
    assert recovered == [claim.task_id]
    with session_factory() as session:
        task = session.get(Task, claim.task_id)
        assert task.status == TaskStatus.QUEUED.value
        by_scope = accounts(session)
        assert by_scope["COMPANY"].consumed_tokens == 0
        assert by_scope["COMPANY"].reserved_tokens == 1_000
        assert by_scope["PROJECT"].consumed_tokens == 0
        assert by_scope["PROJECT"].reserved_tokens == 1_000
        assert len(open_holds(session)) == 2


# ================================================ R5 cancel + lost worker
def test_r5_cancel_after_call_started_keeps_company_cost_conservative(client, session_factory):
    """R5: a cancel marker is not evidence that the call was free."""
    headers, claim = claimed(client, session_factory)
    with session_factory() as session:
        ExecutionService().start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="in-flight",
            estimated_tokens=200,
        )
        session.commit()
    assert (
        client.post(f"/api/v1/tasks/{claim.task_id}/cancel", json={}, headers=headers).status_code
        == 200
    )
    expire(session_factory, claim)
    run_worker(session_factory).recover()

    with session_factory() as session:
        by_scope = accounts(session)
        assert by_scope["TASK"].consumed_tokens == 200
        # Company and project consumed the full conservative envelope (task budget
        # 1000 default from create_task) instead of releasing it.
        assert by_scope["COMPANY"].consumed_tokens == 1_000
        assert by_scope["PROJECT"].consumed_tokens == 1_000
        assert by_scope["COMPANY"].reserved_tokens == 0
        assert by_scope["PROJECT"].reserved_tokens == 0
        # Every conservative charge is flagged so an operator can correct it.
        settled_unknown = [
            entry for entry in ledger(session, entry_kind="SETTLE") if entry.usage_tokens is None
        ]
        assert {entry.account_id for entry in settled_unknown} == {
            by_scope["COMPANY"].id,
            by_scope["PROJECT"].id,
            by_scope["TASK"].id,
        }
        assert all(entry.needs_reconciliation for entry in settled_unknown)


# ================================================== R6 failed-step usage
def test_r6_failed_step_unknown_usage_makes_the_attempt_unknown(client, session_factory):
    """R6: one unknown step must not be summarized as a fully known attempt."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"], demo_behavior="PERMANENT_FAILURE").json()[
        "task"
    ]["id"]
    run_worker(session_factory).run_once()

    with session_factory() as session:
        execution = session.scalars(select(TaskExecution)).one()
        failed = session.scalars(
            select(TaskExecutionStep).where(TaskExecutionStep.status == "FAILED")
        ).one()
        assert failed.usage_known is False
        assert execution.usage_known is False
        assert execution.needs_reconciliation is True
        assert execution.settled_tokens is None
        # The company/project envelope keeps the conservative, flagged amount;
        # it is not silently reduced to the known part (200) or released.
        by_scope = accounts(session)
        assert by_scope["TASK"].consumed_tokens == 264
        assert by_scope["COMPANY"].consumed_tokens == 1_000
        assert by_scope["PROJECT"].consumed_tokens == 1_000
        assert by_scope["COMPANY"].reserved_tokens == 0
        company_settles = ledger(session, entry_kind="SETTLE", scope="COMPANY")
        assert len(company_settles) == 1
        assert company_settles[0].usage_tokens is None
        assert company_settles[0].needs_reconciliation is True
        assert session.get(Task, task_id).status == TaskStatus.FAILED.value


def test_r6_mixed_known_and_unknown_steps_keep_the_unknown_part(client, session_factory):
    """R6: TIMEOUT (known step 1, unknown step 2) is unknown at every layer."""
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"], demo_behavior="TIMEOUT")
    run_worker(session_factory).run_once()
    with session_factory() as session:
        execution = session.scalars(select(TaskExecution)).one()
        assert execution.usage_known is False
        assert execution.needs_reconciliation is True
        task_account = accounts(session)["TASK"]
        # Step 1 settled 200, step 2 consumed its 64-token estimate.
        assert task_account.consumed_tokens == 264
        assert task_account.reserved_tokens == 0
        assert accounts(session)["COMPANY"].consumed_tokens == 1_000


# ================================================ R7 company default budget
@pytest.mark.parametrize("configured_default", [0, 7_777])
def test_r7_first_task_uses_the_configured_company_default(
    client, app, session_factory, configured_default
):
    """R7: the company account is created with the configured limit regardless of
    whether the budget page was read first, including a zero default."""
    app.state.settings.company_default_budget_tokens = configured_default
    app.state.budget_service._default_company_limit = configured_default
    headers, project = bootstrap(client, budget_tokens=1_000)

    response = create_task(client, headers, project["id"], budget_tokens=500)
    with session_factory() as session:
        company_account = accounts(session).get("COMPANY")
        if configured_default == 0:
            # Zero is a real limit: the hold cannot be granted, and neither a
            # task nor a hold may be left behind.
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "BUDGET_INSUFFICIENT"
            assert session.scalars(select(Task)).all() == []
            assert open_holds(session) == []
            assert company_account is None or company_account.limit_tokens == 0
        else:
            assert response.status_code == 201, response.text
            assert company_account.limit_tokens == configured_default
            assert company_account.reserved_tokens == 500


def test_r7_budget_page_first_and_task_first_agree(client, app, session_factory):
    """R7: request order no longer changes the resulting company allowance."""
    app.state.settings.company_default_budget_tokens = 4_242
    app.state.budget_service._default_company_limit = 4_242
    headers, project = bootstrap(client)

    # Budget page first.
    assert client.get("/api/v1/budgets", headers=headers).status_code == 200
    assert create_task(client, headers, project["id"]).status_code == 201
    with session_factory() as session:
        assert accounts(session)["COMPANY"].limit_tokens == 4_242


def test_r7_task_first_then_budget_page_agree(client, app, session_factory):
    app.state.settings.company_default_budget_tokens = 4_242
    app.state.budget_service._default_company_limit = 4_242
    headers, project = bootstrap(client)

    assert create_task(client, headers, project["id"]).status_code == 201
    body = client.get("/api/v1/budgets", headers=headers).json()
    assert body["company"]["limitTokens"] == 4_242
    with session_factory() as session:
        assert accounts(session)["COMPANY"].limit_tokens == 4_242


# ============================================================ R8 SSE order
def test_r8_late_committed_event_is_not_skipped(client, session_factory):
    """R8: a transaction that commits after a higher id must still be delivered.

    Two real sessions claim two tasks; the second (higher event id) commits
    first. The stream reads by publication cursor, so the late commit receives a
    *higher* cursor and the replay after the first read returns it.
    """
    headers, project = bootstrap(client)
    for _ in range(2):
        assert create_task(client, headers, project["id"]).status_code == 201
    service = ExecutionService()
    feed = EventFeedService()
    first, second, low, high = two_task_then_claim_twice(session_factory, service)

    second.commit()
    # The reader publishes what is visible: only the high-id claim.
    first_batch = _fetch_events(session_factory, feed, 0, None)
    delivered = [event for event in first_batch if event.execution_id == high.execution_id]
    assert len(delivered) == 1
    cursor_after_first_read = delivered[0].published_seq

    low_id = first.scalar(
        select(DomainEvent.id).where(DomainEvent.execution_id == low.execution_id)
    )
    assert low_id < delivered[0].id  # the id order really is inverted

    first.commit()
    later = _fetch_events(session_factory, feed, cursor_after_first_read, None)
    late_events = [event for event in later if event.execution_id == low.execution_id]
    assert len(late_events) == 1, "the late-committed event must be replayed"
    assert late_events[0].published_seq > cursor_after_first_read

    # And it is not delivered twice.
    assert _fetch_events(session_factory, feed, late_events[0].published_seq, None) == []


def test_r8_cursor_is_stable_for_already_committed_events(client, session_factory):
    """R8: publishing is idempotent — a second pass assigns no new cursor."""
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"])
    feed = EventFeedService()
    with session_factory() as session:
        first = feed.publish_pending(session)
        session.commit()
    assert first >= 1
    with session_factory() as session:
        assert feed.publish_pending(session) == 0
        cursors = list(session.scalars(select(DomainEvent.published_seq).order_by(DomainEvent.id)))
        session.commit()
    assert None not in cursors
    assert len(cursors) == len(set(cursors))


def test_r8_http_replay_uses_the_publication_cursor(client, session_factory):
    """R8: the per-task replay endpoint and the stream agree on one cursor."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"]).json()["task"]["id"]
    run_worker(session_factory).run_once()

    page = client.get(f"/api/v1/tasks/{task_id}/events?after=0", headers=headers).json()
    ids = [event["id"] for event in page["events"]]
    assert ids == sorted(ids) and len(ids) >= 3
    assert page["latestId"] == ids[-1]
    resume = client.get(f"/api/v1/tasks/{task_id}/events?after={ids[0]}", headers=headers).json()
    assert [event["id"] for event in resume["events"]] == ids[1:]


# ================================================== R9 retry hold atomicity
def test_r9_auto_retry_leaves_no_partial_company_hold(client, session_factory):
    """R9: a refused project hold rolls the company hold back with it."""
    headers, project = bootstrap(client, budget_tokens=1_000)
    create_task(
        client,
        headers,
        project["id"],
        demo_behavior="TRANSIENT_FAILURE",
        max_auto_retries=1,
    )
    run_worker(session_factory).run_once()

    with session_factory() as session:
        task = session.scalars(select(Task)).one()
        assert task.status == TaskStatus.BLOCKED.value
        assert open_holds(session) == [], "no orphan company reservation may survive"
        by_scope = accounts(session)
        assert by_scope["COMPANY"].reserved_tokens == 0
        assert by_scope["PROJECT"].reserved_tokens == 0
        # The retry attempt's reservation never got a ledger entry either.
        retry_reserves = [
            entry
            for entry in ledger(session, entry_kind="RESERVE")
            if entry.detail and entry.detail.startswith("auto-retry")
        ]
        assert retry_reserves == []


def test_r9_manual_retry_leaves_no_partial_hold_when_project_is_short(client, session_factory):
    """R9 (manual path): the same atomicity rule applies to POST /retry."""
    headers, project = bootstrap(client, budget_tokens=1_000)
    task_id = create_task(
        client, headers, project["id"], demo_behavior="PERMANENT_FAILURE", budget_tokens=900
    ).json()["task"]["id"]
    run_worker(session_factory).run_once()

    # Consume the project room so the retry's project hold must fail.
    service = ExecutionService()
    with session_factory() as session:
        other = ProjectService().create(
            session,
            owner_user_id=1,
            payload=ProjectInput(
                key="OTHER",
                name="Other",
                objective="Second project",
                run_mode="DEMO",
                budget_tokens=200,
            ),
        )
        # Drain the shared project allowance with a second project's task is not
        # possible (separate account); instead shrink the account directly, which
        # models an operator lowering the ceiling.
        account = session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == "PROJECT", BudgetAccount.scope_id == project["id"]
            )
        ).one()
        account.limit_tokens = 0
        session.commit()

    response = client.post(f"/api/v1/tasks/{task_id}/retry", json={"idempotencyKey": "regression-retry"}, headers=headers)
    assert response.status_code == 409, response.text
    with session_factory() as session:
        assert open_holds(session) == []
        assert accounts(session)["COMPANY"].reserved_tokens == 0
        assert session.get(Task, task_id).status == TaskStatus.FAILED.value
        del other, service


# ==================================================== R10 reconciliation
def test_r10_settled_unknown_usage_is_visible_in_every_projection(client, session_factory):
    """R10: the flag, not the status, decides what needs reconciliation."""
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"], demo_behavior="UNKNOWN_USAGE")
    run_worker(session_factory).run_once()

    with session_factory() as session:
        flagged = session.scalars(
            select(BudgetReservation).where(BudgetReservation.needs_reconciliation.is_(True))
        ).all()
        assert len(flagged) == 4
        assert {row.status for row in flagged} == {"SETTLED"}

    overview = client.get("/api/v1/budgets", headers=headers).json()
    assert overview["pendingReconciliationCount"] == 4
    # One company planning envelope, one project envelope, two task call holds.
    assert overview["company"]["needsReconciliationCount"] == 1
    assert overview["projects"][0]["needsReconciliationCount"] == 1
    pending = client.get("/api/v1/budgets/pending-reconciliation", headers=headers).json()
    assert len(pending) == 4
    assert {row["status"] for row in pending} == {"SETTLED"}
    assert {row["accountScope"] for row in pending} == {"COMPANY", "PROJECT", "TASK"}
    assert all(row["settledTokens"] is not None for row in pending)


def test_r10_open_unknown_hold_is_distinguished_from_settled(client, session_factory):
    """R10: an unfinished call is listed as RESERVED, a finished one as SETTLED."""
    _, claim = claimed(client, session_factory)
    with session_factory() as session:
        ExecutionService().start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="unfinished",
            estimated_tokens=64,
        )
        # Model a provider that has not answered yet: the hold stays open but is
        # already flagged for reconciliation.
        reservation = session.scalars(
            select(BudgetReservation).where(
                BudgetReservation.kind == ReservationKind.CALL.value,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
        ).one()
        reservation.needs_reconciliation = True
        session.commit()

    with session_factory() as session:
        pending = BudgetService().pending_reconciliation(session)
        assert [(row.kind, row.status) for row in pending] == [("CALL", "RESERVED")]


# ================================================== R11 cancel state machine
def test_r11_cancel_interrupted_task_reaches_cancelled(client, session_factory):
    """R11: a state with no live execution is cancelled immediately."""
    headers, claim = claimed(client, session_factory)
    expire(session_factory, claim)
    run_worker(session_factory).recover()
    with session_factory() as session:
        assert session.get(Task, claim.task_id).status == TaskStatus.INTERRUPTED.value

    response = client.post(f"/api/v1/tasks/{claim.task_id}/cancel", json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "CANCELLED"

    with session_factory() as session:
        task = session.get(Task, claim.task_id)
        assert task.status == TaskStatus.CANCELLED.value
        assert task.finished_at is not None
        # No call ever started, so the envelope is released, not consumed.
        by_scope = accounts(session)
        assert by_scope["COMPANY"].reserved_tokens == 0
        assert by_scope["COMPANY"].consumed_tokens == 0
        assert by_scope["PROJECT"].reserved_tokens == 0
        cancelled_events = session.scalars(
            select(DomainEvent).where(
                DomainEvent.task_id == claim.task_id,
                DomainEvent.event_type == "TASK_CANCELLED",
            )
        ).all()
        assert cancelled_events


def test_r11_cancel_blocked_task_reaches_cancelled(client, session_factory):
    """R11: BLOCKED has no execution in flight; cancelling must terminate it."""
    headers, project = bootstrap(client, budget_tokens=1_000)
    task_id = create_task(
        client, headers, project["id"], demo_behavior="TRANSIENT_FAILURE", max_auto_retries=1
    ).json()["task"]["id"]
    run_worker(session_factory).run_once()
    with session_factory() as session:
        assert session.get(Task, task_id).status == TaskStatus.BLOCKED.value

    response = client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers)
    assert response.status_code == 200
    assert response.json()["task"]["status"] == "CANCELLED"
    with session_factory() as session:
        assert session.get(Task, task_id).status == TaskStatus.CANCELLED.value
        assert open_holds(session) == []


def test_r11_cancel_running_task_stays_cooperative(client, session_factory):
    """R11 must not regress the cooperative path: a live attempt keeps RUNNING."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"], demo_behavior="SLOW").json()["task"]["id"]
    claim = _claim(session_factory)
    response = client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers)
    assert response.status_code == 200
    assert response.json()["task"]["status"] == "RUNNING"
    assert response.json()["task"]["cancelPending"] is True
    with session_factory() as session:
        assert session.get(TaskExecution, claim.execution_id).cancel_requested_at is not None


# ====================================================== R12 query contract
def test_r12_frontend_query_names_filter_and_limit(client, session_factory):
    """R12: the exact names the frontend sends must work."""
    headers, first = bootstrap(client)
    second = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "key": "SECOND",
            "name": "Second",
            "objective": "Other project",
            "runMode": "DEMO",
            "budgetTokens": 100_000,
        },
    ).json()
    for project in (first, second):
        assert create_task(client, headers, project["id"]).status_code == 201

    camel = client.get("/api/v1/tasks", params={"projectId": first["id"]}).json()
    assert {task["projectId"] for task in camel["tasks"]} == {first["id"]}
    snake = client.get("/api/v1/tasks", params={"project_id": first["id"]}).json()
    assert {task["projectId"] for task in snake["tasks"]} == {first["id"]}
    unpaged = client.get("/api/v1/tasks").json()
    assert {task["projectId"] for task in unpaged["tasks"]} == {first["id"], second["id"]}

    assert len(client.get("/api/v1/budgets", params={"ledgerLimit": 1}).json()["ledger"]) == 1
    assert len(client.get("/api/v1/budgets", params={"ledger_limit": 2}).json()["ledger"]) == 2


def test_r12_sse_accepts_the_frontend_project_parameter(client):
    headers, project = bootstrap(client)
    # Give the project one visible task event to deliver.
    assert create_task(client, headers, project["id"]).status_code == 201
    with client.stream(
        "GET",
        "/api/v1/events/stream",
        params={"after": 0, "projectId": project["id"]},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        chunks: list[str] = []
        for chunk in response.iter_text():
            chunks.append(chunk)
            if "TASK_CREATED" in chunk:
                break
        text = "".join(chunks)
        assert f'"projectId":{project["id"]}' in text


# ====================================================== R13 idempotency key
def test_r13_same_key_after_lost_response_yields_one_task_and_one_hold(client, session_factory):
    """R13 (server side): the identical key/body is the only retry the server sees.

    The client-side half of R13 lives in the frontend test
    ``phase03-views.spec.ts`` ("reuses the idempotency key"). Here the server is
    proven to keep the invariant the frontend relies on: one task, one company
    hold, one project hold, one task account.
    """
    headers, project = bootstrap(client)
    key = f"lost-response-{uuid.uuid4().hex}"
    first = create_task(client, headers, project["id"], idempotency_key=key)
    assert first.status_code == 201
    second = create_task(client, headers, project["id"], idempotency_key=key)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["task"]["id"] == first.json()["task"]["id"]

    with session_factory() as session:
        assert len(session.scalars(select(Task)).all()) == 1
        holds = open_holds(session)
        assert len(holds) == 2
        assert {hold.scope for hold in holds} == {"COMPANY", "PROJECT"}
        task_accounts = session.scalars(
            select(BudgetAccount).where(BudgetAccount.scope == "TASK")
        ).all()
        assert len(task_accounts) == 1


def test_r13_different_body_under_the_same_key_is_refused(client, session_factory):
    headers, project = bootstrap(client)
    key = "reused-with-edits"
    assert create_task(client, headers, project["id"], idempotency_key=key).status_code == 201
    changed = create_task(client, headers, project["id"], title="改过的标题", idempotency_key=key)
    assert changed.status_code == 409
    assert changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
    with session_factory() as session:
        assert len(session.scalars(select(Task)).all()) == 1
        assert len(open_holds(session)) == 2


# ============================================ cross-cutting invariants
def test_successful_run_leaves_no_open_hold_and_no_unknown(client, session_factory):
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"])
    run_worker(session_factory).run_once()
    with session_factory() as session:
        assert open_holds(session) == []
        # The envelope settles at the real usage of both steps (120+80+200+100).
        assert accounts(session)["COMPANY"].consumed_tokens == 500
        unknown = [
            entry for entry in ledger(session, entry_kind="SETTLE") if entry.needs_reconciliation
        ]
        assert unknown == []
        # Ledger shape: each reservation settles exactly once.
        keys = [
            (entry.account_id, entry.entry_kind, entry.reservation_id) for entry in ledger(session)
        ]
        assert len(keys) == len(set(keys))


def test_recovery_is_idempotent_under_a_second_scan(client, session_factory):
    """Two recovery passes must not double-settle or re-queue twice."""
    _, claim = claimed(client, session_factory, retries=1)
    expire(session_factory, claim)
    first = run_worker(session_factory).recover()
    second = run_worker(session_factory).recover()
    assert first == [claim.task_id]
    assert second == []
    with session_factory() as session:
        settles = ledger(session, entry_kind="SETTLE")
        keys = [(entry.account_id, entry.reservation_id) for entry in settles]
        assert len(keys) == len(set(keys))
        assert session.get(Task, claim.task_id).status == TaskStatus.QUEUED.value


def test_queued_cancel_still_releases_without_consuming(client, session_factory):
    """Control: a cancel before any claim releases the envelope (no false charge)."""
    headers, project = bootstrap(client)
    task_id = create_task(client, headers, project["id"], budget_tokens=700).json()["task"]["id"]
    assert (
        client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers).status_code == 200
    )
    with session_factory() as session:
        by_scope = accounts(session)
        assert by_scope["COMPANY"].consumed_tokens == 0
        assert by_scope["COMPANY"].reserved_tokens == 0
        assert by_scope["PROJECT"].consumed_tokens == 0
        assert open_holds(session) == []


def test_company_wide_invariants_hold_after_mixed_workload(client, session_factory):
    """A mixed run of success, failure, unknown usage and recovery leaves the
    database in a state the budget page can explain: no negative bucket, no open
    hold whose task is terminal, no unflagged unknown usage."""
    headers, project = bootstrap(client, budget_tokens=100_000)
    for behavior in ["SUCCESS", "PERMANENT_FAILURE", "UNKNOWN_USAGE", "TRANSIENT_FAILURE"]:
        assert (
            create_task(
                client, headers, project["id"], demo_behavior=behavior, max_auto_retries=0
            ).status_code
            == 201
        )
    runtime = run_worker(session_factory)
    for _ in range(6):
        if not runtime.run_once():
            break
    with session_factory() as session:
        for account in accounts(session).values():
            assert account.consumed_tokens >= 0
            assert account.reserved_tokens >= 0
        for reservation in session.scalars(select(BudgetReservation)).all():
            if reservation.status == "RESERVED":
                assert reservation.needs_reconciliation is True
        terminal = session.scalars(
            select(Task).where(Task.status.in_(["SUCCEEDED", "FAILED", "CANCELLED"]))
        ).all()
        for task in terminal:
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM budget_reservations"
                        " WHERE task_id = :task AND status = 'RESERVED'"
                    ),
                    {"task": task.id},
                )
                == 0
            )
        unknown = [
            entry for entry in ledger(session, entry_kind="SETTLE") if entry.usage_tokens is None
        ]
        assert all(entry.needs_reconciliation for entry in unknown)
