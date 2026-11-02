"""Pydantic request/response models for the Phase 02 API surface.

Everything a response can contain is deliberately non-secret. There is no field
anywhere in this module that can carry a credential value: providers expose a
*reference name* plus a boolean ``secret_configured``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.api.schemas import ApiModel

AgentStatusLiteral = Literal["ACTIVE", "PAUSED", "ARCHIVED"]
ProviderKindLiteral = Literal["DEEPSEEK", "OPENAI_COMPATIBLE"]
EnablementLiteral = Literal["ENABLED", "DISABLED"]
ProbeOutcomeLiteral = Literal["PENDING", "SUCCESS", "FAILURE"]


# ------------------------------------------------------------------- company
class CompanyInitRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=2000)
    template_key: str = Field(default="tech-company", max_length=64)
    template_version: int = Field(default=1, ge=1, le=100)
    # Optional model binding applied to all five employees at creation.
    model_id: int | None = None
    # Retrying the wizard with the same key must not create a second company.
    idempotency_key: str | None = Field(default=None, max_length=80)


class CompanyUpdateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=2000)
    # Optimistic concurrency: the client echoes the version it edited.
    expected_version: int = Field(ge=1)


class CompanyResponse(ApiModel):
    id: int
    name: str
    goal: str
    status: Literal["ACTIVE"]
    template_key: str
    template_version: int
    version: int
    initialized_at: datetime
    position_count: int
    agent_count: int


class TemplatePositionInfo(ApiModel):
    code: str
    title: str
    default_agent_name: str
    duty: str
    reports_to_code: str | None
    sort_order: int


class CompanyTemplateResponse(ApiModel):
    template_key: str
    template_version: int
    label: str
    description: str
    positions: list[TemplatePositionInfo]


# -------------------------------------------------------------- organization
class PositionSummary(ApiModel):
    id: int
    code: str
    title: str
    duty: str
    # ``version`` is the duty snapshot number (1-based); ``row_version`` is the
    # optimistic-locking counter the client echoes when editing.
    version: int
    row_version: int
    sort_order: int
    department_name: str


class AgentModelRef(ApiModel):
    model_id: int
    logical_name: str
    label: str
    provider_id: int
    provider_name: str
    provider_kind: str
    # Null until a probe verified it; never guessed from provider kind.
    supports_json_output: bool | None


class AgentSummary(ApiModel):
    id: int
    employee_code: str
    display_name: str
    status: AgentStatusLiteral
    position_id: int
    position_code: str
    position_title: str
    manager_id: int | None
    manager_name: str | None
    duty_override: str | None
    effective_duty: str
    model: AgentModelRef | None
    version: int
    status_changed_at: datetime
    archived_at: datetime | None


class OrganizationResponse(ApiModel):
    company: CompanyResponse
    department_name: str
    department_purpose: str
    positions: list[PositionSummary]
    agents: list[AgentSummary]


class PositionUpdateRequest(ApiModel):
    title: str | None = Field(default=None, max_length=80)
    duty: str | None = Field(default=None, max_length=4000)
    expected_version: int | None = Field(default=None, ge=1)


class PositionUpdateResponse(ApiModel):
    position: PositionSummary
    version_created: bool


class AgentUpdateRequest(ApiModel):
    display_name: str | None = Field(default=None, max_length=80)
    duty_override: str | None = Field(default=None, max_length=4000)
    clear_duty_override: bool = False
    # ``change_manager`` distinguishes "leave unchanged" (null default) from
    # "make this employee report to nobody" (true + managerId null).
    manager_id: int | None = None
    change_manager: bool = False
    status: AgentStatusLiteral | None = None
    expected_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=200)


class AgentAssignmentRequest(ApiModel):
    model_id: int
    note: str | None = Field(default=None, max_length=200)


class AgentHistoryEntry(ApiModel):
    kind: Literal["DUTY_OVERRIDE", "MODEL_ASSIGNMENT"]
    occurred_at: datetime
    summary: str
    detail: str | None


class AgentHistoryResponse(ApiModel):
    agent_id: int
    entries: list[AgentHistoryEntry]


# ------------------------------------------------------------- model centre
class ProviderResponse(ApiModel):
    id: int
    name: str
    kind: ProviderKindLiteral
    version: int
    base_url: str
    # Environment variable *name*. Never a value.
    secret_ref: str | None
    secret_configured: bool
    status: EnablementLiteral
    notes: str | None
    timeout_seconds: float
    model_count: int
    created_at: datetime
    updated_at: datetime


class ProviderCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    kind: ProviderKindLiteral = "OPENAI_COMPATIBLE"
    base_url: str = Field(min_length=1, max_length=300)
    secret_ref: str | None = Field(default=None, max_length=128)
    status: EnablementLiteral = "ENABLED"
    notes: str | None = Field(default=None, max_length=500)
    timeout_seconds: float | None = Field(default=None, ge=1, le=120)


class ProviderUpdateRequest(ApiModel):
    # Required: the version the client read. A write that cannot say which version
    # it was based on cannot be proven fresh, and a stale form must never silently
    # overwrite a newer edit (review finding R7). A mismatch is 409.
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: ProviderKindLiteral | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=300)
    secret_ref: str | None = Field(default=None, max_length=128)
    # Explicit flag: null secretRef would otherwise be ambiguous between
    # "clear the reference" and "leave it as it was".
    change_secret_ref: bool = False
    status: EnablementLiteral | None = None
    notes: str | None = Field(default=None, max_length=500)
    timeout_seconds: float | None = Field(default=None, ge=1, le=120)


class ModelResponse(ApiModel):
    id: int
    provider_id: int
    provider_name: str
    # Provider facts the UI needs to explain why a model is unusable without a
    # second request. Never contains a credential value.
    provider_status: EnablementLiteral
    provider_secret_configured: bool
    version: int
    logical_name: str
    label: str
    status: EnablementLiteral
    max_output_tokens: int | None
    supports_json_output: bool | None
    # True when the capability fields above were learned under a configuration
    # that has since changed; the UI then says "未验证" instead of showing stale
    # evidence (review finding R3).
    probe_evidence_stale: bool
    resolved_name: str | None
    last_probe_outcome: ProbeOutcomeLiteral | None
    last_probe_at: datetime | None
    last_probe_detail: str | None
    created_at: datetime
    updated_at: datetime


class ModelCreateRequest(ApiModel):
    provider_id: int
    logical_name: str = Field(min_length=1, max_length=160)
    label: str | None = Field(default=None, max_length=160)
    status: EnablementLiteral = "ENABLED"
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


class ModelUpdateRequest(ApiModel):
    # Required for the same reason as the provider form (review finding R7).
    expected_version: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=160)
    status: EnablementLiteral | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    change_max_output_tokens: bool = False


class ProbeResponse(ApiModel):
    id: int
    model_id: int
    # ``PENDING`` means the call was attempted but no result was recorded: the
    # process stopped mid-call. Read it as "结果与用量未知"; it is never retried
    # automatically.
    outcome: Literal["PENDING", "SUCCESS", "FAILURE"]
    error_code: str | None
    message: str
    started_at: datetime
    finished_at: datetime | None
    latency_ms: int | None
    json_output_verified: bool | None
    resolved_model_name: str | None
    call_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    usage_known: bool
    # Fixed warning shown before and after a probe; a probe costs tokens.
    token_warning: str


class ProbeHistoryEntry(ApiModel):
    id: int
    model_id: int
    outcome: Literal["PENDING", "SUCCESS", "FAILURE"]
    error_code: str | None
    detail: str | None
    started_at: datetime
    latency_ms: int | None
    call_count: int
    json_output_verified: bool | None
    usage_known: bool


class UsageSummary(ApiModel):
    """Probe spend so far, with per-dimension completeness.

    A dimension the provider never reported stays ``null``, never 0. When some
    calls reported a dimension and others did not, the total covers only the
    reporting calls and ``*_unreported_calls`` says how many are missing from it
    (review finding R4).
    """

    probe_calls: int
    measured_calls: int
    partially_measured_calls: int
    unmeasured_calls: int
    prompt_tokens: int | None
    completion_tokens: int | None
    prompt_tokens_unreported_calls: int
    completion_tokens_unreported_calls: int
    # Attempts committed but never completed: result and usage unknown.
    pending_probes: int


class ModelCentreResponse(ApiModel):
    providers: list[ProviderResponse]
    models: list[ModelResponse]
    assignments: list[AgentModelBinding]
    recent_probes: list[ProbeHistoryEntry]
    usage: UsageSummary
    deepseek_preset: DeepSeekPreset


class DeepSeekPreset(ApiModel):
    kind: ProviderKindLiteral
    base_url: str
    secret_ref: str
    suggested_model: str
    notes: str


class ModelUnbindRequest(ApiModel):
    """Explicit unbind request; ends the current binding, keeps its history."""

    note: str | None = Field(default=None, max_length=200)


class AgentModelBinding(ApiModel):
    agent_id: int
    employee_code: str
    display_name: str
    agent_status: AgentStatusLiteral
    position_title: str
    model_id: int | None
    model_label: str | None
    provider_name: str | None
    provider_kind: str | None
    model_status: EnablementLiteral | None
    provider_status: EnablementLiteral | None
    secret_configured: bool | None
    json_verified: bool | None
    assigned_at: datetime | None


# --------------------------------------------------------------------- audit
class AuditEventResponse(ApiModel):
    id: int
    occurred_at: datetime
    actor_type: str
    actor_user_id: int | None
    actor_display_name: str | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    detail: str | None
    request_id: str | None


class AuditListResponse(ApiModel):
    events: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


# Forward-ref resolution for the nested preset used above.
ModelCentreResponse.model_rebuild()
