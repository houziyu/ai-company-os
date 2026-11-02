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


class CompanyStatus(StrEnum):
    """Company lifecycle. Phase 02 only creates ACTIVE companies."""

    ACTIVE = "ACTIVE"


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


class UsageSource(StrEnum):
    """Where a usage record came from. Phase 03 adds task executions."""

    PROBE = "PROBE"


class SecretSource(StrEnum):
    """How a provider credential is supplied.

    Phase 02 reads secrets from the server process environment only. The
    database stores the *name* of the variable, never a value.
    """

    ENV = "ENV"
