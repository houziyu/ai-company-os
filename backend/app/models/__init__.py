"""Import every model so ``Base.metadata`` is complete for Alembic."""

from app.models.entities import (
    AuditEvent,
    AuthSession,
    LoginAttempt,
    PlatformState,
    User,
    WorkerHeartbeat,
)
from app.models.organization import (
    Agent,
    AgentDutyOverrideHistory,
    AgentModelAssignment,
    Company,
    CompanyTemplatePosition,
    Department,
    Model,
    ModelProbe,
    Position,
    PositionVersion,
    Provider,
    ProviderUsageRecord,
)

__all__ = [
    "Agent",
    "AgentDutyOverrideHistory",
    "AgentModelAssignment",
    "AuditEvent",
    "AuthSession",
    "Company",
    "CompanyTemplatePosition",
    "Department",
    "LoginAttempt",
    "Model",
    "ModelProbe",
    "PlatformState",
    "Position",
    "PositionVersion",
    "Provider",
    "ProviderUsageRecord",
    "User",
    "WorkerHeartbeat",
]
