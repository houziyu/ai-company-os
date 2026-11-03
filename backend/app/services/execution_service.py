"""Execution kernel: claiming, leasing, fenced writes, recovery and settlement.

This module is the only place that mutates an execution. Everything here is
built so that a crashed or evicted worker cannot corrupt the record:

Claim
    ``SELECT ... FOR UPDATE SKIP LOCKED`` over queued tasks, then one conditional
    ``UPDATE tasks SET status='RUNNING', fencing_counter = fencing_counter + 1
    WHERE id = :id AND status='QUEUED'``. The increment happens in SQL, so the
    fencing token is monotonic even if two workers somehow both reached the row.

Lock order
    ``company -> task -> execution -> budget`` for step writes and recovery.
    Company uses NO KEY UPDATE, compatible with a claim's foreign-key KEY SHARE.
    Claims lock only tasks/executions; heartbeats lock only executions. Neither
    later acquires a company lock, so concurrent SKIP LOCKED claims remain safe.
    A fence check and the step/ledger write that follows it are protected by
    the same row locks. Checking the token with an unlocked ``SELECT`` and then
    writing is *not* enough: recovery could commit ``INTERRUPTED`` in between.

Stop gate
    Every new paid call passes :meth:`ExecutionService._assert_startable` in the
    same transaction that opens the step and its hold. It reads the committed
    company state and the cancel markers under the task/execution locks, so a
    pause or cancel issued *before* the step starts is always honoured — a
    claimed-but-not-started attempt is no longer exempt.

Fenced writes
    Every write an attempt makes verifies ``fencing_token``, ``status='RUNNING'``
    and an unexpired ``lease_expires_at`` on the locked execution row. Failure
    raises :class:`LeaseLostError`; the caller must abandon the attempt rather
    than retry the write. That is what makes a late result harmless.

Heartbeat
    Extends ``lease_expires_at`` only while the token matches *and* the lease has
    not already expired. A worker that cannot extend its lease therefore *knows*
    it lost the attempt, and it cannot resurrect an expired lease.

Recovery
    Leases past expiry are marked INTERRUPTED with
    ``interrupted_unknown_side_effects = true``. Automatic re-queue happens only
    when the processor declares its steps idempotent (the demo processor does) and
    the retry budget allows it; otherwise the task waits for a human. Recovery
    never assumes the outside world was left unchanged, and a retry that cannot
    obtain its allowance is recorded as BLOCKED instead of aborting recovery.

Settlement
    Actual usage ends up in three ledgers because three allowances were checked:
    task call holds, then the company and project planning envelopes. Each is
    settled once (unique ledger index), and unknown usage consumes the hold and is
    flagged for reconciliation instead of being reported as zero. A planning
    envelope is released only when the recorded evidence proves no call started;
    a cancel marker alone is not evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.enums import (
    DomainEventType,
    EventActorType,
    ExecutionStopReason,
    ReservationKind,
    ReservationStatus,
    StepStatus,
    TaskErrorCode,
    TaskStatus,
)
from app.domain.errors import (
    AppError,
    BudgetInsufficientError,
    ExecutionNotFoundError,
    ExecutionStoppedError,
    LeaseLostError,
    TaskNotFoundError,
)
from app.models.organization import Company
from app.models.tasks import (
    BudgetAccount,
    BudgetReservation,
    DomainEvent,
    Project,
    Task,
    TaskEvent,
    TaskExecution,
    TaskExecutionStep,
)
from app.repositories.auth_repository import utcnow
from app.services.budget_service import BudgetService

logger = logging.getLogger(__name__)

# Lease is shorter than the acceptance wait so a killed worker is detected quickly
# in tests while still being generous compared with a real provider call.
DEFAULT_LEASE_SECONDS = 30.0
HEARTBEAT_INTERVAL_SECONDS = 5.0


@dataclass(slots=True)
class ClaimedExecution:
    """Everything a worker needs after a successful claim."""

    execution_id: int
    task_id: int
    project_id: int
    attempt: int
    fencing_token: int
    demo_behavior: str
    task_budget_tokens: int
    title: str


@dataclass(slots=True)
class ProcessorOutcome:
    """What a processor reports back. Unknown usage is ``None``, never 0.

    ``stopped_reason`` and ``blocked`` exist because "the attempt could not
    continue" is not the same as "the work failed": a pause/cancel stop leaves a
    human-actionable state, and a refused allowance blocks further automatic
    progress. Collapsing all three into FAILED would misreport what happened.
    """

    succeeded: bool
    summary: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    usage_known: bool
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    cancelled: bool = False
    stopped_reason: str | None = None
    blocked: bool = False


@dataclass(slots=True)
class AttemptUsage:
    """Usage derived from the step rows of one attempt.

    Steps are the evidence: the step row is committed *before* its call starts and
    closed *after* it returns, so a RUNNING step means "usage not yet known". A
    single unknown step makes the whole attempt unknown — summing only the known
    part would report a confident number that is silently too small (R6).
    """

    prompt_tokens: int | None
    completion_tokens: int | None
    usage_known: bool
    has_steps: bool


@dataclass(slots=True)
class CallEvidence:
    """Whether any paid call may have started for an attempt."""

    has_any: bool
    has_running_step: bool
    settled_tokens: int | None
    usage_known: bool


class ExecutionService:
    def __init__(
        self,
        *,
        budget_service: BudgetService | None = None,
        default_company_limit: int | None = None,
    ) -> None:
        """Accept the shared budget service so the configured default allowance is
        the same one the API and the probe path use (R7)."""
        if budget_service is not None:
            self._budget = budget_service
        elif default_company_limit is not None:
            self._budget = BudgetService(default_company_limit=default_company_limit)
        else:
            self._budget = BudgetService()

    # ----------------------------------------------------------------- claiming
    def claim_next(
        self,
        session: Session,
        *,
        worker_id: str,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        project_id: int | None = None,
    ) -> ClaimedExecution | None:
        """Claim one queued task. Returns None when the queue is empty.

        ``SKIP LOCKED`` is what lets two workers hit the queue simultaneously
        without either waiting for the other: each locks a different row, and the
        conditional UPDATE guarantees only one of them changes the status.

        The company status is read without a lock on purpose. Pausing must not
        deadlock against a claim; the authoritative check is the per-step gate,
        which runs under the task lock and sees every committed pause.
        """
        company = session.get(Company, 1)
        if company is None:
            # A fresh deployment has no company until the owner finishes the
            # wizard. That is not an error the worker should crash on: there is
            # simply nothing to claim yet.
            return None
        if company.status == "PAUSED":
            # Paused means no *new* paid calls; the per-step gate stops existing
            # attempts at their next checkpoint.
            return None

        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.QUEUED.value)
            .order_by(Task.queued_at.asc().nulls_first(), Task.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        task = session.scalars(stmt).one_or_none()
        if task is None:
            return None

        project = session.get(Project, task.project_id)
        if project is None:  # pragma: no cover - FK guarantees it
            raise RuntimeError(f"task {task.id} references a missing project")

        now = utcnow()
        result = session.execute(
            update(Task)
            .where(Task.id == task.id, Task.status == TaskStatus.QUEUED.value)
            .values(
                status=TaskStatus.RUNNING.value,
                fencing_counter=Task.fencing_counter + 1,
                attempt_count=Task.attempt_count + 1,
                started_at=func.coalesce(Task.started_at, now),
                version=Task.version + 1,
            )
            .returning(Task.fencing_counter, Task.attempt_count)
        )
        row = result.one_or_none()
        if row is None:
            # Another worker won between SELECT and UPDATE. Treat as "nothing to do".
            return None
        fencing_token, attempt = int(row[0]), int(row[1])

        previous = session.scalars(
            select(TaskExecution)
            .where(TaskExecution.task_id == task.id)
            .order_by(TaskExecution.attempt.desc())
            .limit(1)
        ).one_or_none()

        execution = TaskExecution(
            task_id=task.id,
            project_id=task.project_id,
            company_id=task.company_id,
            attempt=attempt,
            status="RUNNING",
            run_mode=task.run_mode,
            demo_behavior=task.demo_behavior,
            worker_id=worker_id,
            fencing_token=fencing_token,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            started_at=now,
            reserved_tokens=task.budget_tokens,
            retried_from_execution_id=previous.id if previous else None,
        )
        session.add(execution)
        session.flush()

        self._timeline(
            session,
            task=task,
            execution_id=execution.id,
            event_type=DomainEventType.TASK_STARTED.value,
            message=f"执行 #{execution.attempt} 已由 {worker_id} 领取。",
            status_from=TaskStatus.QUEUED.value,
            status_to=TaskStatus.RUNNING.value,
            actor_type=EventActorType.WORKER,
            detail=f"演练处理器（{task.demo_behavior}）",
        )
        self._emit(
            session,
            event_type=DomainEventType.TASK_STARTED,
            project_id=task.project_id,
            task_id=task.id,
            execution_id=execution.id,
            payload={
                "taskId": task.id,
                "executionId": execution.id,
                "attempt": attempt,
                "status": TaskStatus.RUNNING.value,
                "workerId": worker_id,
            },
        )
        return ClaimedExecution(
            execution_id=execution.id,
            task_id=task.id,
            project_id=task.project_id,
            attempt=attempt,
            fencing_token=fencing_token,
            demo_behavior=task.demo_behavior,
            task_budget_tokens=task.budget_tokens,
            title=task.title,
        )

    # --------------------------------------------------------------- heartbeat
    def heartbeat(
        self,
        session: Session,
        *,
        execution_id: int,
        fencing_token: int,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """Extend the lease. False means the attempt is no longer ours.

        An expired lease is *not* extended: once it lapsed another worker may
        already have been scheduled to recover the attempt, so treating the beat
        as valid would let two attempts write concurrently (R2).
        """
        execution = session.scalars(
            select(TaskExecution)
            .where(TaskExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        # Take the timestamp AFTER waiting for the lock. A timestamp captured
        # before a blocked UPDATE can revive a lease that expired while waiting.
        now = utcnow()
        if (
            execution is None
            or execution.fencing_token != fencing_token
            or execution.status != "RUNNING"
            or execution.lease_expires_at is None
            or execution.lease_expires_at <= now
        ):
            return False
        execution.heartbeat_at = now
        execution.lease_expires_at = now + timedelta(seconds=lease_seconds)
        session.flush()
        return True

    # ------------------------------------------------------------------- steps
    def start_step(
        self,
        session: Session,
        *,
        execution_id: int,
        fencing_token: int,
        sequence: int,
        name: str,
        estimated_tokens: int,
    ) -> TaskExecutionStep:
        """Open a step and hold its estimated cost at task scope.

        The hold happens *before* the call, in the same transaction that records
        the step, so a crash between the two cannot produce a step with no hold or
        a hold with no step.

        This is also the single stop gate: company pause, cancel request, valid
        lease and the task allowance are all checked while the task and execution
        rows are locked. A step therefore cannot start after a committed pause or
        cancel, and cannot start on an execution recovery has already closed.
        """
        task, execution = self._lock_fenced_execution(
            session, execution_id=execution_id, fencing_token=fencing_token
        )
        self._assert_startable(session, task=task, execution=execution)

        step = TaskExecutionStep(
            execution_id=execution_id,
            sequence=sequence,
            name=name,
            status=StepStatus.RUNNING.value,
            estimated_tokens=estimated_tokens,
            started_at=utcnow(),
        )
        session.add(step)
        session.flush()

        self._budget.reserve_task_allowance(
            session,
            task=task,
            step_id=step.id,
            execution_id=execution_id,
            amount=estimated_tokens,
            detail=f"step:{name}",
        )
        self._step_changed(session, task=task, execution=execution, step=step)
        return step

    def finish_step(
        self,
        session: Session,
        *,
        execution_id: int,
        fencing_token: int,
        step_id: int,
        status: StepStatus,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        usage_known: bool,
        detail: str | None = None,
    ) -> None:
        """Close a step and settle its hold with the reported usage.

        A cancel request does not block this write: closing the step *is* how a
        running call reaches its safe point.
        """
        task, execution = self._lock_fenced_execution(
            session, execution_id=execution_id, fencing_token=fencing_token
        )
        step = session.scalar(
            select(TaskExecutionStep)
            .where(
                TaskExecutionStep.id == step_id,
                TaskExecutionStep.execution_id == execution_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if step is None:
            raise ExecutionNotFoundError()
        if status is StepStatus.RUNNING:
            raise AppError("INVALID_STEP_RESULT", "完成步骤必须提供终态。", 409)
        if step.status != StepStatus.RUNNING.value:
            if (
                step.status,
                step.prompt_tokens,
                step.completion_tokens,
                step.usage_known,
                step.detail,
            ) == (status.value, prompt_tokens, completion_tokens, usage_known, detail):
                return
            raise AppError("STEP_RESULT_CONFLICT", "步骤已经结束，不能覆盖历史结果。", 409)
        total = (
            (prompt_tokens or 0) + (completion_tokens or 0)
            if usage_known and (prompt_tokens is not None or completion_tokens is not None)
            else None
        )
        step.status = status.value
        step.finished_at = utcnow()
        step.prompt_tokens = prompt_tokens
        step.completion_tokens = completion_tokens
        step.usage_known = usage_known
        step.attempts = 1
        step.detail = detail

        reservation = session.scalars(
            select(BudgetReservation)
            .where(
                BudgetReservation.step_id == step_id,
                BudgetReservation.kind == ReservationKind.CALL.value,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
            .with_for_update()
        ).one_or_none()
        if reservation is not None:
            # A persisted step means its call may have started. Cancellation
            # does not prove zero cost; unknown usage consumes the hold.
            self._budget.settle(
                session,
                reservation_id=reservation.id,
                actual_tokens=total,
                known=usage_known,
                detail=f"step:{step.name}",
            )
        session.flush()

        self._step_changed(session, task=task, execution=execution, step=step)

    def _step_changed(
        self, session: Session, *, task: Task, execution: TaskExecution, step: TaskExecutionStep
    ) -> None:
        self._emit(
            session,
            event_type=DomainEventType.TASK_STEP_UPDATED,
            project_id=task.project_id,
            task_id=task.id,
            execution_id=execution.id,
            payload={"stepId": step.id, "status": step.status, "sequence": step.sequence},
        )
        self._timeline(
            session,
            task=task,
            execution_id=execution.id,
            event_type=DomainEventType.TASK_STEP_UPDATED.value,
            message=f"步骤「{step.name}」状态已更新：{step.status}。",
            actor_type=EventActorType.WORKER,
        )

    # -------------------------------------------------------------- completion
    def complete(
        self,
        session: Session,
        *,
        execution_id: int,
        fencing_token: int,
        outcome: ProcessorOutcome,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> Task:
        """Write the attempt's final state. Refuses if the lease is gone.

        Success is recorded only from an outcome the processor reported; nothing
        here infers success from a timeout or a closed connection. Usage is taken
        from the step evidence, not only from the processor's summary, so a failed
        step with unknown usage cannot be reported as a fully known attempt (R6).
        """
        task, execution = self._lock_fenced_execution(
            session, execution_id=execution_id, fencing_token=fencing_token
        )
        now = utcnow()
        previous_status = task.status

        step_usage = self._step_usage(session, execution_id=execution_id)
        if step_usage.has_steps and not step_usage.usage_known:
            # One unknown step makes the attempt unknown; a partial sum would be
            # a confident number that is too small.
            prompt = completion = None
            usage_known = False
        elif step_usage.has_steps:
            prompt = step_usage.prompt_tokens
            completion = step_usage.completion_tokens
            usage_known = True
        else:
            prompt = outcome.prompt_tokens
            completion = outcome.completion_tokens
            usage_known = outcome.usage_known

        total_known = (
            (prompt or 0) + (completion or 0)
            if usage_known and (prompt is not None or completion is not None)
            else None
        )

        cancel_pending = (
            task.cancel_requested_at is not None or execution.cancel_requested_at is not None
        )
        stopped = ExecutionStopReason(outcome.stopped_reason) if outcome.stopped_reason else None

        execution.finished_at = now
        execution.lease_expires_at = None
        execution.prompt_tokens = prompt
        execution.completion_tokens = completion
        execution.usage_known = usage_known
        execution.error_code = outcome.error_code
        execution.error_message = (outcome.error_message or "")[:500] or None
        execution.result_summary = (outcome.summary or "")[:2000] or None
        execution.settled_tokens = total_known
        execution.needs_reconciliation = not usage_known

        if outcome.cancelled or stopped is ExecutionStopReason.CANCELLED:
            execution.status = "CANCELLED"
            next_status = TaskStatus.CANCELLED
        elif stopped is ExecutionStopReason.COMPANY_PAUSED:
            # No call was made. The attempt stops at a checkpoint; the task stays
            # actionable (a human resumes the company and retries) instead of
            # being reported as a failure of the work.
            execution.status = "INTERRUPTED"
            execution.interrupted_unknown_side_effects = False
            execution.recovery_note = "公司在安全检查点暂停；未启动新的调用。"
            next_status = TaskStatus.INTERRUPTED
        elif outcome.succeeded and cancel_pending:
            # The steps finished, but the owner asked to stop before the attempt
            # was recorded. The attempt evidence stays SUCCEEDED; the task state
            # follows the owner's command and the timeline explains the split.
            execution.status = "SUCCEEDED"
            next_status = TaskStatus.CANCELLED
            execution.result_summary = (
                (outcome.summary or "")[:1800]
                + "（取消请求到达时步骤已全部完成；已按实际用量结算。）"
            )[:2000]
        elif outcome.succeeded:
            execution.status = "SUCCEEDED"
            next_status = TaskStatus.SUCCEEDED
        elif outcome.blocked:
            execution.status = "BLOCKED"
            execution.error_code = outcome.error_code or TaskErrorCode.BUDGET_INSUFFICIENT.value
            next_status = TaskStatus.BLOCKED
        elif outcome.retryable and self._retry_allowed(task):
            execution.status = "FAILED"
            next_status = TaskStatus.QUEUED
        else:
            execution.status = "FAILED"
            next_status = TaskStatus.FAILED

        # Close the open call holds first, then the company/project envelope.
        self._budget.settle_all_open_for_task(
            session,
            task_id=task.id,
            actual_tokens=total_known,
            known=usage_known,
            detail=f"execution:{execution.id}",
        )
        self._close_envelope(
            session,
            task=task,
            execution=execution,
            total_known=total_known,
            usage_known=usage_known,
            detail=f"execution:{execution.id}",
        )

        task.status = next_status.value
        task.version += 1
        if next_status is TaskStatus.QUEUED:
            task.queued_at = now
            task.last_error_code = outcome.error_code
            task.last_error_message = (outcome.error_message or "")[:500] or None
            task.finished_at = None
            # The retry needs its own envelope; open holds were settled above.
            project = session.get(Project, task.project_id)
            if project is not None:
                try:
                    # Both holds are one unit: if the project refuses, the
                    # company hold must not survive (R9). A savepoint rolls back
                    # only this attempt, leaving the settlement above committed.
                    with session.begin_nested():
                        self._budget.reserve_company_allowance(
                            session,
                            amount=task.budget_tokens,
                            kind=ReservationKind.PLANNING,
                            owner_ref=task.id,
                            project_id=task.project_id,
                            task_id=task.id,
                            execution_id=None,
                            detail=f"auto-retry:{task.id}",
                        )
                        self._budget.reserve_project_allowance(
                            session,
                            project=project,
                            amount=task.budget_tokens,
                            kind=ReservationKind.PLANNING,
                            owner_ref=task.id,
                            task_id=task.id,
                            execution_id=None,
                            detail=f"auto-retry:{task.id}",
                        )
                except BudgetInsufficientError:
                    # No room for another attempt: stop honestly instead of
                    # retrying without an allowance, and leave no partial hold.
                    task.status = TaskStatus.BLOCKED.value
                    task.last_error_code = TaskErrorCode.BUDGET_INSUFFICIENT.value
                    task.last_error_message = "自动重试需要新的额度预留，但可用额度不足。"
                    task.finished_at = now
                    next_status = TaskStatus.BLOCKED
        else:
            task.finished_at = (
                now
                if next_status
                in {
                    TaskStatus.SUCCEEDED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }
                else None
            )
            if next_status is TaskStatus.SUCCEEDED:
                task.result_summary = (outcome.summary or "")[:2000] or None
                task.last_error_code = None
                task.last_error_message = None
            elif next_status is TaskStatus.CANCELLED and outcome.succeeded:
                task.result_summary = execution.result_summary
                task.last_error_code = TaskErrorCode.CANCELLED_BY_OWNER.value
                task.last_error_message = "取消请求到达时步骤已全部完成；已按实际用量结算。"
            else:
                task.last_error_code = outcome.error_code
                task.last_error_message = (outcome.error_message or "")[:500] or None

        event_type = {
            TaskStatus.SUCCEEDED: DomainEventType.TASK_SUCCEEDED,
            TaskStatus.FAILED: DomainEventType.TASK_FAILED,
            TaskStatus.CANCELLED: DomainEventType.TASK_CANCELLED,
            TaskStatus.QUEUED: DomainEventType.TASK_RETRY_SCHEDULED,
            TaskStatus.INTERRUPTED: DomainEventType.TASK_INTERRUPTED,
            TaskStatus.BLOCKED: DomainEventType.TASK_BLOCKED,
        }.get(next_status, DomainEventType.TASK_FAILED)

        message = {
            TaskStatus.SUCCEEDED: "执行完成，结果与用量已记录。",
            TaskStatus.FAILED: "执行失败，已记录原因；历史尝试保留。",
            TaskStatus.CANCELLED: "执行已按请求停止。",
            TaskStatus.QUEUED: "执行失败，已按策略安排有限次自动重试。",
            TaskStatus.INTERRUPTED: "公司在安全检查点暂停，未启动新的调用。",
            TaskStatus.BLOCKED: "执行无法继续，等待人工处理。",
        }.get(next_status, "执行状态已更新。")

        self._timeline(
            session,
            task=task,
            execution_id=execution.id,
            event_type=event_type.value,
            message=message,
            status_from=previous_status,
            status_to=task.status,
            actor_type=EventActorType.WORKER,
            detail=outcome.error_message,
        )
        self._emit(
            session,
            event_type=event_type,
            project_id=task.project_id,
            task_id=task.id,
            execution_id=execution.id,
            payload={
                "taskId": task.id,
                "executionId": execution.id,
                "attempt": execution.attempt,
                "status": task.status,
                "usageKnown": usage_known,
                "settledTokens": total_known,
            },
        )
        return task

    def _retry_allowed(self, task: Task) -> bool:
        """Automatic retry needs a remaining attempt *and* an idempotent processor.

        The demo processor declares its steps idempotent, so a transient simulated
        failure may retry. Anything with unknown external side effects must not be
        replayed automatically; such a step would set ``retryable=False`` at the
        processor level instead of relying on this counter alone.
        """
        return task.attempt_count <= task.max_auto_retries

    # -------------------------------------------------------------- recovery
    def recover_expired_leases(
        self,
        session: Session,
        *,
        idempotent_processor: bool = True,
        limit: int = 50,
    ) -> list[int]:
        """Mark stale attempts INTERRUPTED and decide whether to re-queue.

        Used on worker start and periodically. The decision is written to
        ``recovery_note`` so an operator can see *why* a task resumed or did not.

        The company row is locked first, then each task and execution, matching
        the module-wide lock order. A retry whose envelope cannot be granted is
        stored as BLOCKED inside a savepoint: the interruption itself is never
        rolled back and one un-affordable task cannot abort the whole batch (R4).
        """
        company = session.scalars(
            select(Company)
            .where(Company.id == 1)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if company is None:
            return []

        now = utcnow()
        candidate_ids = list(
            session.scalars(
                select(TaskExecution.id)
                .where(
                    TaskExecution.status == "RUNNING",
                    TaskExecution.lease_expires_at.is_not(None),
                    TaskExecution.lease_expires_at < now,
                )
                .order_by(TaskExecution.id)
                .limit(limit)
            ).all()
        )
        recovered: list[int] = []
        for execution_id in candidate_ids:
            try:
                task_id = self._recover_one(
                    session,
                    execution_id=execution_id,
                    now=now,
                    idempotent_processor=idempotent_processor,
                )
            except AppError as exc:
                # A business error for one attempt must not undo the batch; the
                # attempt stays RUNNING and the next scan retries it.
                logger.warning(
                    "recovery skipped",
                    extra={"execution_id": execution_id, "error": exc.code},
                )
                continue
            if task_id is not None:
                recovered.append(task_id)
        return recovered

    def _recover_one(
        self,
        session: Session,
        *,
        execution_id: int,
        now: object,
        idempotent_processor: bool,
    ) -> int | None:
        execution_ref = session.execute(
            select(TaskExecution.task_id).where(TaskExecution.id == execution_id)
        ).one_or_none()
        if execution_ref is None:  # pragma: no cover - row was just selected
            return None
        task = session.scalars(
            select(Task)
            .where(Task.id == execution_ref[0])
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if task is None:  # pragma: no cover
            return None
        execution = session.scalars(
            select(TaskExecution)
            .where(TaskExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if execution is None:  # pragma: no cover
            return None
        # Re-verify under the lock: another recovery worker may have handled it,
        # or a late heartbeat may have renewed the lease before we got here.
        if (
            execution.status != "RUNNING"
            or execution.lease_expires_at is None
            or execution.lease_expires_at >= utcnow()  # type: ignore[operator]
        ):
            return None

        evidence = self._call_evidence(
            session, task_id=task.id, execution_id=execution.id, include_running=True
        )
        execution.status = "INTERRUPTED"
        execution.finished_at = utcnow()
        execution.lease_expires_at = None
        execution.interrupted_unknown_side_effects = True
        execution.error_code = TaskErrorCode.WORKER_LOST.value
        execution.error_message = "Worker 进程未在租约内完成；结果与外部副作用未知。"
        # Close every step left RUNNING so the attempt's evidence is complete
        # before the envelope decision reads it.
        self._interrupt_running_steps(session, execution_id=execution.id)

        cancelled = task.cancel_requested_at is not None or execution.cancel_requested_at
        retry = (
            idempotent_processor and not cancelled and task.attempt_count <= task.max_auto_retries
        )

        # Whatever the attempt held is now unaccounted for. Consume the holds
        # and flag them: releasing would under-report a possibly paid call.
        self._budget.settle_all_open_for_task(
            session,
            task_id=task.id,
            actual_tokens=None,
            known=False,
            detail=f"interrupted:{execution.id}",
        )

        if cancelled:
            task.status = TaskStatus.CANCELLED.value
            task.finished_at = utcnow()
            execution.recovery_note = "租约过期；取消已请求，记录为已取消。"
            # A cancel marker is not proof that no call happened (R5). Release
            # only when the recorded evidence shows no call ever started.
            self._close_envelope(
                session,
                task=task,
                execution=execution,
                total_known=None,
                usage_known=False,
                detail=f"interrupted-cancel:{execution.id}",
                evidence=evidence,
            )
            next_event = DomainEventType.TASK_CANCELLED
        elif retry:
            execution.recovery_note = "租约过期；处理器步骤幂等且仍有重试次数，已重新排队。"
            self._close_envelope(
                session,
                task=task,
                execution=execution,
                total_known=None,
                usage_known=False,
                detail=f"interrupted:{execution.id}",
                evidence=evidence,
            )
            project = session.get(Project, task.project_id)
            if project is None:  # pragma: no cover - FK guarantees it
                task.status = TaskStatus.BLOCKED.value
                task.last_error_code = TaskErrorCode.BUDGET_INSUFFICIENT.value
                task.last_error_message = "重试需要的项目额度预留无法创建。"
                next_event = DomainEventType.TASK_BLOCKED
            else:
                try:
                    with session.begin_nested():
                        self._budget.reserve_company_allowance(
                            session,
                            amount=task.budget_tokens,
                            kind=ReservationKind.PLANNING,
                            owner_ref=task.id,
                            project_id=task.project_id,
                            task_id=task.id,
                            execution_id=None,
                            detail=f"recovery-retry:{task.id}",
                        )
                        self._budget.reserve_project_allowance(
                            session,
                            project=project,
                            amount=task.budget_tokens,
                            kind=ReservationKind.PLANNING,
                            owner_ref=task.id,
                            task_id=task.id,
                            execution_id=None,
                            detail=f"recovery-retry:{task.id}",
                        )
                except BudgetInsufficientError:
                    # The interruption above stays committed; only the failed
                    # reservation attempt is rolled back (R4).
                    task.status = TaskStatus.BLOCKED.value
                    task.finished_at = utcnow()
                    task.last_error_code = TaskErrorCode.BUDGET_INSUFFICIENT.value
                    task.last_error_message = (
                        "租约已过期，且自动重试需要的额度预留不足；等待人工重试或追加额度。"
                    )
                    execution.recovery_note = (
                        "租约过期；处理器幂等，但重试额度预留失败，已标记 BLOCKED 等待人工处理。"
                    )
                    next_event = DomainEventType.TASK_BLOCKED
                else:
                    task.status = TaskStatus.QUEUED.value
                    task.queued_at = utcnow()
                    task.finished_at = None
                    next_event = DomainEventType.TASK_REQUEUED
        else:
            # Unknown side effects and no safe replay: stop and ask a human.
            task.status = TaskStatus.INTERRUPTED.value
            task.finished_at = None
            execution.recovery_note = "租约过期；副作用未知，未自动重放，等待人工重试。"
            self._close_envelope(
                session,
                task=task,
                execution=execution,
                total_known=None,
                usage_known=False,
                detail=f"interrupted:{execution.id}",
                evidence=evidence,
            )
            next_event = DomainEventType.TASK_INTERRUPTED

        if next_event is not DomainEventType.TASK_BLOCKED or task.last_error_code is None:
            task.last_error_code = TaskErrorCode.WORKER_LOST.value
            task.last_error_message = execution.error_message
        task.version += 1
        self._timeline(
            session,
            task=task,
            execution_id=execution.id,
            event_type=next_event.value,
            message=execution.recovery_note or "",
            status_from=TaskStatus.RUNNING.value,
            status_to=task.status,
            actor_type=EventActorType.SYSTEM,
        )
        self._emit(
            session,
            event_type=next_event,
            project_id=task.project_id,
            task_id=task.id,
            execution_id=execution.id,
            payload={
                "taskId": task.id,
                "executionId": execution.id,
                "status": task.status,
                "reason": "WORKER_LOST",
            },
        )
        return task.id

    # ---------------------------------------------------------------- helpers
    def _lock_fenced_execution(
        self, session: Session, *, execution_id: int, fencing_token: int
    ) -> tuple[Task, TaskExecution]:
        """Lock company, task, execution and verify the attempt may still write.

        The lock order is ``company -> task -> execution``, including recovery,
        so the verification cannot be invalidated by a
        concurrent recovery between the check and the following write (R3).

        The lease is checked here as well as in :meth:`heartbeat`: a worker whose
        lease lapsed must not write even if recovery has not run yet (R2).
        """
        self._budget.lock_company(session)
        execution_ref = session.execute(
            select(TaskExecution.task_id).where(TaskExecution.id == execution_id)
        ).one_or_none()
        if execution_ref is None:
            raise ExecutionNotFoundError()
        task = session.scalars(
            select(Task)
            .where(Task.id == execution_ref[0])
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if task is None:  # pragma: no cover - FK guarantees it
            raise TaskNotFoundError()
        execution = session.scalars(
            select(TaskExecution)
            .where(TaskExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if execution is None:  # pragma: no cover - row was just read
            raise ExecutionNotFoundError()
        if execution.fencing_token != fencing_token or execution.status != "RUNNING":
            raise LeaseLostError(execution_id)
        if execution.lease_expires_at is None or execution.lease_expires_at <= utcnow():
            raise LeaseLostError(execution_id)
        return task, execution

    def _assert_startable(self, session: Session, *, task: Task, execution: TaskExecution) -> None:
        """The single gate before any new paid call.

        Runs with the task and execution rows locked. The company status is read
        from the database (not the identity map) so a pause committed by another
        transaction is always visible. Checks are ordered most-specific first:
        a cancel request, then a paused company.
        """
        if task.cancel_requested_at is not None or execution.cancel_requested_at is not None:
            raise ExecutionStoppedError(
                ExecutionStopReason.CANCELLED.value, "取消已请求，未启动新的调用。"
            )
        company_status = session.scalar(select(Company.status).where(Company.id == task.company_id))
        if company_status == "PAUSED":
            raise ExecutionStoppedError(
                ExecutionStopReason.COMPANY_PAUSED.value,
                "公司已暂停，未启动新的调用。",
            )

    def _step_usage(self, session: Session, *, execution_id: int) -> AttemptUsage:
        rows = session.execute(
            select(
                TaskExecutionStep.status,
                TaskExecutionStep.prompt_tokens,
                TaskExecutionStep.completion_tokens,
                TaskExecutionStep.usage_known,
            ).where(TaskExecutionStep.execution_id == execution_id)
        ).all()
        if not rows:
            return AttemptUsage(None, None, False, False)
        if any(row[0] == StepStatus.RUNNING.value for row in rows) or any(
            not row[3] for row in rows
        ):
            return AttemptUsage(None, None, False, True)
        prompt = sum(int(row[1] or 0) for row in rows)
        completion = sum(int(row[2] or 0) for row in rows)
        return AttemptUsage(prompt, completion, True, True)

    def _call_evidence(
        self,
        session: Session,
        *,
        task_id: int,
        execution_id: int,
        include_running: bool = False,
    ) -> CallEvidence:
        """What the database can prove about calls in this attempt.

        A step row is committed before its call starts, so its existence means a
        call *may* have started. ``settled_tokens`` is filled only when every
        finished step reported usage and no step is unfinished.
        """
        steps = session.execute(
            select(
                TaskExecutionStep.status,
                TaskExecutionStep.prompt_tokens,
                TaskExecutionStep.completion_tokens,
                TaskExecutionStep.usage_known,
            ).where(TaskExecutionStep.execution_id == execution_id)
        ).all()
        call_holds = session.scalar(
            select(func.count())
            .select_from(BudgetReservation)
            .where(
                BudgetReservation.task_id == task_id,
                BudgetReservation.execution_id == execution_id,
                BudgetReservation.kind == ReservationKind.CALL.value,
                # A released hold proves no call; settled/open holds do not.
                BudgetReservation.status != ReservationStatus.RELEASED.value,
            )
        )
        has_any = bool(steps) or bool(call_holds)
        if not has_any:
            return CallEvidence(
                has_any=False, has_running_step=False, settled_tokens=None, usage_known=False
            )
        has_running = any(
            row[0] in {StepStatus.RUNNING.value, StepStatus.INTERRUPTED.value} for row in steps
        )
        usage_known = bool(steps) and not has_running and all(row[3] for row in steps)
        settled = None
        if usage_known:
            settled = sum(int(row[1] or 0) + int(row[2] or 0) for row in steps)
        if include_running and has_running:
            usage_known = False
        return CallEvidence(
            has_any=True,
            has_running_step=has_running,
            settled_tokens=settled,
            usage_known=usage_known,
        )

    def _close_envelope(
        self,
        session: Session,
        *,
        task: Task,
        execution: TaskExecution,
        total_known: int | None,
        usage_known: bool,
        detail: str,
        evidence: CallEvidence | None = None,
    ) -> None:
        """Resolve the company/project planning envelope from recorded evidence.

        * no call evidence at all  -> release (nothing could have been spent);
        * known usage              -> settle at the actual amount;
        * anything else            -> consume the envelope and flag it.

        A cancel marker never short-circuits this: cancelling a task whose call
        already started cannot prove the call was free (R5).
        """
        proof = evidence or self._call_evidence(session, task_id=task.id, execution_id=execution.id)
        if not proof.has_any:
            self._budget.release_planning_holds(session, task_id=task.id, detail=detail)
            return
        if usage_known and total_known is not None:
            self._budget.settle_planning_holds(
                session,
                task_id=task.id,
                actual_tokens=total_known,
                known=True,
                detail=detail,
            )
            return
        self._budget.settle_planning_holds(
            session,
            task_id=task.id,
            actual_tokens=None,
            known=False,
            detail=detail,
        )

    def _interrupt_running_steps(self, session: Session, *, execution_id: int) -> None:
        """Close steps the dead worker left RUNNING, keeping them as evidence."""
        steps = session.scalars(
            select(TaskExecutionStep)
            .where(
                TaskExecutionStep.execution_id == execution_id,
                TaskExecutionStep.status == StepStatus.RUNNING.value,
            )
            .with_for_update()
        ).all()
        for step in steps:
            step.status = StepStatus.INTERRUPTED.value
            step.finished_at = utcnow()
            step.usage_known = False
            step.detail = "Worker 进程丢失；该步骤的用量未知。"

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

    # -------------------------------------------------------------- utilities
    def running_executions(self, session: Session) -> Sequence[TaskExecution]:
        return session.scalars(
            select(TaskExecution)
            .where(TaskExecution.status == "RUNNING")
            .order_by(TaskExecution.id)
        ).all()

    def open_reservation_count(self, session: Session, task_id: int) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(BudgetReservation)
                .where(
                    BudgetReservation.task_id == task_id,
                    BudgetReservation.status == ReservationStatus.RESERVED.value,
                )
            )
            or 0
        )

    def account_balance(self, session: Session, account_id: int) -> int:
        account = session.get(BudgetAccount, account_id)
        if account is None:  # pragma: no cover - caller passes a real id
            raise RuntimeError(f"budget account {account_id} missing")
        return account.limit_tokens - account.consumed_tokens - account.reserved_tokens

    def cancelled_attempts(self, session: Session) -> Sequence[TaskExecution]:
        return session.scalars(
            select(TaskExecution).where(
                TaskExecution.status == "RUNNING",
                TaskExecution.cancel_requested_at.is_not(None),
            )
        ).all()


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "AttemptUsage",
    "CallEvidence",
    "ClaimedExecution",
    "ExecutionService",
    "ProcessorOutcome",
]
