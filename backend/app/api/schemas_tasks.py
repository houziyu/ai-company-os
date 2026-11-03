"""Pydantic models for the Phase 03 API surface (projects, tasks, budgets).

Field names on the wire are camelCase (inherited from :class:`ApiModel`).
No field in this module can carry a credential; execution details are sanitized
strings produced by the server.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.api.schemas import ApiModel

TaskStatusLiteral = Literal[
    "QUEUED",
    "RUNNING",
    "WAITING_APPROVAL",
    "BLOCKED",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
]
ExecutionStatusLiteral = Literal[
    "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"
]
StepStatusLiteral = Literal["RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "CANCELLED", "INTERRUPTED"]
RunModeLiteral = Literal["DEMO", "REAL"]
ProjectStatusLiteral = Literal["ACTIVE", "ARCHIVED"]
DemoBehaviorLiteral = Literal[
    "SUCCESS", "TRANSIENT_FAILURE", "PERMANENT_FAILURE", "SLOW", "UNKNOWN_USAGE", "TIMEOUT"
]


# ------------------------------------------------------------------- projects
class ProjectCreateRequest(ApiModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=4000)
    run_mode: RunModeLiteral = "DEMO"
    budget_tokens: int = Field(default=0, ge=0, le=10**12)


class ProjectBudgetUsage(ApiModel):
    """The four numbers the budget page shows, never merged into one."""

    limit_tokens: int
    consumed_tokens: int
    reserved_tokens: int
    available_tokens: int
    needs_reconciliation_count: int = 0


class ProjectSummary(ApiModel):
    id: int
    key: str
    name: str
    objective: str
    status: ProjectStatusLiteral
    run_mode: RunModeLiteral
    version: int
    created_at: datetime
    updated_at: datetime
    task_counts: dict[str, int] = Field(default_factory=dict)
    budget: ProjectBudgetUsage | None = None


class ProjectDetail(ProjectSummary):
    open_tasks: int = 0


class ProjectListResponse(ApiModel):
    projects: list[ProjectSummary]


# ---------------------------------------------------------------------- tasks
class TaskCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=8000)
    acceptance: str | None = Field(default=None, max_length=4000)
    budget_tokens: int = Field(ge=1, le=10**12)
    demo_behavior: DemoBehaviorLiteral = "SUCCESS"
    max_auto_retries: int = Field(default=1, ge=0, le=5)
    assignee_agent_id: int | None = None
    # Retrying the POST with the same key must not create a second task.
    idempotency_key: str = Field(min_length=1, max_length=80)


class TaskCancelRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=200)


class TaskRetryRequest(ApiModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=80)
    expected_attempt: int | None = Field(default=None, ge=0)


class ExecutionStepResponse(ApiModel):
    id: int
    sequence: int
    name: str
    status: StepStatusLiteral
    started_at: datetime
    finished_at: datetime | None
    estimated_tokens: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    usage_known: bool
    detail: str | None


class ExecutionSummary(ApiModel):
    id: int
    task_id: int
    attempt: int
    status: ExecutionStatusLiteral
    run_mode: RunModeLiteral
    demo_behavior: DemoBehaviorLiteral
    worker_id: str | None
    fencing_token: int
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    started_at: datetime
    finished_at: datetime | None
    reserved_tokens: int | None
    settled_tokens: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    usage_known: bool
    needs_reconciliation: bool
    error_code: str | None
    error_message: str | None
    result_summary: str | None
    interrupted_unknown_side_effects: bool
    recovery_note: str | None
    retried_from_execution_id: int | None
    steps: list[ExecutionStepResponse] = Field(default_factory=list)


class TaskEventResponse(ApiModel):
    id: int
    event_type: str
    status_from: str | None
    status_to: str | None
    actor_type: str
    message: str
    detail: str | None
    created_at: datetime


class TaskTaskBudget(ApiModel):
    limit_tokens: int
    consumed_tokens: int
    reserved_tokens: int
    available_tokens: int
    needs_reconciliation_count: int = 0


class TaskSummary(ApiModel):
    id: int
    project_id: int
    project_name: str
    company_id: int
    title: str
    status: TaskStatusLiteral
    run_mode: RunModeLiteral
    demo_behavior: DemoBehaviorLiteral
    budget_tokens: int
    attempt_count: int
    max_auto_retries: int
    assignee_agent_id: int | None
    assignee_name: str | None
    current_execution_id: int | None
    current_execution_status: ExecutionStatusLiteral | None
    last_error_code: str | None
    last_error_message: str | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    budget: TaskTaskBudget | None = None
    # True while a cancel request is pending but the attempt has not stopped.
    cancel_pending: bool = False


class TaskListResponse(ApiModel):
    tasks: list[TaskSummary]
    total: int
    limit: int
    offset: int
    counts: dict[str, int] = Field(default_factory=dict)


class TaskDetail(TaskSummary):
    objective: str
    acceptance: str | None
    result_summary: str | None
    executions: list[ExecutionSummary] = Field(default_factory=list)
    events: list[TaskEventResponse] = Field(default_factory=list)
    idempotency_key: str | None = None


class TaskMutationResponse(ApiModel):
    task: TaskSummary
    created: bool = False
    message: str


# -------------------------------------------------------------------- budgets
class BudgetAccountResponse(ApiModel):
    scope: Literal["COMPANY", "PROJECT", "TASK"]
    scope_id: int
    label: str
    limit_tokens: int
    consumed_tokens: int
    reserved_tokens: int
    available_tokens: int
    needs_reconciliation_count: int


class LedgerEntryResponse(ApiModel):
    id: int
    created_at: datetime
    entry_kind: Literal["RESERVE", "SETTLE", "RELEASE"]
    source: Literal["TASK_CALL", "PROBE"]
    scope: Literal["COMPANY", "PROJECT", "TASK"]
    project_id: int | None
    task_id: int | None
    execution_id: int | None
    reservation_id: int | None
    delta_reserved: int
    delta_consumed: int
    usage_tokens: int | None
    usage_known: bool
    needs_reconciliation: bool
    detail: str | None


class ExecutionLedgerPage(ApiModel):
    entries: list[LedgerEntryResponse]
    next_cursor: int | None
    total_reserved: int
    total_consumed: int
    needs_reconciliation_count: int


class BudgetOverviewResponse(ApiModel):
    company: BudgetAccountResponse
    projects: list[BudgetAccountResponse]
    ledger: list[LedgerEntryResponse]
    # Counted separately because an unknown amount must not be summed into a
    # total that looks complete.
    pending_reconciliation_count: int
    period_start: datetime
    generated_at: datetime


class PendingReconciliationEntry(ApiModel):
    reservation_id: int
    account_scope: Literal["COMPANY", "PROJECT", "TASK"]
    amount_tokens: int
    settled_tokens: int | None
    kind: Literal["PLANNING", "CALL", "PROBE"]
    # ``SETTLED`` = already charged conservatively; ``RESERVED`` = the call has
    # not ended yet. The page must distinguish them, so the state is exposed.
    status: Literal["RESERVED", "SETTLED", "RELEASED"]
    created_at: datetime
    task_id: int | None
    execution_id: int | None
    detail: str | None


# ------------------------------------------------------------------ governance
class CompanyPauseRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=200)


class CompanyStateResponse(ApiModel):
    status: Literal["ACTIVE", "PAUSED"]
    paused_at: datetime | None
    pause_reason: str | None
    running_executions: int
    queued_tasks: int
    message: str


# ----------------------------------------------------------------------- sse
class EventEnvelope(ApiModel):
    id: int
    event_type: str
    created_at: datetime
    project_id: int | None
    task_id: int | None
    execution_id: int | None
    payload: dict[str, Any]


class EventPage(ApiModel):
    events: list[EventEnvelope]
    latest_id: int
    has_more: bool
