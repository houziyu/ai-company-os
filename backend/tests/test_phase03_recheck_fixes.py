"""Regressions for V2-1 through V2-5, using real PostgreSQL transactions.

Promoted from phase-03-recheck evidence. Lock hooks observe the corrected
SELECT-for-update heartbeat and the recovery lock request (not its acquisition):
recovery now waits behind the step at the company row. Historical evidence stays intact.
"""

import threading
import time
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select, text

from app.domain.enums import StepStatus
from app.main import BACKEND_ROOT
from app.models.tasks import (
    BudgetAccount,
    BudgetReservation,
    DomainEvent,
    Task,
    TaskExecution,
    TaskExecutionStep,
)
from app.services.event_feed_service import EventFeedService
from app.services.execution_service import ExecutionService
from tests.test_phase03_review_fixes import _claim, claimed, expire
from tests.test_tasks import bootstrap, create_task, run_worker


def test_cancel_queued_retry_does_not_charge_previous_attempt_again(client, session_factory):
    headers, project = bootstrap(client)
    response = create_task(
        client, headers, project["id"], demo_behavior="PERMANENT_FAILURE", max_auto_retries=0
    )
    task_id = response.json()["task"]["id"]
    run_worker(session_factory).run_once()
    with session_factory() as session:
        before = session.scalar(
            select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == "COMPANY")
        )
    assert before == 1000
    assert (
        client.post(f"/api/v1/tasks/{task_id}/retry", json={"idempotencyKey": "regression-retry"}, headers=headers).status_code == 200
    )
    assert (
        client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers).status_code == 200
    )
    with session_factory() as session:
        after = session.scalar(
            select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == "COMPANY")
        )
    assert after == before, (
        f"Queued retry made no call, but consumed increased from {before} to {after}"
    )


def test_cancelled_unknown_step_consumes_its_task_hold(client, session_factory):
    headers, claim = claimed(client, session_factory)
    service = ExecutionService()
    with session_factory() as session:
        step = service.start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="unknown in-flight call",
            estimated_tokens=64,
        )
        session.commit()
    assert (
        client.post(f"/api/v1/tasks/{claim.task_id}/cancel", json={}, headers=headers).status_code
        == 200
    )
    with session_factory() as session:
        service.finish_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            step_id=step.id,
            status=StepStatus.CANCELLED,
            prompt_tokens=None,
            completion_tokens=None,
            usage_known=False,
        )
        session.commit()
    with session_factory() as session:
        hold = session.scalars(
            select(BudgetReservation).where(BudgetReservation.step_id == step.id)
        ).one()
        assert hold.status == "SETTLED", (
            f"Unknown call hold was {hold.status}, amount={hold.amount_tokens}"
        )
        assert hold.needs_reconciliation
        assert (
            session.scalar(
                select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == "TASK")
            )
            == 64
        )


def test_migration_keeps_old_cursor_when_event_ids_have_gaps(
    client, session_factory, database_url, monkeypatch
):
    bootstrap(client)
    with session_factory() as session:
        session.add(DomainEvent(company_id=1, event_type="ROLLED_BACK", payload={}))
        session.flush()
        session.rollback()  # PostgreSQL sequences do not roll back: make a real gap.
    with session_factory() as session:
        row = DomainEvent(company_id=1, event_type="OLD_DELIVERED", payload={})
        session.add(row)
        session.commit()
        old_cursor = row.id
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.downgrade(config, "c41f7b93e2a5")
    command.upgrade(config, "head")
    with session_factory() as session:
        row = DomainEvent(company_id=1, event_type="AFTER_UPGRADE", payload={})
        session.add(row)
        session.commit()
        new_id = row.id
    feed = EventFeedService()
    feed.publish(session_factory)
    with session_factory() as session:
        new_seq = session.scalar(select(DomainEvent.published_seq).where(DomainEvent.id == new_id))
        replay = feed.read_after(session, cursor=old_cursor, limit=100)
    assert new_id in [e.id for e in replay], (
        f"old cursor={old_cursor}, new id={new_id}, new seq={new_seq}; event skipped"
    )


def test_step_start_and_recovery_do_not_deadlock(client, session_factory, engine, monkeypatch):
    _, claim = claimed(client, session_factory, retries=1)
    import app.services.execution_service as module

    now = module.utcnow()
    clock = {"value": now}
    monkeypatch.setattr(module, "utcnow", lambda: clock["value"])
    service = ExecutionService()
    reached_company_reserve = threading.Event()
    recovery_requested_company = threading.Event()
    allow_step_writes = threading.Event()
    recovery_pid = []
    original = service._assert_startable
    errors = []

    def gate(session, **kwargs):
        original(session, **kwargs)
        reached_company_reserve.set()  # start_step already owns task and execution.
        assert allow_step_writes.wait(10)

    monkeypatch.setattr(service, "_assert_startable", gate)

    def before_sql(conn, cursor, statement, parameters, context, executemany):
        if (
            threading.current_thread().name == "recheck-recovery"
            and "FROM companies" in statement
            and "FOR" in statement
        ):
            recovery_pid.append(conn.connection.driver_connection.info.backend_pid)
            recovery_requested_company.set()

    event.listen(engine, "before_cursor_execute", before_sql)

    def complete():
        try:
            with session_factory() as session:
                service.start_step(
                    session,
                    execution_id=claim.execution_id,
                    fencing_token=claim.fencing_token,
                    sequence=0,
                    name="concurrent step",
                    estimated_tokens=10,
                )
                session.commit()
        except Exception as exc:
            errors.append(
                (
                    "complete",
                    type(exc).__name__,
                    getattr(getattr(exc, "orig", None), "sqlstate", None),
                )
            )

    def recover():
        try:
            with session_factory() as session:
                ExecutionService().recover_expired_leases(session)
                session.commit()
        except Exception as exc:
            errors.append(
                (
                    "recover",
                    type(exc).__name__,
                    getattr(getattr(exc, "orig", None), "sqlstate", None),
                )
            )

    first = threading.Thread(target=complete, name="recheck-complete")
    second = threading.Thread(target=recover, name="recheck-recovery")
    try:
        first.start()
        assert reached_company_reserve.wait(10)
        clock["value"] = now + timedelta(minutes=5)
        second.start()
        assert recovery_requested_company.wait(10)
        deadline = time.monotonic() + 5
        waiting_on_company = False
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
            while time.monotonic() < deadline:
                activity = observer.execute(
                    text("SELECT wait_event_type, query FROM pg_stat_activity WHERE pid=:pid"),
                    {"pid": recovery_pid[0]},
                ).one()
                if activity.wait_event_type == "Lock":
                    waiting_on_company = "FROM companies" in activity.query
                    break
                time.sleep(0.01)
        assert waiting_on_company, "Recovery must wait at company, before taking task locks"
        allow_step_writes.set()
        first.join(20)
        second.join(20)
        assert not first.is_alive() and not second.is_alive()
        assert errors == [], f"Concurrent step/recovery errors: {errors}"
        with session_factory() as session:
            assert session.get(TaskExecution, claim.execution_id).status == "INTERRUPTED"
            assert session.get(Task, claim.task_id).status == "QUEUED"
            assert session.scalar(select(TaskExecutionStep.status)) == "INTERRUPTED"
            assert (
                session.scalar(
                    select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == "TASK")
                )
                == 10
            )
    finally:
        allow_step_writes.set()
        first.join(20)
        if second.ident is not None:
            second.join(20)
        event.remove(engine, "before_cursor_execute", before_sql)


def test_heartbeat_waiting_on_lock_cannot_resurrect_expired_lease(
    client, session_factory, engine, monkeypatch
):
    _, claim = claimed(client, session_factory)
    import app.services.execution_service as module

    now = module.utcnow()
    clock = {"value": now}
    monkeypatch.setattr(module, "utcnow", lambda: clock["value"])
    with session_factory() as session:
        session.get(TaskExecution, claim.execution_id).lease_expires_at = now + timedelta(seconds=1)
        session.commit()
    issued = threading.Event()
    result = []
    errors = []

    def before_sql(conn, cursor, statement, parameters, context, executemany):
        if (
            threading.current_thread().name == "recheck-heartbeat"
            and "FROM task_executions" in statement
            and "FOR UPDATE" in statement
        ):
            issued.set()

    def heartbeat():
        try:
            with session_factory() as session:
                result.append(
                    ExecutionService().heartbeat(
                        session,
                        execution_id=claim.execution_id,
                        fencing_token=claim.fencing_token,
                        lease_seconds=30,
                    )
                )
                session.commit()
        except Exception as exc:
            errors.append(repr(exc))

    event.listen(engine, "before_cursor_execute", before_sql)
    thread = threading.Thread(target=heartbeat, name="recheck-heartbeat")
    try:
        with session_factory() as lock_holder:
            lock_holder.scalars(
                select(TaskExecution)
                .where(TaskExecution.id == claim.execution_id)
                .with_for_update()
            ).one()
            thread.start()
            assert issued.wait(10)  # The heartbeat is blocked before its authoritative time check.
            clock["value"] = now + timedelta(seconds=2)
            lock_holder.commit()
        thread.join(10)
        assert not thread.is_alive() and not errors
        with session_factory() as session:
            expiry = session.get(TaskExecution, claim.execution_id).lease_expires_at
        assert result == [False], (
            f"Heartbeat accepted after expiry: result={result}, now={clock['value']}, new expiry={expiry}"
        )
    finally:
        thread.join(10)
        event.remove(engine, "before_cursor_execute", before_sql)


def ledger_snapshot(session):
    return (
        session.execute(text("SELECT row_to_json(t) FROM budget_ledger_entries t ORDER BY id"))
        .scalars()
        .all()
    )


@pytest.mark.parametrize("stop", ["cancel", "recover", "pause"])
def test_unused_claimed_retry_preserves_prior_usage(client, session_factory, stop):
    headers, project = bootstrap(client)
    task_id = create_task(
        client, headers, project["id"], demo_behavior="PERMANENT_FAILURE", max_auto_retries=0
    ).json()["task"]["id"]
    run_worker(session_factory).run_once()
    with session_factory() as session:
        old_ledger = ledger_snapshot(session)
        before = {a.scope: a.consumed_tokens for a in session.scalars(select(BudgetAccount))}
    assert (
        client.post(f"/api/v1/tasks/{task_id}/retry", json={"idempotencyKey": "regression-retry"}, headers=headers).status_code == 200
    )
    claim = _claim(session_factory)
    if stop == "pause":
        from app.services.execution_service import ProcessorOutcome

        assert client.post("/api/v1/company/pause", json={}, headers=headers).status_code == 200
        with session_factory() as session:
            ExecutionService().complete(
                session,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                outcome=ProcessorOutcome(
                    succeeded=False,
                    summary=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    usage_known=False,
                    stopped_reason="COMPANY_PAUSED",
                ),
            )
            session.commit()
    else:
        expire(session_factory, claim)
        if stop == "cancel":
            assert (
                client.post(f"/api/v1/tasks/{task_id}/cancel", json={}, headers=headers).status_code
                == 200
            )
        with session_factory() as session:
            ExecutionService().recover_expired_leases(session)
            session.commit()
    with session_factory() as session:
        accounts = list(session.scalars(select(BudgetAccount)))
        assert {a.scope: a.consumed_tokens for a in accounts} == before
        assert all(a.reserved_tokens == 0 for a in accounts)
        assert ledger_snapshot(session)[: len(old_ledger)] == old_ledger
        assert len(session.scalars(select(TaskExecution)).all()) == 2


@pytest.mark.parametrize("known,expected", [(False, 64), (True, 0)])
def test_cancelled_step_settles_once_at_all_scopes(client, session_factory, known, expected):
    from app.services.execution_service import ProcessorOutcome

    headers, claim = claimed(client, session_factory)
    service = ExecutionService()
    with session_factory() as session:
        step = service.start_step(
            session,
            execution_id=claim.execution_id,
            fencing_token=claim.fencing_token,
            sequence=0,
            name="cancelled call",
            estimated_tokens=64,
        )
        session.commit()
    assert (
        client.post(f"/api/v1/tasks/{claim.task_id}/cancel", json={}, headers=headers).status_code
        == 200
    )
    for _ in range(2):
        with session_factory() as session:
            service.finish_step(
                session,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                step_id=step.id,
                status=StepStatus.CANCELLED,
                prompt_tokens=0 if known else None,
                completion_tokens=0 if known else None,
                usage_known=known,
            )
            session.commit()
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
                usage_known=known,
                cancelled=True,
            ),
        )
        session.commit()
    with session_factory() as session:
        accounts = {a.scope: a for a in session.scalars(select(BudgetAccount))}
        assert accounts["TASK"].consumed_tokens == expected
        for scope in ("COMPANY", "PROJECT"):
            assert accounts[scope].consumed_tokens == (0 if known else claim.task_budget_tokens)
        assert all(a.reserved_tokens == 0 for a in accounts.values())
        reservation_id = session.scalar(
            select(BudgetReservation.id).where(BudgetReservation.step_id == step.id)
        )
        entries = [
            row
            for row in ledger_snapshot(session)
            if row["reservation_id"] == reservation_id and row["entry_kind"] == "SETTLE"
        ]
        assert len(entries) == 1


def test_heartbeat_refreshes_cached_fence(client, session_factory):
    _, claim = claimed(client, session_factory)
    with session_factory() as stale:
        cached = stale.get(TaskExecution, claim.execution_id)
        with session_factory() as other:
            other.get(TaskExecution, claim.execution_id).fencing_token += 1
            other.commit()
        assert not ExecutionService().heartbeat(
            stale, execution_id=claim.execution_id, fencing_token=claim.fencing_token
        )
        assert cached.fencing_token != claim.fencing_token


def test_migration_replays_both_cursor_domains_and_preserves_business_data(
    client, session_factory, database_url, monkeypatch
):
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"])
    run_worker(session_factory).run_once()
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.downgrade(config, "e5b7c9d1a2f4")
    with session_factory() as session:
        rows = list(session.scalars(select(DomainEvent).order_by(DomainEvent.id)))
        # A late commit can make published order differ from primary-key order;
        # a prior re-publication can also put the publication cursor above IDs.
        for i, row in enumerate(reversed(rows)):
            row.published_seq = 1000 + i
        rows[-1].published_seq = None
        session.commit()
        raw_cursor = max(r.id for r in rows)
        publication_cursor = max(r.published_seq or 0 for r in rows)
        expected_order = [
            r.id for r in sorted(rows, key=lambda r: (r.published_seq or 100000, r.id))
        ]
        events_before = [(r.id, r.event_type, r.payload, r.created_at) for r in rows]
        ledger_before = ledger_snapshot(session)
        business_before = (
            session.execute(text("SELECT row_to_json(t) FROM tasks t ORDER BY id")).scalars().all()
        )
    command.upgrade(config, "head")
    command.check(config)
    feed = EventFeedService()
    with session_factory() as session:
        for cursor in (raw_cursor, publication_cursor):
            replay = feed.read_after(session, cursor=cursor, limit=100)
            assert [r.id for r in replay] == expected_order
        rows = list(session.scalars(select(DomainEvent).order_by(DomainEvent.id)))
        assert [(r.id, r.event_type, r.payload, r.created_at) for r in rows] == events_before
        assert ledger_snapshot(session) == ledger_before

        assert (
            session.execute(text("SELECT row_to_json(t) FROM tasks t ORDER BY id")).scalars().all()
            == business_before
        )
        cursors = [r.published_seq for r in rows]
    command.downgrade(config, "e5b7c9d1a2f4")
    with session_factory() as session:
        assert (
            list(session.scalars(select(DomainEvent.published_seq).order_by(DomainEvent.id)))
            == cursors
        )
    command.upgrade(config, "head")
    with session_factory() as session:
        assert [
            r.id for r in feed.read_after(session, cursor=max(cursors), limit=100)
        ] == expected_order
        assert ledger_snapshot(session) == ledger_before


def test_recovery_does_not_block_an_uncommitted_claim_foreign_key(client, session_factory):
    headers, project = bootstrap(client)
    create_task(client, headers, project["id"])
    errors = []
    finished = threading.Event()

    def recover():
        try:
            with session_factory() as session:
                assert ExecutionService().recover_expired_leases(session) == []
                session.commit()
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            finished.set()

    with session_factory() as claimant:
        claim = ExecutionService().claim_next(claimant, worker_id="uncommitted-claim")
        assert claim is not None
        worker = threading.Thread(target=recover)
        worker.start()
        try:
            # The claim owns a company FK KEY SHARE lock. Recovery must still
            # acquire its NO KEY UPDATE lock and finish without a dependency.
            assert finished.wait(10), "Recovery waited on an unrelated claim's company FK"
            assert not errors
        finally:
            claimant.commit()
            worker.join(10)
        assert not worker.is_alive()


def test_cached_execution_cannot_ignore_committed_cancellation(client, session_factory):
    from app.domain.errors import ExecutionStoppedError

    headers, claim = claimed(client, session_factory)
    with session_factory() as stale:
        cached_task = stale.get(Task, claim.task_id)
        cached_execution = stale.get(TaskExecution, claim.execution_id)
        assert (
            client.post(
                f"/api/v1/tasks/{claim.task_id}/cancel", json={}, headers=headers
            ).status_code
            == 200
        )
        with pytest.raises(ExecutionStoppedError):
            ExecutionService().start_step(
                stale,
                execution_id=claim.execution_id,
                fencing_token=claim.fencing_token,
                sequence=0,
                name="must not start",
                estimated_tokens=64,
            )
        assert cached_task.cancel_requested_at is not None
        assert cached_execution.cancel_requested_at is not None
