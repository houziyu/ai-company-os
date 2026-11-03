"""Shared enumerations.

Values are persisted in the database, so they must not be renamed without a
migration. The frontend mirrors these in ``frontend/src/api/types.ts``.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "OWNER"


class SessionRevokeReason(StrEnum):
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"  # noqa: S105 - audit reason code, not a password
    ADMINISTRATIVE = "ADMINISTRATIVE"
    EXPIRED = "EXPIRED"


class AuditActorType(StrEnum):
    ANONYMOUS = "ANONYMOUS"
    OWNER = "OWNER"
    SYSTEM = "SYSTEM"


class AuditAction(StrEnum):
    # Phase 01
    OWNER_SETUP_COMPLETED = "OWNER_SETUP_COMPLETED"
    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_BLOCKED = "LOGIN_BLOCKED"
    LOGOUT = "LOGOUT"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    # Phase 02 - company and organization
    COMPANY_INITIALIZED = "COMPANY_INITIALIZED"
    COMPANY_UPDATED = "COMPANY_UPDATED"
    POSITION_UPDATED = "POSITION_UPDATED"
    AGENT_UPDATED = "AGENT_UPDATED"
    AGENT_STATUS_CHANGED = "AGENT_STATUS_CHANGED"
    AGENT_MODEL_ASSIGNED = "AGENT_MODEL_ASSIGNED"
    # Phase 02 - model center
    PROVIDER_CREATED = "PROVIDER_CREATED"
    PROVIDER_UPDATED = "PROVIDER_UPDATED"
    PROVIDER_MODEL_CREATED = "PROVIDER_MODEL_CREATED"
    PROVIDER_MODEL_UPDATED = "PROVIDER_MODEL_UPDATED"
    MODEL_PROBE_COMPLETED = "MODEL_PROBE_COMPLETED"
    MODEL_PROBE_FAILED = "MODEL_PROBE_FAILED"
    # Phase 03 - projects, tasks, budget governance
    PROJECT_CREATED = "PROJECT_CREATED"
    TASK_CREATED = "TASK_CREATED"
    TASK_CANCEL_REQUESTED = "TASK_CANCEL_REQUESTED"
    TASK_RETRIED = "TASK_RETRIED"
    COMPANY_PAUSED = "COMPANY_PAUSED"
    COMPANY_RESUMED = "COMPANY_RESUMED"


class CompanyStatus(StrEnum):
    """Company lifecycle.

    ``PAUSED`` is a Phase 03 operational state: it stops *new* paid calls and new
    task creation. It is not a lifecycle end state, and it never rewrites history.
    """

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class AgentStatus(StrEnum):
    """Employee instance state (COMPONENTS.md §5).

    Identity is never deleted: PAUSED stops new work, ARCHIVED marks the end of
    employment while keeping the record and its history.
    """

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class ProviderKind(StrEnum):
    DEEPSEEK = "DEEPSEEK"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class ProviderStatus(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class ProbeOutcome(StrEnum):
    # PENDING means the intent was committed but no result was recorded: the
    # process died or the transaction failed mid-call. It is read as "结果与用量
    # 未知" and never automatically retried, so a possibly-paid request is never
    # silently repeated.
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ProbeErrorCode(StrEnum):
    """Stable, publishable failure categories.

    Provider response bodies never reach the browser or the audit log; only
    these codes and a sanitized short detail do.
    """

    MISSING_SECRET = "MISSING_SECRET"  # noqa: S105 - error category, not a secret
    # A stored credential exists but this server cannot decrypt it (the
    # encryption key changed or was lost). Separate from MISSING_SECRET so the
    # operator is told to re-enter the key, not to set an environment variable.
    SECRET_UNDECRYPTABLE = "SECRET_UNDECRYPTABLE"  # noqa: S105 - error category, not a secret
    INVALID_CONFIG = "INVALID_CONFIG"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    INVALID_JSON_OUTPUT = "INVALID_JSON_OUTPUT"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"
    MODEL_DISABLED = "MODEL_DISABLED"
    # Phase 03: a probe is a paid call, so the same gates apply as for tasks.
    COMPANY_PAUSED = "COMPANY_PAUSED"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"


class UsageSource(StrEnum):
    """Where a provider usage record came from."""

    PROBE = "PROBE"


# ------------------------------------------------------------------ phase 03
class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class RunMode(StrEnum):
    """How a project's tasks are executed.

    Phase 03 implements the deterministic demo processor only. ``REAL`` is a
    declared value that the server refuses explicitly; it must never be silently
    downgraded to a simulated run.
    """

    DEMO = "DEMO"
    REAL = "REAL"


class TaskStatus(StrEnum):
    """Business state of a task (PAGES.md §9 status matrix).

    Execution state is a separate fact stored on ``task_executions``; a task is
    never "succeeded" because a process exited, only because the server recorded a
    verified result.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"  # reserved for Phase 06
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class StepStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class BudgetScope(StrEnum):
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"
    TASK = "TASK"


class ReservationKind(StrEnum):
    """Why tokens were held.

    ``PLANNING`` holds a task's whole envelope at the company and project level;
    ``CALL`` holds one step's conservative estimate at the task level; ``PROBE``
    holds one connectivity probe's estimate at the company level.
    """

    PLANNING = "PLANNING"
    CALL = "CALL"
    PROBE = "PROBE"


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"


class LedgerSource(StrEnum):
    TASK_CALL = "TASK_CALL"
    PROBE = "PROBE"


class EventActorType(StrEnum):
    OWNER = "OWNER"
    SYSTEM = "SYSTEM"
    WORKER = "WORKER"


class MessagePartyType(StrEnum):
    OWNER = "OWNER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class DemoBehavior(StrEnum):
    """Deterministic behaviour of the labelled demo processor.

    These are test/rehearsal outcomes, never a real model capability claim.
    """

    SUCCESS = "SUCCESS"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    SLOW = "SLOW"
    UNKNOWN_USAGE = "UNKNOWN_USAGE"
    TIMEOUT = "TIMEOUT"


class TaskErrorCode(StrEnum):
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    COMPANY_PAUSED = "COMPANY_PAUSED"
    WORKER_LOST = "WORKER_LOST"
    LEASE_LOST = "LEASE_LOST"
    SIMULATED_TRANSIENT = "SIMULATED_TRANSIENT"
    SIMULATED_PERMANENT = "SIMULATED_PERMANENT"
    SIMULATED_TIMEOUT = "SIMULATED_TIMEOUT"
    PROCESSOR_ERROR = "PROCESSOR_ERROR"
    CANCELLED_BY_OWNER = "CANCELLED_BY_OWNER"


class ExecutionStopReason(StrEnum):
    """Why a step was refused before it could start.

    Both are *stop gates*, not failures of the work itself: no call happened, so
    the attempt ends with the task in a state a human can act on.
    """

    CANCELLED = "CANCELLED"
    COMPANY_PAUSED = "COMPANY_PAUSED"


class DomainEventType(StrEnum):
    """Event types streamed to the browser (SSE) and replayable by id."""

    COMPANY_PAUSED = "COMPANY_PAUSED"
    COMPANY_RESUMED = "COMPANY_RESUMED"
    PROJECT_CREATED = "PROJECT_CREATED"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_STEP_UPDATED = "TASK_STEP_UPDATED"
    TASK_CANCEL_REQUESTED = "TASK_CANCEL_REQUESTED"
    TASK_RETRY_SCHEDULED = "TASK_RETRY_SCHEDULED"
    TASK_SUCCEEDED = "TASK_SUCCEEDED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_INTERRUPTED = "TASK_INTERRUPTED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_REQUEUED = "TASK_REQUEUED"


class SecretSource(StrEnum):
    """How a provider credential is supplied.

    ``ENV`` is the Phase 02 form: the database stores only the *name* of a
    server environment variable. ``DB`` is the ADR-0006 form: the operator types
    the key in the UI and the server stores it AES-GCM encrypted in
    ``providers.secret_ciphertext``. Both may coexist; a row has at most one.
    """

    ENV = "ENV"
    DB = "DB"


class SecretState(StrEnum):
    """What the server can currently say about a provider's credential.

    Reported instead of the credential itself so the UI can distinguish "no key
    configured" from "a key is stored but this server cannot read it" — the
    latter happens after the encryption key is replaced or lost, and pretending
    it is simply missing would send the operator hunting for the wrong fix.
    """

    NONE = "NONE"
    CONFIGURED = "CONFIGURED"
    MISSING_ENV = "MISSING_ENV"
    UNDECRYPTABLE = "UNDECRYPTABLE"
