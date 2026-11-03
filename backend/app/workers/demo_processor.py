"""Deterministic demo processor.

This is a **rehearsal processor**, not a model and not a delivery. It produces
fixed, reproducible outcomes so the scheduler, budget and recovery paths can be
verified. Every task created against it is labelled ``DEMO`` and the UI shows the
label next to the result; a demo result is never presented as a real model output
and ``REAL`` tasks are refused rather than downgraded.

Behaviours (``Task.demo_behavior``):

============== ==================================================================
SUCCESS        two steps, both report usage; task succeeds
TRANSIENT      first step fails once, retryable; a later attempt succeeds
PERMANENT      a step fails and is explicitly *not* retryable
SLOW           step sleeps, so a long-running attempt can be observed/cancelled
UNKNOWN_USAGE  step succeeds but the provider-like source reports no usage, which
               must consume the hold and be flagged for reconciliation
TIMEOUT        step reports a timeout with no usage (unknown, not zero)
============== ==================================================================

Stopping
--------
``start_step`` is the single server-side gate: it refuses to open a step when the
company is paused or a cancel was requested, and it rejects an expired lease. The
processor does not duplicate those checks — it reports the refusal it received.
An attempt that stops before its next step therefore records *no* new step and
*no* new hold, and the task keeps a state a human can act on.

The processor is idempotent per step (it only writes step rows), which is what
allows the recovery path to re-queue automatically. Any processor with real
external side effects must declare ``retryable=False`` instead.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import DemoBehavior, ExecutionStopReason, StepStatus, TaskErrorCode
from app.domain.errors import BudgetInsufficientError, ExecutionStoppedError, LeaseLostError
from app.models.tasks import Task
from app.services.execution_service import (
    ClaimedExecution,
    ExecutionService,
    ProcessorOutcome,
)

logger = logging.getLogger(__name__)

# Token numbers are fixed so budget assertions are exact, not approximate.
STEP1_PROMPT = 120
STEP1_COMPLETION = 80
STEP2_PROMPT = 200
STEP2_COMPLETION = 100
TOTAL_KNOWN_TOKENS = STEP1_PROMPT + STEP1_COMPLETION + STEP2_PROMPT + STEP2_COMPLETION

DEMO_RUN_LABEL = "演练运行（确定性处理器，非真实模型输出）"

# Sleep lengths are short: the acceptance measures that a long attempt can be
# cancelled, and a fixed short duration is more reliable than a race against a
# timeout. The worker passes the configured value so an operator can lengthen it.
SLOW_STEP_SECONDS = 1.5


@dataclass(slots=True)
class DemoStep:
    sequence: int
    name: str
    prompt: int | None
    completion: int | None
    known: bool
    fail: bool = False
    retryable: bool = False
    sleep_seconds: float = 0.0


def _steps_for(behavior: str, *, slow_step_seconds: float = SLOW_STEP_SECONDS) -> list[DemoStep]:
    if behavior == DemoBehavior.SUCCESS.value:
        return [
            DemoStep(0, "准备执行上下文", STEP1_PROMPT, STEP1_COMPLETION, True),
            DemoStep(1, "生成演练结果", STEP2_PROMPT, STEP2_COMPLETION, True),
        ]
    if behavior == DemoBehavior.TRANSIENT_FAILURE.value:
        return [
            DemoStep(0, "准备执行上下文", STEP1_PROMPT, STEP1_COMPLETION, True),
            DemoStep(
                1,
                "生成演练结果",
                None,
                None,
                False,
                fail=True,
                retryable=True,
            ),
        ]
    if behavior == DemoBehavior.PERMANENT_FAILURE.value:
        return [
            DemoStep(0, "准备执行上下文", STEP1_PROMPT, STEP1_COMPLETION, True),
            DemoStep(
                1,
                "生成演练结果",
                None,
                None,
                False,
                fail=True,
                retryable=False,
            ),
        ]
    if behavior == DemoBehavior.SLOW.value:
        return [
            DemoStep(0, "准备执行上下文", STEP1_PROMPT, STEP1_COMPLETION, True),
            DemoStep(
                1,
                "生成演练结果",
                STEP2_PROMPT,
                STEP2_COMPLETION,
                True,
                sleep_seconds=slow_step_seconds,
            ),
        ]
    if behavior == DemoBehavior.UNKNOWN_USAGE.value:
        return [
            DemoStep(0, "准备执行上下文", None, None, False),
            DemoStep(1, "生成演练结果", None, None, False),
        ]
    if behavior == DemoBehavior.TIMEOUT.value:
        return [
            DemoStep(0, "准备执行上下文", STEP1_PROMPT, STEP1_COMPLETION, True, sleep_seconds=0.1),
            DemoStep(1, "生成演练结果", None, None, False, fail=True, retryable=False),
        ]
    # Unknown behaviour values are a programming error, not a silent success.
    raise ValueError(f"unknown demo behavior {behavior!r}")


class DemoProcessor:
    """Runs one claimed attempt, writing each step through the fenced service."""

    label = DEMO_RUN_LABEL

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        execution_service: ExecutionService | None = None,
        sleep_scale: float = 1.0,
        slow_step_seconds: float = SLOW_STEP_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._executions = execution_service or ExecutionService()
        self._sleep_scale = sleep_scale
        self._slow_step_seconds = slow_step_seconds

    def is_idempotent(self) -> bool:
        """The demo processor only writes step rows, so replay is safe."""
        return True

    def process(self, claim: ClaimedExecution, *, lease_seconds: float) -> ProcessorOutcome:
        """Execute the attempt. Each step is committed in its own transaction."""
        behavior = claim.demo_behavior
        steps = _steps_for(behavior, slow_step_seconds=self._slow_step_seconds)
        prompt_total = 0
        completion_total = 0
        usage_known = True

        for spec in steps:
            if spec.sleep_seconds:
                time.sleep(spec.sleep_seconds * self._sleep_scale)

            # Step start + its hold are one transaction. The service applies the
            # stop gate (pause / cancel / lease / allowance) before anything is
            # written, so a refusal here means no step and no hold exist.
            with self._session_factory() as session:
                try:
                    step = self._executions.start_step(
                        session,
                        execution_id=claim.execution_id,
                        fencing_token=claim.fencing_token,
                        sequence=spec.sequence,
                        name=spec.name,
                        estimated_tokens=(spec.prompt or 0) + (spec.completion or 0) or 64,
                    )
                    session.commit()
                except ExecutionStoppedError as exc:
                    session.rollback()
                    return self._stopped_outcome(exc)
                except LeaseLostError:
                    session.rollback()
                    logger.warning(
                        "demo step start rejected: lease lost",
                        extra={"execution_id": claim.execution_id},
                    )
                    return ProcessorOutcome(
                        succeeded=False,
                        summary=None,
                        prompt_tokens=None,
                        completion_tokens=None,
                        usage_known=False,
                        error_code=TaskErrorCode.LEASE_LOST.value,
                        error_message="执行已失去租约，未继续启动步骤。",
                        retryable=False,
                    )
                except BudgetInsufficientError as exc:
                    session.rollback()
                    # No call started for this step; the attempt stops where it is
                    # and the task is blocked for a human, not silently failed.
                    return ProcessorOutcome(
                        succeeded=False,
                        summary=None,
                        prompt_tokens=None,
                        completion_tokens=None,
                        usage_known=False,
                        error_code=TaskErrorCode.BUDGET_INSUFFICIENT.value,
                        error_message=exc.message,
                        retryable=False,
                        blocked=True,
                    )
                except Exception as exc:
                    session.rollback()
                    logger.warning("demo step start failed", extra={"error": type(exc).__name__})
                    return ProcessorOutcome(
                        succeeded=False,
                        summary=None,
                        prompt_tokens=None,
                        completion_tokens=None,
                        usage_known=False,
                        error_code=TaskErrorCode.PROCESSOR_ERROR.value,
                        error_message="步骤未能启动，执行已停止。",
                        retryable=False,
                    )

            if spec.fail:
                with self._session_factory() as session:
                    cancelled = self._cancel_requested(session, claim)
                    status = StepStatus.CANCELLED if cancelled else StepStatus.FAILED
                    self._executions.finish_step(
                        session,
                        execution_id=claim.execution_id,
                        fencing_token=claim.fencing_token,
                        step_id=step.id,
                        status=status,
                        prompt_tokens=spec.prompt,
                        completion_tokens=spec.completion,
                        usage_known=spec.known,
                        detail="演练：模拟失败",
                    )
                    session.commit()
                if cancelled:
                    return ProcessorOutcome(
                        succeeded=False,
                        summary=None,
                        prompt_tokens=prompt_total or None,
                        completion_tokens=completion_total or None,
                        usage_known=usage_known,
                        error_code=TaskErrorCode.CANCELLED_BY_OWNER.value,
                        error_message="执行在安全检查点停止。",
                        cancelled=True,
                    )
                # The failed step itself may have produced no usage report: that
                # unknown amount must not be dropped from the attempt summary.
                # ``complete`` re-derives usage from the step rows, and this value
                # keeps the processor's own report honest as well.
                step_unknown = not spec.known
                if behavior == DemoBehavior.TIMEOUT.value:
                    code = TaskErrorCode.SIMULATED_TIMEOUT.value
                    message = "演练：模拟调用超时，用量未知。"
                elif spec.retryable:
                    code = TaskErrorCode.SIMULATED_TRANSIENT.value
                    message = "演练：模拟临时失败（可重试）。"
                else:
                    code = TaskErrorCode.SIMULATED_PERMANENT.value
                    message = "演练：模拟永久失败（不自动重试）。"
                return ProcessorOutcome(
                    succeeded=False,
                    summary=None,
                    prompt_tokens=(
                        None if (step_unknown or not usage_known) else (prompt_total or None)
                    ),
                    completion_tokens=(
                        None if (step_unknown or not usage_known) else (completion_total or None)
                    ),
                    usage_known=usage_known and not step_unknown,
                    error_code=code,
                    error_message=message,
                    retryable=spec.retryable,
                )

            # A slow step is interruptible: check the cancel marker before closing.
            if spec.sleep_seconds:
                with self._session_factory() as session:
                    cancelled = self._cancel_requested(session, claim)
                    self._executions.finish_step(
                        session,
                        execution_id=claim.execution_id,
                        fencing_token=claim.fencing_token,
                        step_id=step.id,
                        status=StepStatus.CANCELLED if cancelled else StepStatus.SUCCEEDED,
                        prompt_tokens=spec.prompt,
                        completion_tokens=spec.completion,
                        usage_known=spec.known,
                        detail="演练：安全检查点",
                    )
                    session.commit()
                if cancelled:
                    return ProcessorOutcome(
                        succeeded=False,
                        summary=None,
                        prompt_tokens=spec.prompt,
                        completion_tokens=spec.completion,
                        usage_known=spec.known,
                        error_code=TaskErrorCode.CANCELLED_BY_OWNER.value,
                        error_message="执行在安全检查点停止。",
                        cancelled=True,
                    )
            else:
                with self._session_factory() as session:
                    self._executions.finish_step(
                        session,
                        execution_id=claim.execution_id,
                        fencing_token=claim.fencing_token,
                        step_id=step.id,
                        status=StepStatus.SUCCEEDED,
                        prompt_tokens=spec.prompt,
                        completion_tokens=spec.completion,
                        usage_known=spec.known,
                        detail="演练：步骤完成",
                    )
                    session.commit()

            if spec.prompt is not None:
                prompt_total += spec.prompt
            if spec.completion is not None:
                completion_total += spec.completion
            if not spec.known:
                usage_known = False

        if behavior == DemoBehavior.UNKNOWN_USAGE.value:
            return ProcessorOutcome(
                succeeded=True,
                summary=f"{DEMO_RUN_LABEL}：完成，但供应商未返回用量。",
                prompt_tokens=None,
                completion_tokens=None,
                usage_known=False,
            )

        return ProcessorOutcome(
            succeeded=True,
            summary=f"{DEMO_RUN_LABEL}：完成 2 个步骤，结果可复核。",
            prompt_tokens=prompt_total or None,
            completion_tokens=completion_total or None,
            usage_known=usage_known,
        )

    def _stopped_outcome(self, error: ExecutionStoppedError) -> ProcessorOutcome:
        """Report a stop gate as a stop, not as a failure of the work."""
        if error.reason == ExecutionStopReason.CANCELLED.value:
            return ProcessorOutcome(
                succeeded=False,
                summary=None,
                prompt_tokens=None,
                completion_tokens=None,
                usage_known=False,
                error_code=TaskErrorCode.CANCELLED_BY_OWNER.value,
                error_message="取消已请求，执行在安全检查点停止；未启动新的调用。",
                cancelled=True,
                stopped_reason=ExecutionStopReason.CANCELLED.value,
            )
        return ProcessorOutcome(
            succeeded=False,
            summary=None,
            prompt_tokens=None,
            completion_tokens=None,
            usage_known=False,
            error_code=TaskErrorCode.COMPANY_PAUSED.value,
            error_message="公司已暂停，执行在安全检查点停止；未启动新的调用。",
            retryable=False,
            stopped_reason=ExecutionStopReason.COMPANY_PAUSED.value,
        )

    def _cancel_requested(self, session: Session, claim: ClaimedExecution) -> bool:
        task = session.get(Task, claim.task_id)
        return bool(task and task.cancel_requested_at is not None)
