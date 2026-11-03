"""Project and task lifecycle: creation, idempotency, cancellation, retry.

State machine
-------------
Transitions are defined once in :data:`ALLOWED_TRANSITIONS`; nothing else may
assign ``Task.status``. The API never accepts a status field, only named
commands (cancel / retry / pause company), so a client cannot jump a task to
``SUCCEEDED``.

Creation is one transaction and one set of writes:
  task row + company planning hold + project planning hold + task account +
  timeline event + domain event (for SSE).

Idempotency
-----------
``(company_id, idempotency_key)`` is unique. The request body's digest is stored
next to it: a repeat with the *same* digest returns the existing task (200), a
repeat with a *different* digest is refused (409 IDEMPOTENCY_CONFLICT). A retried
POST therefore cannot create a second task or a second reservation.

Cancellation
------------
Cancellation is cooperative and explicit. The request sets
``cancel_requested_at``; a queued task is cancelled immediately, a running one is
stopped by its worker at the next safe point and then recorded as CANCELLED. The
browser closing a stream is never treated as a cancel.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import (
    AuditAction,
    AuditActorType,
    CompanyStatus,
    DomainEventType,
    EventActorType,
    ProjectStatus,
    ReservationKind,
    ReservationStatus,
    RunMode,
    TaskStatus,
)
from app.domain.errors import (
    CompanyNotFoundError,
    CompanyPausedError,
    ConcurrentUpdateError,
    FieldError,
    IdempotencyConflictError,
    InvalidTaskTransitionError,
    ProjectNotFoundError,
    RunModeUnavailableError,
    TaskNotFoundError,
    ValidationFailedError,
)
from app.models.organization import Company
from app.models.tasks import (
    BudgetReservation,
    DomainEvent,
    Project,
    Task,
    TaskEvent,
    TaskExecution,
    TaskExecutionStep,
    TaskRetryCommand,
    content_fingerprint,
)
from app.repositories.auth_repository import AuditRepository, utcnow
from app.services.budget_service import BudgetService

logger = logging.getLogger(__name__)

MAX_PROJECT_NAME = 120
MAX_PROJECT_OBJECTIVE = 4000
MAX_TASK_TITLE = 160
MAX_TASK_OBJECTIVE = 8000
MAX_ACCEPTANCE = 4000
MAX_IDEMPOTENCY_KEY = 80
MAX_REASON = 200

# The planning envelope a task holds at company and project scope before it
# starts. It exists so a queued task cannot be starved by other tasks claiming the
# same project allowance, and it is released when the task becomes terminal.
PLANNING_OVERHEAD_TOKENS = 0

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.BLOCKED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
            TaskStatus.BLOCKED,
            TaskStatus.QUEUED,  # automatic retry re-queues with a new execution
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.INTERRUPTED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.FAILED: frozenset({TaskStatus.QUEUED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})


def assert_transition(current: str, target: TaskStatus, action: str) -> None:
    """Raise 409 unless the state machine allows this move."""
    current_enum = TaskStatus(current)
    if target not in ALLOWED_TRANSITIONS[current_enum]:
        raise InvalidTaskTransitionError(current, action)


@dataclass(slots=True)
class ProjectInput:
    key: str
    name: str
    objective: str
    run_mode: str
    budget_tokens: int


@dataclass(slots=True)
class TaskInput:
    title: str
    objective: str
    acceptance: str | None
    budget_tokens: int
    demo_behavior: str
    max_auto_retries: int
    assignee_agent_id: int | None


class ProjectService:
    def __init__(self, *, budget_service: BudgetService | None = None) -> None:
        self._budget = budget_service or BudgetService()
        self._audit = AuditRepository()

    def _company(self, session: Session) -> Company:
        company = session.get(Company, 1)
        if company is None:
            raise CompanyNotFoundError()
        return company

    def create(
        self,
        session: Session,
        *,
        owner_user_id: int,
        payload: ProjectInput,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Project:
        company = self._company(session)
        if company.status == CompanyStatus.PAUSED.value:
            raise CompanyPausedError()
        if payload.run_mode == RunMode.REAL.value:
            # Declared but not implemented in Phase 03. Refusing is the only
            # honest answer; silently creating a DEMO project would misrepresent
            # what will happen when a task runs.
            raise RunModeUnavailableError(RunMode.REAL.value)

        field_errors: list[FieldError] = []
        key = payload.key.strip().upper()
        name = payload.name.strip()
        objective = payload.objective.strip()
        if not key:
            field_errors.append(FieldError("key", "请输入项目编号。"))
        elif len(key) > 64:
            field_errors.append(FieldError("key", "项目编号不能超过 64 个字符。"))
        elif not key.replace("-", "").replace("_", "").isalnum():
            field_errors.append(FieldError("key", "项目编号只能包含字母、数字、连字符和下划线。"))
        if not name:
            field_errors.append(FieldError("name", "请输入项目名称。"))
        elif len(name) > MAX_PROJECT_NAME:
            field_errors.append(FieldError("name", f"项目名称不能超过 {MAX_PROJECT_NAME} 个字符。"))
        if not objective:
            field_errors.append(FieldError("objective", "请填写项目目标。"))
        elif len(objective) > MAX_PROJECT_OBJECTIVE:
            field_errors.append(
                FieldError("objective", f"项目目标不能超过 {MAX_PROJECT_OBJECTIVE} 个字符。")
            )
        if payload.budget_tokens < 0:
            field_errors.append(FieldError("budgetTokens", "项目额度不能为负数。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        existing = session.scalars(
            select(Project).where(Project.company_id == company.id, Project.key == key)
        ).one_or_none()
        if existing is not None:
            raise ValidationFailedError([FieldError("key", "该项目编号已存在。")])

        project = Project(
            company_id=company.id,
            key=key,
            name=name,
            objective=objective,
            status=ProjectStatus.ACTIVE.value,
            run_mode=payload.run_mode,
            budget_tokens=payload.budget_tokens,
        )
        session.add(project)
        session.flush()
        # The project account is created with the project so a task's project
        # hold always has an account to lock.
        self._budget.ensure_project_account(session, project)

        self._audit.add(
            session,
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            action=AuditAction.PROJECT_CREATED.value,
            outcome="SUCCESS",
            target_type="PROJECT",
            target_id=str(project.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"key={key}",
        )
        self._emit(
            session,
            event_type=DomainEventType.PROJECT_CREATED,
            project_id=project.id,
            payload={"projectId": project.id, "key": key, "name": name},
        )
        return project

    def _emit(
        self,
        session: Session,
        *,
        event_type: DomainEventType,
        project_id: int | None,
        task_id: int | None = None,
        execution_id: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_type=event_type.value,
            company_id=1,
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            payload=payload or {},
        )
        session.add(event)
        session.flush()
        return event


class TaskService:
    def __init__(self, *, budget_service: BudgetService | None = None) -> None:
        """Use the app's shared budget service so the configured company default
        allowance is honoured on the very first task (review finding R7)."""
        self._budget = budget_service or BudgetService()
        self._audit = AuditRepository()

    # ------------------------------------------------------------------ reads
    def get(self, session: Session, task_id: int) -> Task:
        task = session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError()
        return task

    def get_project(self, session: Session, project_id: int) -> Project:
        project = session.get(Project, project_id)
        if project is None:
            raise ProjectNotFoundError()
        return project

    def list_executions(self, session: Session, task_id: int) -> Sequence[TaskExecution]:
        return session.scalars(
            select(TaskExecution)
            .where(TaskExecution.task_id == task_id)
            .order_by(TaskExecution.attempt.desc())
        ).all()

    def timeline(self, session: Session, task_id: int) -> Sequence[TaskEvent]:
        return session.scalars(
            select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id)
        ).all()

    # --------------------------------------------------------------- creation
    def create(
        self,
        session: Session,
        *,
        project_id: int,
        owner_user_id: int,
        payload: TaskInput,
        idempotency_key: str,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Task, bool]:
        """Create a task, its allowances and its events in one transaction.

        Returns ``(task, created)``. ``created=False`` means this was a repeat of
        an identical request; the caller answers 200 rather than 201.
        """
        company = session.get(Company, 1)
        if company is None:
            raise CompanyNotFoundError()
        project = self.get_project(session, project_id)
        if project.status != ProjectStatus.ACTIVE.value:
            raise InvalidTaskTransitionError(project.status, "create-task-in-archived-project")

        title = payload.title.strip()
        objective = payload.objective.strip()
        acceptance = payload.acceptance.strip() if payload.acceptance else None
        field_errors: list[FieldError] = []
        if not title:
            field_errors.append(FieldError("title", "请输入任务标题。"))
        elif len(title) > MAX_TASK_TITLE:
            field_errors.append(FieldError("title", f"任务标题不能超过 {MAX_TASK_TITLE} 个字符。"))
        if not objective:
            field_errors.append(FieldError("objective", "请填写任务目标。"))
        elif len(objective) > MAX_TASK_OBJECTIVE:
            field_errors.append(
                FieldError("objective", f"任务目标不能超过 {MAX_TASK_OBJECTIVE} 个字符。")
            )
        if acceptance is not None and len(acceptance) > MAX_ACCEPTANCE:
            field_errors.append(
                FieldError("acceptance", f"验收说明不能超过 {MAX_ACCEPTANCE} 个字符。")
            )
        if payload.budget_tokens <= 0:
            field_errors.append(FieldError("budgetTokens", "任务额度必须大于 0。"))
        if len(idempotency_key) > MAX_IDEMPOTENCY_KEY:
            field_errors.append(FieldError("idempotencyKey", "请求编号过长。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        fingerprint = content_fingerprint(
            {
                "projectId": project_id,
                "title": title,
                "objective": objective,
                "acceptance": acceptance,
                "budgetTokens": payload.budget_tokens,
                "demoBehavior": payload.demo_behavior,
                "maxAutoRetries": payload.max_auto_retries,
                "assigneeAgentId": payload.assignee_agent_id,
            }
        )

        existing = session.scalars(
            select(Task).where(Task.company_id == 1, Task.idempotency_key == idempotency_key)
        ).one_or_none()
        if existing is not None:
            if existing.idempotency_fingerprint == fingerprint:
                return existing, False
            raise IdempotencyConflictError("task")

        if company.status == CompanyStatus.PAUSED.value:
            raise CompanyPausedError()

        run_mode = project.run_mode
        if run_mode == RunMode.REAL.value:
            raise RunModeUnavailableError(RunMode.REAL.value)

        # ---- lock order: company -> project -> task (new row) ----------------
        # The company row lock serialises concurrent creation, so two tasks
        # competing for the same project allowance cannot both pass the check.
        company = self._budget.lock_company(session)
        if company.status == CompanyStatus.PAUSED.value:
            raise CompanyPausedError()
        session.scalars(select(Project).where(Project.id == project_id).with_for_update()).one()

        task = Task(
            company_id=1,
            project_id=project_id,
            assignee_agent_id=payload.assignee_agent_id,
            title=title,
            objective=objective,
            acceptance=acceptance,
            status=TaskStatus.QUEUED.value,
            demo_behavior=payload.demo_behavior,
            run_mode=run_mode,
            budget_tokens=payload.budget_tokens,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=fingerprint,
            max_auto_retries=payload.max_auto_retries,
            queued_at=utcnow(),
            version=1,
        )
        session.add(task)
        try:
            session.flush()
        except IntegrityError:
            # Lost a race with an identical concurrent request: read the winner.
            session.rollback()
            winner = session.scalars(
                select(Task).where(Task.company_id == 1, Task.idempotency_key == idempotency_key)
            ).one_or_none()
            if winner is not None and winner.idempotency_fingerprint == fingerprint:
                return winner, False
            raise IdempotencyConflictError("task") from None

        self._budget.ensure_task_account(session, task)

        # Planning envelope: company then project. Both holds are part of this
        # transaction, so a failure leaves no task and no hold behind.
        reserve_amount = payload.budget_tokens + PLANNING_OVERHEAD_TOKENS
        # Order: company then project (module-wide lock order). Both holds are
        # one unit — if the project refuses, the company hold rolls back with it
        # even though the caller keeps its outer transaction.
        with session.begin_nested():
            self._budget.reserve_company_allowance(
                session,
                amount=reserve_amount,
                kind=ReservationKind.PLANNING,
                owner_ref=task.id,
                project_id=project_id,
                task_id=task.id,
                execution_id=None,
                detail=f"task-planning:{task.id}",
            )
            self._budget.reserve_project_allowance(
                session,
                project=project,
                amount=reserve_amount,
                kind=ReservationKind.PLANNING,
                owner_ref=task.id,
                task_id=task.id,
                execution_id=None,
                detail=f"task-planning:{task.id}",
            )

        self._timeline(
            session,
            task=task,
            event_type=DomainEventType.TASK_CREATED.value,
            message="任务已进入队列。",
            status_to=TaskStatus.QUEUED.value,
            actor_type=EventActorType.OWNER,
        )
        self._emit(
            session,
            event_type=DomainEventType.TASK_CREATED,
            project_id=project_id,
            task_id=task.id,
            payload={
                "taskId": task.id,
                "projectId": project_id,
                "title": title,
                "status": task.status,
                "budgetTokens": task.budget_tokens,
                "demoBehavior": task.demo_behavior,
            },
        )
        self._audit.add(
            session,
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            action=AuditAction.TASK_CREATED.value,
            outcome="SUCCESS",
            target_type="TASK",
            target_id=str(task.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"project={project_id} budget={task.budget_tokens}",
        )
        return task, True

    # ------------------------------------------------------------ transitions
    def request_cancel(
        self,
        session: Session,
        *,
        task_id: int,
        owner_user_id: int,
        reason: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Task:
        """Record a cooperative cancel. Never pretends an in-flight call stopped.

        The decision is made from the *state machine*, not from "is it QUEUED or
        not" (review finding R11):

        * ``QUEUED``               -> cancelled now; nothing could have been called.
        * ``RUNNING`` with an execution still alive -> cooperative: marker only.
        * any other non-terminal state (``INTERRUPTED``, ``BLOCKED``,
          ``WAITING_APPROVAL``) -> no worker owns it, so it is cancelled now and
          its remaining envelope is resolved from recorded call evidence.
        """
        self._budget.lock_company(session)
        task = session.scalars(
            select(Task)
            .where(Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if task is None:
            raise TaskNotFoundError()
        if TaskStatus(task.status) in TERMINAL_STATUSES:
            raise InvalidTaskTransitionError(task.status, "cancel")
        if task.cancel_requested_at is not None:
            return task  # already requested; idempotent

        now = utcnow()
        task.cancel_requested_at = now
        task.cancel_requested_by_user_id = owner_user_id
        task.version += 1

        # An execution can be *active* (RUNNING with a live lease) only when a
        # worker may still complete it cooperatively.
        active_execution = session.scalars(
            select(TaskExecution)
            .where(
                TaskExecution.task_id == task.id,
                TaskExecution.status == "RUNNING",
                TaskExecution.lease_expires_at.is_not(None),
                TaskExecution.lease_expires_at > now,
            )
            .with_for_update()
        ).one_or_none()
        owns_cooperatively = (
            TaskStatus(task.status) is TaskStatus.RUNNING and active_execution is not None
        )

        if owns_cooperatively and active_execution is not None:
            active_execution.cancel_requested_at = now
            self._timeline(
                session,
                task=task,
                event_type=DomainEventType.TASK_CANCEL_REQUESTED.value,
                message="已请求取消；正在运行的调用会执行到安全检查点后停止。",
                status_from=task.status,
                status_to=task.status,
                actor_type=EventActorType.OWNER,
            )
        else:
            # Nobody will finish it: no live execution owns a checkpoint.
            previous_status = task.status
            task.status = TaskStatus.CANCELLED.value
            task.finished_at = now
            for execution in session.scalars(
                select(TaskExecution)
                .where(TaskExecution.task_id == task.id, TaskExecution.status == "RUNNING")
                .with_for_update()
            ).all():
                execution.cancel_requested_at = now
            self._resolve_envelope_on_cancel(
                session, task=task, was_queued=previous_status == TaskStatus.QUEUED.value
            )
            self._timeline(
                session,
                task=task,
                event_type=DomainEventType.TASK_CANCELLED.value,
                message=(
                    "任务已取消；未完成的调用不再等待 Worker 收尾，已按记录到的调用证据处理额度。"
                ),
                status_from=previous_status,
                status_to=TaskStatus.CANCELLED.value,
                actor_type=EventActorType.OWNER,
            )
            self._emit(
                session,
                event_type=DomainEventType.TASK_CANCELLED,
                project_id=task.project_id,
                task_id=task.id,
                payload={
                    "taskId": task.id,
                    "status": task.status,
                    "stage": "NO_ACTIVE_EXECUTION",
                },
            )

        self._audit.add(
            session,
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            action=AuditAction.TASK_CANCEL_REQUESTED.value,
            outcome="SUCCESS",
            target_type="TASK",
            target_id=str(task.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=(reason or "")[:MAX_REASON] or None,
        )
        return task

    def _resolve_envelope_on_cancel(self, session: Session, *, task: Task, was_queued: bool) -> int:
        """Close the planning envelope of a cancelled attempt from real evidence.

        Releasing is only correct when nothing could have been charged: no call
        hold was ever opened and no step row exists. A cancel request after a
        call started proves nothing about that call, so its envelope is settled
        conservatively and flagged instead (R5).
        """
        reservations = session.scalars(
            select(BudgetReservation)
            .where(
                BudgetReservation.task_id == task.id,
                BudgetReservation.kind == ReservationKind.PLANNING.value,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
            .with_for_update()
        ).all()
        if not reservations:
            return 0

        # A queued retry has a new envelope but no execution yet. Evidence from
        # its previous attempts must never charge this unused envelope again.
        if was_queued:
            return self._budget.release_planning_holds(
                session, task_id=task.id, detail="cancelled-before-start"
            )
        execution_id = session.scalar(
            select(TaskExecution.id).where(
                TaskExecution.task_id == task.id,
                TaskExecution.attempt == task.attempt_count,
            )
        )

        call_holds = session.scalar(
            select(func.count())
            .select_from(BudgetReservation)
            .where(
                BudgetReservation.task_id == task.id,
                BudgetReservation.execution_id == execution_id,
                BudgetReservation.kind == ReservationKind.CALL.value,
                BudgetReservation.status != ReservationStatus.RELEASED.value,
            )
        )
        step_rows = session.scalar(
            select(func.count())
            .select_from(TaskExecutionStep)
            .where(TaskExecutionStep.execution_id == execution_id)
        )
        if not call_holds and not step_rows:
            return self._budget.release_planning_holds(
                session, task_id=task.id, detail="cancelled-before-start"
            )
        return self._budget.settle_planning_holds(
            session,
            task_id=task.id,
            actual_tokens=None,
            known=False,
            detail="cancelled-after-call-started",
        )

    def request_retry(
        self,
        session: Session,
        *,
        task_id: int,
        owner_user_id: int,
        idempotency_key: str,
        expected_attempt: int | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Task:
        """Re-queue a terminal or interrupted task as a *new* attempt.

        The previous execution row is never reused: the new attempt gets its own
        id, its own steps and its own ledger entries, so the earlier evidence
        stays intact.
        """
        company = self._budget.lock_company(session)
        task = session.scalars(
            select(Task)
            .where(Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if task is None:
            raise TaskNotFoundError()
        if not idempotency_key.strip() or len(idempotency_key) > MAX_IDEMPOTENCY_KEY:
            raise ValidationFailedError(
                [FieldError("idempotencyKey", "重试需要 1–80 个字符的请求编号。")]
            )
        fingerprint = content_fingerprint({"taskId": task_id, "expectedAttempt": expected_attempt})
        previous_command = session.scalar(
            select(TaskRetryCommand).where(
                TaskRetryCommand.company_id == company.id,
                TaskRetryCommand.idempotency_key == idempotency_key,
            )
        )
        if previous_command is not None:
            if previous_command.request_fingerprint != fingerprint:
                raise IdempotencyConflictError("task-retry")
            return task
        if company.status == CompanyStatus.PAUSED.value:
            raise CompanyPausedError()
        if expected_attempt is not None and task.attempt_count != expected_attempt:
            raise ConcurrentUpdateError("task")
        if TaskStatus(task.status) not in {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.INTERRUPTED,
        }:
            raise InvalidTaskTransitionError(task.status, "retry")
        if task.cancel_requested_at is not None:
            raise InvalidTaskTransitionError(task.status, "retry-cancelled")

        project = self.get_project(session, task.project_id)
        if project.run_mode == RunMode.REAL.value:
            raise RunModeUnavailableError(RunMode.REAL.value)

        # A new attempt needs the envelope again: the previous one was released
        # when the task became terminal/interrupted. Both holds are one unit —
        # a savepoint keeps a rejected project hold from leaving the company one
        # behind, while preserving everything written before this savepoint.
        with session.begin_nested():
            self._budget.reserve_company_allowance(
                session,
                amount=task.budget_tokens,
                kind=ReservationKind.PLANNING,
                owner_ref=task.id,
                project_id=task.project_id,
                task_id=task.id,
                execution_id=None,
                detail=f"task-retry:{task.id}",
            )
            self._budget.reserve_project_allowance(
                session,
                project=project,
                amount=task.budget_tokens,
                kind=ReservationKind.PLANNING,
                owner_ref=task.id,
                task_id=task.id,
                execution_id=None,
                detail=f"task-retry:{task.id}",
            )

        session.add(
            TaskRetryCommand(
                company_id=company.id,
                task_id=task.id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                target_attempt=task.attempt_count + 1,
                actor_user_id=owner_user_id,
            )
        )
        previous_status = task.status
        task.status = TaskStatus.QUEUED.value
        task.queued_at = utcnow()
        task.finished_at = None
        task.last_error_code = None
        task.last_error_message = None
        task.version += 1
        self._timeline(
            session,
            task=task,
            event_type=DomainEventType.TASK_REQUEUED.value,
            message="任务已重新排队，将创建新的执行记录。",
            status_from=previous_status,
            status_to=TaskStatus.QUEUED.value,
            actor_type=EventActorType.OWNER,
        )
        self._emit(
            session,
            event_type=DomainEventType.TASK_REQUEUED,
            project_id=task.project_id,
            task_id=task.id,
            payload={"taskId": task.id, "status": task.status},
        )
        self._audit.add(
            session,
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            action=AuditAction.TASK_RETRIED.value,
            outcome="SUCCESS",
            target_type="TASK",
            target_id=str(task.id),
            request_id=request_id,
            ip_address=ip_address,
        )
        return task

    # ---------------------------------------------------------------- helpers
    def _timeline(
        self,
        session: Session,
        *,
        task: Task,
        event_type: str,
        message: str,
        status_from: str | None = None,
        status_to: str | None = None,
        execution_id: int | None = None,
        actor_type: EventActorType = EventActorType.SYSTEM,
        detail: str | None = None,
    ) -> TaskEvent:
        event = TaskEvent(
            task_id=task.id,
            execution_id=execution_id,
            event_type=event_type,
            status_from=status_from,
            status_to=status_to,
            actor_type=actor_type.value,
            message=message,
            detail=detail,
        )
        session.add(event)
        session.flush()
        return event

    def _emit(
        self,
        session: Session,
        *,
        event_type: DomainEventType,
        project_id: int | None,
        task_id: int | None,
        execution_id: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_type=event_type.value,
            company_id=1,
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            payload=payload or {},
        )
        session.add(event)
        session.flush()
        return event


class CompanyGovernanceService:
    """Company-wide pause. Blocks *new* work only; history is never rewritten."""

    def __init__(self) -> None:
        self._audit = AuditRepository()

    def pause(
        self,
        session: Session,
        *,
        owner_user_id: int,
        reason: str | None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Company:
        company = session.scalars(
            select(Company)
            .where(Company.id == 1)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if company is None:
            raise CompanyNotFoundError()
        if company.status == CompanyStatus.PAUSED.value:
            return company
        company.status = CompanyStatus.PAUSED.value
        company.paused_at = utcnow()
        company.pause_reason = (reason or "")[:MAX_REASON] or None
        company.version += 1
        self._audit.add(
            session,
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            action=AuditAction.COMPANY_PAUSED.value,
            outcome="SUCCESS",
            target_type="COMPANY",
            target_id="1",
            request_id=request_id,
            ip_address=ip_address,
            detail=company.pause_reason,
        )
        session.add(
            DomainEvent(
                event_type=DomainEventType.COMPANY_PAUSED.value,
                company_id=1,
                payload={
                    "status": company.status,
                    "reason": company.pause_reason,
                    "runningCallsFinish": True,
                },
            )
        )
        session.flush()
        return company

    def resume(
        self,
        session: Session,
        *,
        owner_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Company:
        company = session.scalars(
            select(Company)
            .where(Company.id == 1)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if company is None:
            raise CompanyNotFoundError()
        if company.status == CompanyStatus.ACTIVE.value:
            return company
        company.status = CompanyStatus.ACTIVE.value
        company.paused_at = None
        company.pause_reason = None
        company.version += 1
        self._audit.add(
            session,
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            action=AuditAction.COMPANY_RESUMED.value,
            outcome="SUCCESS",
            target_type="COMPANY",
            target_id="1",
            request_id=request_id,
            ip_address=ip_address,
        )
        session.add(
            DomainEvent(
                event_type=DomainEventType.COMPANY_RESUMED.value,
                company_id=1,
                payload={"status": company.status},
            )
        )
        session.flush()
        return company
