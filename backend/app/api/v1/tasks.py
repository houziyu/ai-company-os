"""Projects, tasks, executions, budgets, pause and the event feed.

Design notes
------------
* **Commands, not state writes.** There is no endpoint that accepts a task
  ``status``. Cancelling and retrying are named commands; the service decides the
  resulting state and rejects an impossible transition with 409.
* **Writes are idempotent.** ``POST /tasks`` requires ``idempotencyKey``; a repeat
  with the same body returns the existing task with 200, a different body with the
  same key is refused with 409.
* **SSE is replayable.** ``GET /events/stream`` sends an ``id:`` per event and
  accepts ``Last-Event-ID`` (or ``?after=``) to replay from where the browser
  stopped. A cursor endpoint lets the client verify it missed nothing.
* **Numbers are never invented.** Unknown usage is ``null`` in responses and is
  counted in ``needsReconciliationCount`` rather than summed as zero.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.context import client_ip, get_request_id
from app.api.deps import AuthenticatedDep, DbSession, WriteGuard
from app.api.schemas_tasks import (
    BudgetAccountResponse,
    BudgetOverviewResponse,
    CompanyPauseRequest,
    CompanyStateResponse,
    EventEnvelope,
    EventPage,
    ExecutionLedgerPage,
    ExecutionStepResponse,
    ExecutionSummary,
    LedgerEntryResponse,
    PendingReconciliationEntry,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectSummary,
    TaskCancelRequest,
    TaskCreateRequest,
    TaskDetail,
    TaskEventResponse,
    TaskListResponse,
    TaskMutationResponse,
    TaskRetryRequest,
    TaskSummary,
    TaskTaskBudget,
)
from app.domain.enums import BudgetScope, TaskStatus
from app.domain.errors import (
    FieldError,
    ProjectNotFoundError,
    TaskNotFoundError,
    ValidationFailedError,
)
from app.models.organization import Agent, Company
from app.models.tasks import (
    BudgetAccount,
    BudgetLedgerEntry,
    DomainEvent,
    Project,
    Task,
    TaskEvent,
    TaskExecution,
    TaskExecutionStep,
)
from app.repositories.auth_repository import utcnow
from app.services.budget_service import BudgetService
from app.services.event_feed_service import EventFeedService, cursor_of
from app.services.task_service import (
    CompanyGovernanceService,
    ProjectInput,
    ProjectService,
    TaskInput,
    TaskService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])

# Server-sent events: how often to re-check for new rows, and how many to send per
# batch. A short interval keeps progress feeling live without a second queue.
STREAM_POLL_SECONDS = 1.0
STREAM_BATCH = 100

# Query parameters are read by the browser, which sends camelCase everywhere else
# on the wire. Both spellings are accepted so the frontend contract and the
# documented snake_case form cannot diverge silently (review finding R12).
ProjectIdQuery = Annotated[
    int | None,
    Query(validation_alias=AliasChoices("projectId", "project_id")),
]
LedgerLimitQuery = Annotated[
    int,
    Query(ge=1, le=200, validation_alias=AliasChoices("ledgerLimit", "ledger_limit")),
]


# ------------------------------------------------------------------- helpers
def _budget(request: Request) -> BudgetService:
    service: BudgetService | None = getattr(request.app.state, "budget_service", None)
    if service is None:  # pragma: no cover - the app factory always installs one
        settings = request.app.state.settings
        service = BudgetService(default_company_limit=settings.company_default_budget_tokens)
        request.app.state.budget_service = service
    return service


def _task_service(request: Request) -> TaskService:
    """Build the task service on the app's configured budget dependency.

    Constructing a bare ``TaskService()`` would give it a hard-coded default
    company allowance, so the first task of a company could create the company
    account with a limit that ignores ``COMPANY_DEFAULT_BUDGET_TOKENS`` (R7).
    """
    return TaskService(budget_service=_budget(request))


def _event_feed() -> EventFeedService:
    return EventFeedService()


def _account_payload(
    service: BudgetService, session: Session, account: BudgetAccount, *, label: str
) -> BudgetAccountResponse:
    view = service.account_view(account, session)
    return BudgetAccountResponse(
        scope=account.scope,  # type: ignore[arg-type]
        scope_id=account.scope_id,
        label=label,
        limit_tokens=view.limit_tokens,
        consumed_tokens=view.consumed_tokens,
        reserved_tokens=view.reserved_tokens,
        available_tokens=view.available_tokens,
        needs_reconciliation_count=view.needs_reconciliation_count,
    )


def _latest_execution(session: Session, task_id: int) -> TaskExecution | None:
    return session.scalars(
        select(TaskExecution)
        .where(TaskExecution.task_id == task_id)
        .order_by(TaskExecution.attempt.desc(), TaskExecution.id.desc())
        .limit(1)
    ).one_or_none()


def _task_budget(service: BudgetService, session: Session, task: Task) -> TaskTaskBudget | None:
    account = service.task_account(session, task.id)
    if account is None:
        return None
    view = service.account_view(account, session)
    return TaskTaskBudget(
        limit_tokens=view.limit_tokens,
        consumed_tokens=view.consumed_tokens,
        reserved_tokens=view.reserved_tokens,
        available_tokens=view.available_tokens,
        needs_reconciliation_count=view.needs_reconciliation_count,
    )


def _task_summary(
    session: Session,
    task: Task,
    *,
    service: BudgetService,
    project_name: str,
    assignee_name: str | None,
    latest: TaskExecution | None = None,
) -> TaskSummary:
    execution = latest if latest is not None else _latest_execution(session, task.id)
    return TaskSummary(
        id=task.id,
        project_id=task.project_id,
        project_name=project_name,
        company_id=task.company_id,
        title=task.title,
        status=task.status,  # type: ignore[arg-type]
        run_mode=task.run_mode,  # type: ignore[arg-type]
        demo_behavior=task.demo_behavior,  # type: ignore[arg-type]
        budget_tokens=task.budget_tokens,
        attempt_count=task.attempt_count,
        max_auto_retries=task.max_auto_retries,
        assignee_agent_id=task.assignee_agent_id,
        assignee_name=assignee_name,
        current_execution_id=execution.id if execution else None,
        current_execution_status=(
            execution.status if execution else None  # type: ignore[arg-type]
        ),
        last_error_code=task.last_error_code,
        last_error_message=task.last_error_message,
        cancel_requested_at=task.cancel_requested_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        budget=_task_budget(service, session, task),
        cancel_pending=bool(
            task.cancel_requested_at is not None
            and task.status
            in {
                TaskStatus.RUNNING.value,
                TaskStatus.QUEUED.value,
            }
        ),
    )


def _execution_payload(session: Session, execution: TaskExecution) -> ExecutionSummary:
    steps = session.scalars(
        select(TaskExecutionStep)
        .where(TaskExecutionStep.execution_id == execution.id)
        .order_by(TaskExecutionStep.sequence)
    ).all()
    return ExecutionSummary(
        id=execution.id,
        task_id=execution.task_id,
        attempt=execution.attempt,
        status=execution.status,  # type: ignore[arg-type]
        run_mode=execution.run_mode,  # type: ignore[arg-type]
        demo_behavior=execution.demo_behavior,  # type: ignore[arg-type]
        worker_id=execution.worker_id,
        fencing_token=execution.fencing_token,
        lease_expires_at=execution.lease_expires_at,
        heartbeat_at=execution.heartbeat_at,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        reserved_tokens=execution.reserved_tokens,
        settled_tokens=execution.settled_tokens,
        prompt_tokens=execution.prompt_tokens,
        completion_tokens=execution.completion_tokens,
        usage_known=execution.usage_known,
        needs_reconciliation=execution.needs_reconciliation,
        error_code=execution.error_code,
        error_message=execution.error_message,
        result_summary=execution.result_summary,
        interrupted_unknown_side_effects=execution.interrupted_unknown_side_effects,
        recovery_note=execution.recovery_note,
        retried_from_execution_id=execution.retried_from_execution_id,
        steps=[
            ExecutionStepResponse(
                id=step.id,
                sequence=step.sequence,
                name=step.name,
                status=step.status,  # type: ignore[arg-type]
                started_at=step.started_at,
                finished_at=step.finished_at,
                estimated_tokens=step.estimated_tokens,
                prompt_tokens=step.prompt_tokens,
                completion_tokens=step.completion_tokens,
                usage_known=step.usage_known,
                detail=step.detail,
            )
            for step in steps
        ],
    )


def _event_payload(event: TaskEvent) -> TaskEventResponse:
    return TaskEventResponse(
        id=event.id,
        event_type=event.event_type,
        status_from=event.status_from,
        status_to=event.status_to,
        actor_type=event.actor_type,
        message=event.message,
        detail=event.detail,
        created_at=event.created_at,
    )


def _project_summary(
    session: Session, project: Project, *, service: BudgetService
) -> ProjectSummary:
    counts: dict[str, int] = {}
    rows = session.execute(
        select(Task.status, func.count()).where(Task.project_id == project.id).group_by(Task.status)
    ).all()
    for task_status, count in rows:
        counts[str(task_status)] = int(count)
    account = session.scalars(
        select(BudgetAccount).where(
            BudgetAccount.scope == BudgetScope.PROJECT.value,
            BudgetAccount.scope_id == project.id,
        )
    ).one_or_none()
    budget = None
    if account is not None:
        view = service.account_view(account, session)
        budget = {
            "limit_tokens": view.limit_tokens,
            "consumed_tokens": view.consumed_tokens,
            "reserved_tokens": view.reserved_tokens,
            "available_tokens": view.available_tokens,
            "needs_reconciliation_count": view.needs_reconciliation_count,
        }
    return ProjectSummary(
        id=project.id,
        key=project.key,
        name=project.name,
        objective=project.objective,
        status=project.status,  # type: ignore[arg-type]
        run_mode=project.run_mode,  # type: ignore[arg-type]
        version=project.version,
        created_at=project.created_at,
        updated_at=project.updated_at,
        task_counts=counts,
        budget=budget,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ projects
@router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    request: Request, _auth: AuthenticatedDep, session: DbSession
) -> ProjectListResponse:
    service = _budget(request)
    projects = session.scalars(
        select(Project).order_by(Project.created_at.desc(), Project.id.desc())
    ).all()
    return ProjectListResponse(
        projects=[_project_summary(session, project, service=service) for project in projects]
    )


@router.post("/projects", response_model=ProjectSummary, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateRequest,
    request: Request,
    auth: WriteGuard,
    session: DbSession,
) -> ProjectSummary:
    """Create a project. ``REAL`` mode is refused, never downgraded."""
    service = ProjectService()
    project = service.create(
        session,
        owner_user_id=auth.user.id,
        payload=ProjectInput(
            key=payload.key,
            name=payload.name,
            objective=payload.objective,
            run_mode=payload.run_mode,
            budget_tokens=payload.budget_tokens,
        ),
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return _project_summary(session, project, service=_budget(request))


@router.get("/projects/{project_id}", response_model=ProjectSummary)
def get_project(
    project_id: int, request: Request, _auth: AuthenticatedDep, session: DbSession
) -> ProjectSummary:
    project = session.get(Project, project_id)
    if project is None:
        raise ProjectNotFoundError()
    return _project_summary(session, project, service=_budget(request))


# --------------------------------------------------------------------- tasks
@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    _auth: AuthenticatedDep,
    session: DbSession,
    project_id: ProjectIdQuery = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskListResponse:
    service = _budget(request)
    base = select(Task)
    count_stmt = select(func.count()).select_from(Task)
    if project_id is not None:
        base = base.where(Task.project_id == project_id)
        count_stmt = count_stmt.where(Task.project_id == project_id)
    if status_filter:
        base = base.where(Task.status == status_filter)
        count_stmt = count_stmt.where(Task.status == status_filter)

    total = int(session.scalar(count_stmt) or 0)
    tasks = session.scalars(
        base.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit).offset(offset)
    ).all()

    project_names = (
        {
            project.id: project.name
            for project in session.scalars(
                select(Project).where(Project.id.in_({t.project_id for t in tasks}))
            )
        }
        if tasks
        else {}
    )
    agent_ids = {t.assignee_agent_id for t in tasks if t.assignee_agent_id}
    agent_names: dict[int, str] = {}
    if agent_ids:
        for agent in session.scalars(select(Agent).where(Agent.id.in_(agent_ids))):
            agent_names[agent.id] = agent.display_name

    counts: dict[str, int] = {}
    if project_id is not None:
        rows = session.execute(
            select(Task.status, func.count())
            .where(Task.project_id == project_id)
            .group_by(Task.status)
        ).all()
        counts = {str(row[0]): int(row[1]) for row in rows}
    else:
        rows = session.execute(select(Task.status, func.count()).group_by(Task.status)).all()
        counts = {str(row[0]): int(row[1]) for row in rows}

    return TaskListResponse(
        tasks=[
            _task_summary(
                session,
                task,
                service=service,
                project_name=project_names.get(task.project_id, "—"),
                assignee_name=agent_names.get(task.assignee_agent_id or -1),
            )
            for task in tasks
        ],
        total=total,
        limit=limit,
        offset=offset,
        counts=counts,
    )


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    project_id: int,
    payload: TaskCreateRequest,
    request: Request,
    response: Response,
    auth: WriteGuard,
    session: DbSession,
) -> TaskMutationResponse:
    """Create a task. A repeated request returns the existing task with 200.

    The endpoint is declared 201; the repeated case sets 200 on the response so a
    client can distinguish "created now" from "already existed".
    """
    service = _task_service(request)
    task, created = service.create(
        session,
        project_id=project_id,
        owner_user_id=auth.user.id,
        payload=TaskInput(
            title=payload.title,
            objective=payload.objective,
            acceptance=payload.acceptance,
            budget_tokens=payload.budget_tokens,
            demo_behavior=payload.demo_behavior,
            max_auto_retries=payload.max_auto_retries,
            assignee_agent_id=payload.assignee_agent_id,
        ),
        idempotency_key=payload.idempotency_key,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    project = session.get(Project, task.project_id)
    assignee = session.get(Agent, task.assignee_agent_id) if task.assignee_agent_id else None
    return TaskMutationResponse(
        task=_task_summary(
            session,
            task,
            service=_budget(request),
            project_name=project.name if project else "—",
            assignee_name=assignee.display_name if assignee else None,
        ),
        created=created,
        message="任务已创建并进入队列。" if created else "该请求已创建过任务，返回既有记录。",
    )


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(
    task_id: int, request: Request, _auth: AuthenticatedDep, session: DbSession
) -> TaskDetail:
    service = _budget(request)
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError()
    project = session.get(Project, task.project_id)
    assignee = session.get(Agent, task.assignee_agent_id) if task.assignee_agent_id else None
    summary = _task_summary(
        session,
        task,
        service=service,
        project_name=project.name if project else "—",
        assignee_name=assignee.display_name if assignee else None,
    )
    executions = session.scalars(
        select(TaskExecution)
        .where(TaskExecution.task_id == task.id)
        .order_by(TaskExecution.attempt.desc(), TaskExecution.id.desc())
    ).all()
    events = session.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.id)
    ).all()
    return TaskDetail(
        **summary.model_dump(),
        objective=task.objective,
        acceptance=task.acceptance,
        result_summary=task.result_summary,
        executions=[_execution_payload(session, row) for row in executions],
        events=[_event_payload(event) for event in events],
        idempotency_key=task.idempotency_key,
    )


@router.post("/tasks/{task_id}/cancel", response_model=TaskMutationResponse)
def cancel_task(
    task_id: int,
    payload: TaskCancelRequest,
    request: Request,
    auth: WriteGuard,
    session: DbSession,
) -> TaskMutationResponse:
    """Request a cooperative cancel.

    A queued task is cancelled immediately. A running one records the request and
    is stopped by its worker at the next safe point — the response says which
    happened instead of claiming an in-flight call stopped.
    """
    service = _task_service(request)
    task = service.request_cancel(
        session,
        task_id=task_id,
        owner_user_id=auth.user.id,
        reason=payload.reason,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    project = session.get(Project, task.project_id)
    assignee = session.get(Agent, task.assignee_agent_id) if task.assignee_agent_id else None
    still_running = task.status == TaskStatus.RUNNING.value
    return TaskMutationResponse(
        task=_task_summary(
            session,
            task,
            service=_budget(request),
            project_name=project.name if project else "—",
            assignee_name=assignee.display_name if assignee else None,
        ),
        created=False,
        message=(
            "已请求取消；正在运行的步骤会在安全检查点停止。" if still_running else "任务已取消。"
        ),
    )


@router.post("/tasks/{task_id}/retry", response_model=TaskMutationResponse)
def retry_task(
    task_id: int,
    request: Request,
    auth: WriteGuard,
    session: DbSession,
    payload: TaskRetryRequest | None = None,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=1, max_length=80
    ),
) -> TaskMutationResponse:
    key = payload.idempotency_key if payload and payload.idempotency_key else idempotency_key
    if not key or (payload and payload.idempotency_key and idempotency_key and payload.idempotency_key != idempotency_key):
        raise ValidationFailedError([FieldError("idempotencyKey", "请提供一致的重试请求编号。")])
    service = _task_service(request)
    task = service.request_retry(
        session,
        task_id=task_id,
        owner_user_id=auth.user.id,
        idempotency_key=key,
        expected_attempt=payload.expected_attempt if payload else None,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    project = session.get(Project, task.project_id)
    assignee = session.get(Agent, task.assignee_agent_id) if task.assignee_agent_id else None
    return TaskMutationResponse(
        task=_task_summary(
            session,
            task,
            service=_budget(request),
            project_name=project.name if project else "—",
            assignee_name=assignee.display_name if assignee else None,
        ),
        created=False,
        message="重试请求已记录，返回任务当前状态；同一请求不会重复执行。",
    )


# ---------------------------------------------------------------- executions
@router.get("/executions/{execution_id}", response_model=ExecutionSummary)
def get_execution(
    execution_id: int, _auth: AuthenticatedDep, session: DbSession
) -> ExecutionSummary:
    execution = session.get(TaskExecution, execution_id)
    if execution is None:
        from app.domain.errors import ExecutionNotFoundError

        raise ExecutionNotFoundError()
    return _execution_payload(session, execution)


def _ledger_payload(session: Session, entry: BudgetLedgerEntry) -> LedgerEntryResponse:
    return LedgerEntryResponse(
        id=entry.id,
        created_at=entry.created_at,
        entry_kind=entry.entry_kind,  # type: ignore[arg-type]
        source=entry.source,
        scope=_scope_of(entry.account_id, session),  # type: ignore[arg-type]
        project_id=entry.project_id,
        task_id=entry.task_id,
        execution_id=entry.execution_id,
        reservation_id=entry.reservation_id,
        delta_reserved=entry.delta_reserved,
        delta_consumed=entry.delta_consumed,
        usage_tokens=entry.usage_tokens,
        usage_known=entry.usage_known,
        needs_reconciliation=entry.needs_reconciliation,
        detail=entry.detail,
    )


@router.get("/executions/{execution_id}/ledger", response_model=ExecutionLedgerPage)
def execution_ledger(
    execution_id: int,
    _auth: AuthenticatedDep,
    session: DbSession,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> ExecutionLedgerPage:
    from app.domain.errors import ExecutionNotFoundError

    # A short shared lock keeps totals and this page on the same execution
    # snapshot. Step writers already lock this execution before touching ledgers.
    execution = session.scalar(
        select(TaskExecution)
        .where(
            TaskExecution.id == execution_id,
            TaskExecution.company_id == 1,
        )
        .with_for_update(read=True)
    )
    if execution is None:
        raise ExecutionNotFoundError()
    scope = (BudgetLedgerEntry.execution_id == execution_id, BudgetLedgerEntry.company_id == 1)
    rows = list(
        session.scalars(
            select(BudgetLedgerEntry)
            .where(
                *scope,
                BudgetLedgerEntry.id > after,
            )
            .order_by(BudgetLedgerEntry.id)
            .limit(limit + 1)
        )
    )
    totals = session.execute(
        select(
            func.coalesce(
                func.sum(BudgetLedgerEntry.delta_reserved).filter(
                    BudgetLedgerEntry.entry_kind == "RESERVE"
                ),
                0,
            ),
            func.coalesce(func.sum(BudgetLedgerEntry.delta_consumed), 0),
            func.count().filter(BudgetLedgerEntry.needs_reconciliation.is_(True)),
        ).where(*scope)
    ).one()
    return ExecutionLedgerPage(
        entries=[_ledger_payload(session, entry) for entry in rows[:limit]],
        next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        total_reserved=int(totals[0]),
        total_consumed=int(totals[1]),
        needs_reconciliation_count=int(totals[2]),
    )


@router.get("/tasks/{task_id}/events", response_model=EventPage)
def task_events(
    task_id: int,
    request: Request,
    _auth: AuthenticatedDep,
    session: DbSession,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> EventPage:
    """Published events for one task, replayable by cursor (used after reconnecting).

    The cursor is assigned in commit order (see ``EventFeedService``), so a
    reconnect after a dropped stream cannot skip an event whose transaction
    committed late.
    """
    if session.get(Task, task_id) is None:
        raise TaskNotFoundError()
    feed = _event_feed()
    # Assign cursors to committed-but-unpublished rows before reading, so the
    # HTTP replay path and the stream agree on one order. The request session is
    # reused: publishing here is part of this read transaction.
    feed.publish_pending(session)
    rows = feed.read_after(session, cursor=after, task_id=task_id, limit=limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    latest = feed.latest_cursor(session, task_id=task_id)
    return EventPage(
        events=[
            EventEnvelope(
                id=cursor_of(row),
                event_type=row.event_type,
                created_at=row.created_at,
                project_id=row.project_id,
                task_id=row.task_id,
                execution_id=row.execution_id,
                payload=row.payload,
            )
            for row in rows
        ],
        latest_id=latest,
        has_more=has_more,
    )


# ------------------------------------------------------------------- budgets
@router.get("/budgets", response_model=BudgetOverviewResponse)
def budget_overview(
    request: Request,
    _auth: AuthenticatedDep,
    session: DbSession,
    ledger_limit: LedgerLimitQuery = 50,
) -> BudgetOverviewResponse:
    from app.domain.errors import CompanyNotFoundError

    service = _budget(request)
    company = session.get(Company, 1)
    if company is None:
        raise CompanyNotFoundError()
    company_account = service.ensure_company_account(session)
    projects = session.scalars(
        select(Project).order_by(Project.created_at.desc(), Project.id.desc())
    ).all()
    project_accounts = [service.ensure_project_account(session, project) for project in projects]
    ledger = service.ledger_for_company(session, limit=ledger_limit)
    pending = len(service.pending_reconciliation(session, limit=1_000_000))
    return BudgetOverviewResponse(
        company=_account_payload(service, session, company_account, label="公司总额度"),
        projects=[
            _account_payload(service, session, account, label=project.name)
            for account, project in zip(project_accounts, projects, strict=True)
        ],
        ledger=[
            LedgerEntryResponse(
                id=entry.id,
                created_at=entry.created_at,
                entry_kind=entry.entry_kind,  # type: ignore[arg-type]
                source=entry.source,  # type: ignore[arg-type]
                scope=_scope_of(entry.account_id, session),
                project_id=entry.project_id,
                task_id=entry.task_id,
                execution_id=entry.execution_id,
                reservation_id=entry.reservation_id,
                delta_reserved=entry.delta_reserved,
                delta_consumed=entry.delta_consumed,
                usage_tokens=entry.usage_tokens,
                usage_known=entry.usage_known,
                needs_reconciliation=entry.needs_reconciliation,
                detail=entry.detail,
            )
            for entry in ledger
        ],
        pending_reconciliation_count=pending,
        period_start=utcnow() - timedelta(days=30),
        generated_at=utcnow(),
    )


def _scope_of(account_id: int, session: Session) -> str:
    account = session.get(BudgetAccount, account_id)
    return account.scope if account else "COMPANY"


@router.get(
    "/budgets/pending-reconciliation",
    response_model=list[PendingReconciliationEntry],
)
def pending_reconciliation(
    request: Request, _auth: AuthenticatedDep, session: DbSession
) -> list[PendingReconciliationEntry]:
    """Reservations whose real usage was never reported.

    Listed separately on purpose: these tokens are already treated as consumed,
    and the page must say so rather than show them as a clean zero. A settled
    conservative charge and a still-open call are both included — the flag, not
    the status, is what makes an entry need reconciliation (R10).
    """
    _budget(request)
    reservations = _budget(request).pending_reconciliation(session)
    return [
        PendingReconciliationEntry(
            reservation_id=row.id,
            account_scope=row.scope,  # type: ignore[arg-type]
            amount_tokens=row.amount_tokens,
            settled_tokens=row.settled_tokens,
            kind=row.kind,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            created_at=row.created_at,
            task_id=row.task_id,
            execution_id=row.execution_id,
            detail=row.detail,
        )
        for row in reservations
    ]


# ---------------------------------------------------------------- governance
@router.get("/company/state", response_model=CompanyStateResponse)
def company_state(_auth: AuthenticatedDep, session: DbSession) -> CompanyStateResponse:
    from app.domain.errors import CompanyNotFoundError

    company = session.get(Company, 1)
    if company is None:
        raise CompanyNotFoundError()
    running = int(
        session.scalar(
            select(func.count()).select_from(TaskExecution).where(TaskExecution.status == "RUNNING")
        )
        or 0
    )
    queued = int(
        session.scalar(
            select(func.count()).select_from(Task).where(Task.status == TaskStatus.QUEUED.value)
        )
        or 0
    )
    paused = company.status == "PAUSED"
    return CompanyStateResponse(
        status=company.status,  # type: ignore[arg-type]
        paused_at=company.paused_at,
        pause_reason=company.pause_reason,
        running_executions=running,
        queued_tasks=queued,
        message=(
            "公司已暂停：不会再发起新的模型调用或创建新任务；正在进行的调用按检查点停止或收尾。"
            if paused
            else "公司运行中。"
        ),
    )


@router.post("/company/pause", response_model=CompanyStateResponse)
def pause_company(
    payload: CompanyPauseRequest,
    request: Request,
    auth: WriteGuard,
    session: DbSession,
) -> CompanyStateResponse:
    service = CompanyGovernanceService()
    service.pause(
        session,
        owner_user_id=auth.user.id,
        reason=payload.reason,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return company_state(auth, session)


@router.post("/company/resume", response_model=CompanyStateResponse)
def resume_company(request: Request, auth: WriteGuard, session: DbSession) -> CompanyStateResponse:
    service = CompanyGovernanceService()
    service.resume(
        session,
        owner_user_id=auth.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return company_state(auth, session)


# ----------------------------------------------------------------------- sse
def _sse_message(event: DomainEvent) -> str:
    """One SSE frame. The ``id:`` is the publication cursor used for resume."""
    cursor = cursor_of(event)
    payload = json.dumps(
        {
            "id": cursor,
            "type": event.event_type,
            "createdAt": event.created_at.isoformat(),
            "projectId": event.project_id,
            "taskId": event.task_id,
            "executionId": event.execution_id,
            "payload": event.payload or {},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return f"id: {cursor}\nevent: {event.event_type}\ndata: {payload}\n\n"


def _fetch_events(
    factory: sessionmaker[Session],
    feed: EventFeedService,
    cursor: int,
    project_id: int | None,
) -> list[DomainEvent]:
    """Publish any committed-but-unpublished rows, then read one ordered batch.

    Detached from the request transaction on purpose: the stream must not hold a
    snapshot open between polls, and this runs in a worker thread so the async
    event loop is never blocked by synchronous database I/O.
    """
    feed.publish(factory)
    with factory() as session:
        return feed.read_after(session, cursor=cursor, project_id=project_id, limit=STREAM_BATCH)


@router.get("/events/stream")
def stream_events(
    request: Request,
    _auth: AuthenticatedDep,
    after: int = Query(default=0, ge=0),
    project_id: ProjectIdQuery = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Server-sent events with replayable cursors.

    A browser that reconnects sends ``Last-Event-ID`` automatically; ``?after=``
    is accepted for clients that cannot set the header. Both replay strictly after
    the given cursor, so a reconnect cannot silently skip progress. The cursor is
    a commit-ordered publication sequence, not the row id, because a row id is
    allocated before its transaction commits (R8).

    The stream is read-only and does not hold a database transaction between
    polls: each poll opens a short session, which keeps an idle tab from pinning a
    connection or a snapshot.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    feed: EventFeedService = (
        getattr(request.app.state, "event_feed_service", None) or EventFeedService()
    )
    resume_from = after
    if last_event_id:
        try:
            resume_from = max(resume_from, int(last_event_id.strip()))
        except ValueError:
            resume_from = after

    async def _generate() -> AsyncIterator[str]:
        cursor = resume_from
        while True:
            if await request.is_disconnected():
                break
            rows = await asyncio.to_thread(_fetch_events, factory, feed, cursor, project_id)
            for row in rows:
                yield _sse_message(row)
                cursor = cursor_of(row)
            if not rows:
                # Comment frames keep intermediaries from timing the stream out
                # without inventing an event id that would break replay.
                yield ": keep-alive\n\n"
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
