"""Pydantic request/response models for the v1 API.

Field names on the wire are camelCase to match TypeScript conventions; internal
code stays snake_case.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    head, *rest = value.split("_")
    return head + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class FieldErrorModel(ApiModel):
    field: str
    message: str


class ErrorResponse(ApiModel):
    code: str
    message: str
    request_id: str
    field_errors: list[FieldErrorModel] = Field(default_factory=list)
    details: dict[str, object] | None = None


# --------------------------------------------------------------------- setup
class SetupStatus(ApiModel):
    setup_complete: bool
    app_version: str
    password_min_length: int
    # Company initialization is a separate step after owner setup. The SPA uses
    # this to route an authenticated owner to the company wizard exactly once.
    company_initialized: bool
    company_name: str | None = None


class SetupRequest(ApiModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    password_confirm: str = Field(min_length=1, max_length=256)


# --------------------------------------------------------------------- login
class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class OwnerProfile(ApiModel):
    id: int
    username: str
    display_name: str
    role: str


class SessionResponse(ApiModel):
    user: OwnerProfile
    expires_at: datetime
    csrf_token: str


class CurrentSessionResponse(ApiModel):
    user: OwnerProfile
    expires_at: datetime
    session_created_at: datetime


# -------------------------------------------------------------------- health
class HealthCheck(ApiModel):
    name: str
    status: Literal["ok", "fail", "skip"]
    detail: str | None = None


class LivenessResponse(ApiModel):
    status: Literal["ok"]
    service: str
    version: str


class ReadinessResponse(ApiModel):
    ready: bool
    service: str
    version: str
    checks: list[HealthCheck]
    evaluated_at: datetime


class ServiceStatus(ApiModel):
    """Non-secret deployment facts shown on the settings page."""

    app_version: str
    environment: str
    database_status: Literal["ok", "fail", "unknown"]
    database_revision: str | None = None
    expected_revision: str | None = None
    worker_last_heartbeat_at: datetime | None = None
    worker_status: Literal["ok", "stale", "missing", "unknown"]
    worker_version: str | None = None
    session_ttl_hours: int
    started_at: datetime
