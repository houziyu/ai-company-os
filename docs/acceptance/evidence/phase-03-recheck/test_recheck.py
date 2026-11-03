"""Independent correct-behaviour checks. Failing assertions are open defects.

Run from backend using an isolated TEST_ADMIN_DATABASE_URL:
python -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-recheck/test_recheck.py -q
"""
import threading
from datetime import timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import event, select

from app.domain.enums import StepStatus
from app.main import BACKEND_ROOT
from app.models.tasks import BudgetAccount, BudgetReservation, DomainEvent, TaskExecution
from app.services.event_feed_service import EventFeedService
from app.services.execution_service import ExecutionService
from tests.test_phase03_review_fixes import claimed
from tests.test_tasks import bootstrap, create_task, run_worker


def test_cancel_queued_retry_does_not_charge_previous_attempt_again(client, session_factory):
    headers, project = bootstrap(client)
    response = create_task(client, headers, project['id'], demo_behavior='PERMANENT_FAILURE', max_auto_retries=0)
    task_id = response.json()['task']['id']
    run_worker(session_factory).run_once()
    with session_factory() as session:
        before = session.scalar(select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == 'COMPANY'))
    assert before == 1000
    assert client.post(f'/api/v1/tasks/{task_id}/retry', json={}, headers=headers).status_code == 200
    assert client.post(f'/api/v1/tasks/{task_id}/cancel', json={}, headers=headers).status_code == 200
    with session_factory() as session:
        after = session.scalar(select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == 'COMPANY'))
    assert after == before, f'Queued retry made no call, but consumed increased from {before} to {after}'


def test_cancelled_unknown_step_consumes_its_task_hold(client, session_factory):
    headers, claim = claimed(client, session_factory)
    service = ExecutionService()
    with session_factory() as session:
        step = service.start_step(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
                                  sequence=0, name='unknown in-flight call', estimated_tokens=64)
        session.commit()
    assert client.post(f'/api/v1/tasks/{claim.task_id}/cancel', json={}, headers=headers).status_code == 200
    with session_factory() as session:
        service.finish_step(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
                            step_id=step.id, status=StepStatus.CANCELLED,
                            prompt_tokens=None, completion_tokens=None, usage_known=False)
        session.commit()
    with session_factory() as session:
        hold = session.scalars(select(BudgetReservation).where(BudgetReservation.step_id == step.id)).one()
        assert hold.status == 'SETTLED', f'Unknown call hold was {hold.status}, amount={hold.amount_tokens}'
        assert hold.needs_reconciliation
        assert session.scalar(select(BudgetAccount.consumed_tokens).where(BudgetAccount.scope == 'TASK')) == 64


def test_migration_keeps_old_cursor_when_event_ids_have_gaps(client, session_factory, database_url, monkeypatch):
    bootstrap(client)
    with session_factory() as session:
        session.add(DomainEvent(company_id=1, event_type='ROLLED_BACK', payload={}))
        session.flush()
        session.rollback()  # PostgreSQL sequences do not roll back: make a real gap.
    with session_factory() as session:
        row = DomainEvent(company_id=1, event_type='OLD_DELIVERED', payload={})
        session.add(row)
        session.commit()
        old_cursor = row.id
    monkeypatch.setenv('DATABASE_URL', database_url)
    config = Config(str(BACKEND_ROOT / 'alembic.ini'))
    command.downgrade(config, 'c41f7b93e2a5')
    command.upgrade(config, 'head')
    with session_factory() as session:
        row = DomainEvent(company_id=1, event_type='AFTER_UPGRADE', payload={})
        session.add(row)
        session.commit()
        new_id = row.id
    feed = EventFeedService()
    feed.publish(session_factory)
    with session_factory() as session:
        new_seq = session.scalar(select(DomainEvent.published_seq).where(DomainEvent.id == new_id))
        replay = feed.read_after(session, cursor=old_cursor, limit=100)
    assert new_id in [e.id for e in replay], f'old cursor={old_cursor}, new id={new_id}, new seq={new_seq}; event skipped'


def test_step_start_and_recovery_do_not_deadlock(client, session_factory, engine, monkeypatch):
    _, claim = claimed(client, session_factory, retries=1)
    import app.services.execution_service as module
    now = module.utcnow()
    clock = {'value': now}
    monkeypatch.setattr(module, 'utcnow', lambda: clock['value'])
    service = ExecutionService()
    reached_company_reserve = threading.Event()
    recovery_has_company = threading.Event()
    original = service._assert_startable
    errors = []

    def gate(session, **kwargs):
        original(session, **kwargs)
        reached_company_reserve.set()  # start_step already owns task and execution.
        assert recovery_has_company.wait(10)

    monkeypatch.setattr(service, '_assert_startable', gate)

    def after_sql(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == 'recheck-recovery' and 'FROM companies' in statement and 'FOR UPDATE' in statement:
            recovery_has_company.set()

    event.listen(engine, 'after_cursor_execute', after_sql)

    def complete():
        try:
            with session_factory() as session:
                service.start_step(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
                    sequence=0, name='concurrent step', estimated_tokens=10)
                session.commit()
        except Exception as exc:
            errors.append(('complete', type(exc).__name__, getattr(getattr(exc, 'orig', None), 'sqlstate', None)))

    def recover():
        try:
            with session_factory() as session:
                ExecutionService().recover_expired_leases(session)
                session.commit()
        except Exception as exc:
            errors.append(('recover', type(exc).__name__, getattr(getattr(exc, 'orig', None), 'sqlstate', None)))

    first = threading.Thread(target=complete, name='recheck-complete')
    second = threading.Thread(target=recover, name='recheck-recovery')
    try:
        first.start()
        assert reached_company_reserve.wait(10)
        clock['value'] = now + timedelta(minutes=5)
        second.start()
        first.join(20)
        second.join(20)
        assert not first.is_alive() and not second.is_alive()
        assert errors == [], f'Concurrent step/recovery errors: {errors}'
    finally:
        recovery_has_company.set()
        first.join(20)
        if second.ident is not None:
            second.join(20)
        event.remove(engine, 'after_cursor_execute', after_sql)


def test_heartbeat_waiting_on_lock_cannot_resurrect_expired_lease(client, session_factory, engine, monkeypatch):
    _, claim = claimed(client, session_factory)
    import app.services.execution_service as module
    now = module.utcnow()
    clock = {'value': now}
    monkeypatch.setattr(module, 'utcnow', lambda: clock['value'])
    with session_factory() as session:
        session.get(TaskExecution, claim.execution_id).lease_expires_at = now + timedelta(seconds=1)
        session.commit()
    issued = threading.Event()
    result = []
    errors = []

    def before_sql(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == 'recheck-heartbeat' and statement.startswith('UPDATE task_executions'):
            issued.set()

    def heartbeat():
        try:
            with session_factory() as session:
                result.append(ExecutionService().heartbeat(session, execution_id=claim.execution_id,
                    fencing_token=claim.fencing_token, lease_seconds=30))
                session.commit()
        except Exception as exc:
            errors.append(repr(exc))

    event.listen(engine, 'before_cursor_execute', before_sql)
    thread = threading.Thread(target=heartbeat, name='recheck-heartbeat')
    try:
        with session_factory() as lock_holder:
            lock_holder.scalars(select(TaskExecution).where(TaskExecution.id == claim.execution_id).with_for_update()).one()
            thread.start()
            assert issued.wait(10)  # SQL carries the earlier timestamp and cannot obtain the row lock yet.
            clock['value'] = now + timedelta(seconds=2)
            lock_holder.commit()
        thread.join(10)
        assert not thread.is_alive() and not errors
        with session_factory() as session:
            expiry = session.get(TaskExecution, claim.execution_id).lease_expires_at
        assert result == [False], f'Heartbeat accepted after expiry: result={result}, now={clock["value"]}, new expiry={expiry}'
    finally:
        thread.join(10)
        event.remove(engine, 'before_cursor_execute', before_sql)
