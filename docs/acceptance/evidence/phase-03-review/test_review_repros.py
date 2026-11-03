"""Review diagnostics: PASS means the named defect was reproduced, NOT acceptance.

Run from backend with an isolated TEST_ADMIN_DATABASE_URL:
python -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-review/test_review_repros.py -q -s
No production code is patched. Each test uses the existing per-test cloned DB.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.v1.tasks import _fetch_events
from app.domain.errors import BudgetInsufficientError
from app.models.tasks import BudgetAccount, BudgetReservation, DomainEvent, Task, TaskExecution, TaskExecutionStep
from app.repositories.auth_repository import utcnow
from app.services.execution_service import ExecutionService
from tests.test_tasks import bootstrap, create_task, run_worker


def claimed(client, session_factory, *, project_budget=100_000, retries=0):
    headers, project = bootstrap(client, budget_tokens=project_budget)
    response = create_task(client, headers, project['id'], max_auto_retries=retries)
    assert response.status_code == 201, response.text
    with session_factory() as session:
        claim = ExecutionService().claim_next(session, worker_id='review')
        session.commit()
    return headers, claim


def expire(session_factory, claim):
    with session_factory() as session:
        session.get(TaskExecution, claim.execution_id).lease_expires_at = utcnow() - timedelta(seconds=10)
        session.commit()


@pytest.mark.parametrize('command', ['pause', 'cancel'])
def test_running_task_ignores_stop_gate(client, session_factory, command):
    headers, project = bootstrap(client)
    response = create_task(client, headers, project['id'])
    task_id = response.json()['task']['id']
    runtime = run_worker(session_factory)
    claim = runtime.claim_one()
    url = '/api/v1/company/pause' if command == 'pause' else f'/api/v1/tasks/{task_id}/cancel'
    assert client.post(url, json={}, headers=headers).status_code == 200
    runtime.process_claim(claim)
    with session_factory() as session:
        assert session.get(Task, task_id).status == 'SUCCEEDED'
        assert len(session.scalars(select(TaskExecutionStep)).all()) == 2
    print(f'{command}: already-claimed task starts BOTH steps and SUCCEEDS after stop command')


def test_expired_lease_can_write_and_renew(client, session_factory):
    _, claim = claimed(client, session_factory)
    expire(session_factory, claim)
    service = ExecutionService()
    with session_factory() as session:
        step = service.start_step(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
                                  sequence=0, name='after expiry', estimated_tokens=10)
        assert step.id
        assert service.heartbeat(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token)
        session.commit()
    print('expired lease: new step accepted and heartbeat resurrects expired lease')


def test_fence_check_does_not_protect_following_write(client, session_factory, monkeypatch):
    _, claim = claimed(client, session_factory)
    service = ExecutionService()
    original = service._assert_fence

    def recover_between_check_and_write(session, **kwargs):
        original(session, **kwargs)
        expire(session_factory, claim)
        with session_factory() as recovery:
            ExecutionService().recover_expired_leases(recovery, idempotent_processor=False)
            recovery.commit()

    monkeypatch.setattr(service, '_assert_fence', recover_between_check_and_write)
    with session_factory() as session:
        service.start_step(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
                           sequence=0, name='late stale step', estimated_tokens=10)
        session.commit()
    with session_factory() as session:
        assert session.get(TaskExecution, claim.execution_id).status == 'INTERRUPTED'
        assert session.scalars(select(TaskExecutionStep)).one().status == 'RUNNING'
    print('fence race: INTERRUPTED execution acquires new RUNNING step and budget hold')


def test_recovery_without_retry_budget_escapes_worker(client, session_factory):
    _, claim = claimed(client, session_factory, project_budget=1000, retries=1)
    expire(session_factory, claim)
    with pytest.raises(BudgetInsufficientError):
        run_worker(session_factory).recover()
    with session_factory() as session:
        assert session.get(TaskExecution, claim.execution_id).status == 'RUNNING'
        assert session.get(Task, claim.task_id).status == 'RUNNING'
    print('recovery: BudgetInsufficientError escapes runtime; interruption transaction rolls back')


def test_cancelled_recovery_releases_unknown_company_cost(client, session_factory):
    headers, claim = claimed(client, session_factory)
    with session_factory() as session:
        ExecutionService().start_step(session, execution_id=claim.execution_id, fencing_token=claim.fencing_token,
                                      sequence=0, name='in-flight', estimated_tokens=200)
        session.commit()
    assert client.post(f'/api/v1/tasks/{claim.task_id}/cancel', json={}, headers=headers).status_code == 200
    expire(session_factory, claim)
    run_worker(session_factory).recover()
    with session_factory() as session:
        accounts = {a.scope: a for a in session.scalars(select(BudgetAccount)).all()}
        assert accounts['TASK'].consumed_tokens == 200
        assert accounts['COMPANY'].consumed_tokens == accounts['PROJECT'].consumed_tokens == 0
        assert accounts['COMPANY'].reserved_tokens == accounts['PROJECT'].reserved_tokens == 0
    print('cancel+crash: unknown call charged 200 TASK, but COMPANY/PROJECT both release all cost to zero')


def test_failed_step_unknown_usage_becomes_known_execution(client, session_factory):
    headers, project = bootstrap(client)
    create_task(client, headers, project['id'], demo_behavior='PERMANENT_FAILURE')
    run_worker(session_factory).run_once()
    with session_factory() as session:
        execution = session.scalars(select(TaskExecution)).one()
        failed = session.scalars(select(TaskExecutionStep).where(TaskExecutionStep.status == 'FAILED')).one()
        accounts = {a.scope: a for a in session.scalars(select(BudgetAccount)).all()}
        assert not failed.usage_known
        assert execution.usage_known and not execution.needs_reconciliation
        assert accounts['TASK'].consumed_tokens == 264
        assert accounts['COMPANY'].consumed_tokens == accounts['PROJECT'].consumed_tokens == 200
    print('failed step: unknown 64-token call disappears from company/project settlement; execution says known 200')


def test_unknown_reservations_invisible_in_reconciliation_api(client, session_factory):
    headers, project = bootstrap(client)
    create_task(client, headers, project['id'], demo_behavior='UNKNOWN_USAGE')
    run_worker(session_factory).run_once()
    with session_factory() as session:
        flagged = session.scalars(select(BudgetReservation).where(BudgetReservation.needs_reconciliation.is_(True))).all()
        assert len(flagged) == 4
    overview = client.get('/api/v1/budgets').json()
    assert overview['pendingReconciliationCount'] == 0
    assert overview['company']['needsReconciliationCount'] == 0
    assert client.get('/api/v1/budgets/pending-reconciliation').json() == []
    print('reconciliation: 4 flagged reservations exist, API counts 0 and returns empty list')


def test_auto_retry_leaks_partial_company_hold(client, session_factory):
    headers, project = bootstrap(client, budget_tokens=1000)
    create_task(client, headers, project['id'], demo_behavior='TRANSIENT_FAILURE', max_auto_retries=1)
    run_worker(session_factory).run_once()
    with session_factory() as session:
        assert session.scalars(select(Task)).one().status == 'BLOCKED'
        holds = session.scalars(select(BudgetReservation).where(BudgetReservation.status == 'RESERVED')).all()
        assert len(holds) == 1 and holds[0].scope == 'COMPANY' and holds[0].amount_tokens == 1000
    print('auto retry: project rejects second hold but company retains orphan 1000-token reservation')


def test_cancelling_interrupted_task_never_completes(client, session_factory):
    headers, claim = claimed(client, session_factory)
    expire(session_factory, claim)
    run_worker(session_factory).recover()
    response = client.post(f'/api/v1/tasks/{claim.task_id}/cancel', json={}, headers=headers)
    assert response.status_code == 200
    with session_factory() as session:
        task = session.get(Task, claim.task_id)
        assert task.status == 'INTERRUPTED' and task.cancel_requested_at is not None
    retry = client.post(f'/api/v1/tasks/{claim.task_id}/retry', json={}, headers=headers)
    assert retry.status_code == 409
    print('interrupted cancel: accepted 200 but never CANCELLED; retry permanently rejected')


def test_concurrent_commits_skip_sse_event(client, session_factory):
    headers, project = bootstrap(client)
    for _ in range(2):
        assert create_task(client, headers, project['id']).status_code == 201
    service = ExecutionService()
    with session_factory() as first, session_factory() as second:
        low_claim = service.claim_next(first, worker_id='first')
        low_id = first.scalars(select(DomainEvent.id).where(DomainEvent.execution_id == low_claim.execution_id)).one()
        high_claim = service.claim_next(second, worker_id='second')
        high_id = second.scalars(select(DomainEvent.id).where(DomainEvent.execution_id == high_claim.execution_id)).one()
        assert high_id > low_id
        second.commit()
        delivered = _fetch_events(session_factory, low_id - 1, None)
        assert [e.id for e in delivered] == [high_id]
        first.commit()
    assert _fetch_events(session_factory, high_id, None) == []
    print(f'SSE: event {high_id} delivered first, committed-late event {low_id} permanently skipped by cursor')


def test_task_creation_ignores_configured_zero_budget(client, app, session_factory):
    # Actual application setting and read-service agree on zero; TaskService constructs its own default.
    app.state.settings.company_default_budget_tokens = 0
    app.state.budget_service._default_company_limit = 0
    headers, project = bootstrap(client)
    response = create_task(client, headers, project['id'])
    assert response.status_code == 201, response.text
    with session_factory() as session:
        company_account = session.scalars(select(BudgetAccount).where(BudgetAccount.scope == 'COMPANY')).one()
        assert company_account.limit_tokens == 1_000_000
    print('configured company limit 0: first task creates company account with 1000000 and is accepted')


def test_frontend_query_names_are_ignored(client, session_factory):
    headers, first = bootstrap(client)
    second = client.post('/api/v1/projects', headers=headers, json={
        'key': 'SECOND', 'name': 'Second', 'objective': 'Other project',
        'runMode': 'DEMO', 'budgetTokens': 100000,
    }).json()
    for project in (first, second):
        assert create_task(client, headers, project['id']).status_code == 201
    # These are the exact names used in frontend/src/api/endpoints.ts.
    response = client.get('/api/v1/tasks', params={'projectId': first['id']}).json()
    assert {t['projectId'] for t in response['tasks']} == {first['id'], second['id']}
    correct = client.get('/api/v1/tasks', params={'project_id': first['id']}).json()
    assert {t['projectId'] for t in correct['tasks']} == {first['id']}
    overview = client.get('/api/v1/budgets', params={'ledgerLimit': 1}).json()
    assert len(overview['ledger']) == 4
    assert len(client.get('/api/v1/budgets', params={'ledger_limit': 1}).json()['ledger']) == 1
    print('query contract: projectId ignored (both projects returned); ledgerLimit ignored (4 instead of 1)')
