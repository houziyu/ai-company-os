"""Model centre routes: providers, models, bindings and connectivity probes.

Two rules shape this module:

1. **No credential is ever read back.** A request may carry a literal
   ``secretValue`` (stored AES-GCM encrypted, ADR-0006) or the legacy
   ``secretRef`` environment-variable name. A response carries the reference
   name, the source, the state (``NONE / CONFIGURED / MISSING_ENV /
   UNDECRYPTABLE``) and nothing derived from the key. There is no
   read-secret endpoint.
2. **Saving and probing are separate commands.** ``PATCH``/``POST`` on providers
   and models never touch the network; ``POST /models/{id}/probe`` performs
   exactly one call against already-persisted settings and records its own
   usage row.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.context import client_ip, get_request_id
from app.api.deps import AuthenticatedDep, DbSession, WriteGuard
from app.api.schemas_organization import (
    AgentModelBinding,
    DeepSeekPreset,
    ModelCentreResponse,
    ModelCreateRequest,
    ModelResponse,
    ModelUpdateRequest,
    ProbeHistoryEntry,
    ProbeResponse,
    ProviderCreateRequest,
    ProviderResponse,
    ProviderUpdateRequest,
    UsageSummary,
)
from app.domain.enums import SecretState
from app.domain.model_evidence import evidence_is_stale, json_capability
from app.models.organization import Model, ModelProbe, Provider
from app.repositories.organization_repository import (
    AgentRepository,
    AssignmentRepository,
    CompanyRepository,
    PositionRepository,
    ProviderRepository,
)
from app.services.model_centre_service import ModelCentreService

router = APIRouter(tags=["models"])

# Shown before and after every probe: the call costs tokens and is never retried.
# Named "cost notice" rather than "token warning" so secret scanners do not read it
# as a credential (this string is user-facing copy, not a key).
PROBE_COST_NOTICE = "测试连接会向供应商发送一次请求，可能消耗少量 Token，不会自动重试。"


def _service(request: Request) -> ModelCentreService:
    service: ModelCentreService | None = getattr(request.app.state, "model_centre_service", None)
    if service is None:  # pragma: no cover - the app factory always installs one
        service = ModelCentreService()
        request.app.state.model_centre_service = service
    return service


def _provider_response(
    provider: Provider, *, model_count: int, secret_state: SecretState
) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,  # type: ignore[arg-type]
        version=provider.version,
        base_url=provider.base_url,
        secret_ref=provider.secret_ref,
        secret_source=provider.secret_source,  # type: ignore[arg-type]
        secret_state=secret_state.value,  # type: ignore[arg-type]
        # "Configured" means usable right now, not merely present. A stored key
        # the server cannot decrypt is reported as UNDECRYPTABLE and is not
        # silently rounded to "not configured" — the fix is different.
        secret_configured=secret_state is SecretState.CONFIGURED,
        status=provider.status,  # type: ignore[arg-type]
        notes=provider.notes,
        timeout_seconds=float(provider.timeout_seconds),
        model_count=model_count,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _model_response(
    model: Model,
    provider: Provider | None,
    *,
    secret_configured: bool = False,
) -> ModelResponse:
    # Evidence belongs to the configuration that produced it. When the provider's
    # fingerprint has moved on, the stored capability no longer describes the
    # current setup, so it is reported as stale rather than as verified.
    stale = evidence_is_stale(model, provider)
    return ModelResponse(
        id=model.id,
        provider_id=model.provider_id,
        provider_name=provider.name if provider else "未知供应商",
        provider_status=provider.status if provider else "DISABLED",  # type: ignore[arg-type]
        provider_secret_configured=secret_configured,
        version=model.version,
        logical_name=model.logical_name,
        label=model.label,
        status=model.status,  # type: ignore[arg-type]
        max_output_tokens=model.max_output_tokens,
        supports_json_output=json_capability(model, provider),
        probe_evidence_stale=stale,
        resolved_name=None if stale else model.resolved_name,
        last_probe_outcome=None if stale else model.last_probe_outcome,  # type: ignore[arg-type]
        last_probe_at=model.last_probe_at,
        last_probe_detail=model.last_probe_detail,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _probe_entry(probe: ModelProbe) -> ProbeHistoryEntry:
    return ProbeHistoryEntry(
        id=probe.id,
        model_id=probe.model_id,
        outcome=probe.outcome,  # type: ignore[arg-type]
        error_code=probe.error_code,
        detail=probe.detail,
        started_at=probe.started_at,
        latency_ms=probe.latency_ms,
        call_count=probe.call_count,
        json_output_verified=probe.json_output_verified,
        usage_known=(probe.prompt_tokens is not None and probe.completion_tokens is not None),
    )


# ----------------------------------------------------------------- providers
@router.get("/models/centre", response_model=ModelCentreResponse)
def model_centre(
    request: Request, _authenticated: AuthenticatedDep, session: DbSession
) -> ModelCentreResponse:
    """Everything the model centre page needs in one read."""
    service = _service(request)
    providers = service.list_providers(session)
    provider_by_id = {view.provider.id: view.provider for view in providers}
    models = service.list_models(session)

    agent_repo = AgentRepository()
    assignment_repo = AssignmentRepository()
    company_id = None
    company = CompanyRepository().get(session)
    if company is not None:
        company_id = company.id

    bindings: list[AgentModelBinding] = []
    if company_id is not None:
        model_by_id = {model.id: model for model in models}
        current = {
            item.agent_id: item for item in assignment_repo.current_for_company(session, company_id)
        }
        positions = {
            position.id: position.title
            for position in PositionRepository().list_for_company(session, company_id)
        }
        for agent in agent_repo.list_for_company(session, company_id):
            assignment = current.get(agent.id)
            model = model_by_id.get(assignment.model_id) if assignment else None
            provider = provider_by_id.get(model.provider_id) if model else None
            bindings.append(
                AgentModelBinding(
                    agent_id=agent.id,
                    employee_code=agent.employee_code,
                    display_name=agent.display_name,
                    agent_status=agent.status,  # type: ignore[arg-type]
                    position_title=positions.get(agent.position_id, "—"),
                    model_id=model.id if model else None,
                    model_label=model.label if model else None,
                    provider_name=provider.name if provider else None,
                    provider_kind=provider.kind if provider else None,
                    model_status=model.status if model else None,  # type: ignore[arg-type]
                    provider_status=provider.status if provider else None,  # type: ignore[arg-type]
                    secret_configured=(service.secret_configured(provider) if provider else None),
                    json_verified=json_capability(model, provider) if model else None,
                    assigned_at=assignment.started_at if assignment else None,
                )
            )

    preset = service.deepseek_preset()
    usage = service.usage_summary(session)
    return ModelCentreResponse(
        providers=[
            _provider_response(
                view.provider,
                model_count=len(view.models),
                secret_state=view.secret_state,
            )
            for view in providers
        ],
        models=[
            _model_response(
                model,
                provider_by_id.get(model.provider_id),
                secret_configured=service.secret_configured(provider_by_id.get(model.provider_id)),
            )
            for model in models
        ],
        assignments=bindings,
        recent_probes=[_probe_entry(probe) for probe in service.recent_probes(session)],
        usage=UsageSummary(
            probe_calls=int(usage["probe_calls"] or 0),
            measured_calls=int(usage["measured_calls"] or 0),
            partially_measured_calls=int(usage["partially_measured_calls"] or 0),
            unmeasured_calls=int(usage["unmeasured_calls"] or 0),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            prompt_tokens_unreported_calls=int(usage["prompt_tokens_unreported_calls"] or 0),
            completion_tokens_unreported_calls=int(
                usage["completion_tokens_unreported_calls"] or 0
            ),
            pending_probes=int(usage["pending_probes"] or 0),
        ),
        deepseek_preset=DeepSeekPreset(
            kind=preset["kind"],  # type: ignore[arg-type]
            base_url=str(preset["baseUrl"]),
            secret_ref=str(preset["secretRef"]),
            suggested_model=str(preset["suggestedModel"]),
            notes=str(preset["notes"]),
        ),
    )


@router.post("/providers", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> ProviderResponse:
    service = _service(request)
    provider = service.create_provider(
        session,
        name=payload.name,
        kind=payload.kind,
        base_url=payload.base_url,
        secret_ref=payload.secret_ref,
        secret_value=payload.secret_value,
        status=payload.status,
        notes=payload.notes,
        timeout_seconds=payload.timeout_seconds,
        actor_user_id=authenticated.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return _provider_response(
        provider,
        model_count=0,
        secret_state=service.secret_state(provider),
    )


@router.patch("/providers/{provider_id}", response_model=ProviderResponse)
def update_provider(
    provider_id: int,
    payload: ProviderUpdateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> ProviderResponse:
    service = _service(request)
    provider = service.update_provider(
        session,
        provider_id=provider_id,
        name=payload.name,
        kind=payload.kind,
        base_url=payload.base_url,
        secret_ref=payload.secret_ref,
        change_secret_ref=payload.change_secret_ref,
        secret_value=payload.secret_value,
        change_secret_value=payload.change_secret_value,
        status=payload.status,
        notes=payload.notes,
        # Distinguish "notes omitted" from "notes explicitly cleared": the form
        # sends null to erase the text (review finding R9).
        notes_provided="notes" in payload.model_fields_set,
        timeout_seconds=payload.timeout_seconds,
        expected_version=payload.expected_version,
        actor_user_id=authenticated.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    view = service.get_provider_view(session, provider.id)
    return _provider_response(
        view.provider,
        model_count=len(view.models),
        secret_state=view.secret_state,
    )


# -------------------------------------------------------------------- models
@router.post("/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelCreateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> ModelResponse:
    service = _service(request)
    model = service.create_model(
        session,
        provider_id=payload.provider_id,
        logical_name=payload.logical_name,
        label=payload.label,
        max_output_tokens=payload.max_output_tokens,
        status=payload.status,
        actor_user_id=authenticated.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    provider = ProviderRepository().get(session, model.provider_id)
    return _model_response(
        model,
        provider,
        secret_configured=service.secret_configured(provider),
    )


@router.patch("/models/{model_id}", response_model=ModelResponse)
def update_model(
    model_id: int,
    payload: ModelUpdateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> ModelResponse:
    service = _service(request)
    model = service.update_model(
        session,
        model_id=model_id,
        expected_version=payload.expected_version,
        label=payload.label,
        max_output_tokens=payload.max_output_tokens,
        change_max_output_tokens=payload.change_max_output_tokens,
        status=payload.status,
        actor_user_id=authenticated.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    provider = ProviderRepository().get(session, model.provider_id)
    return _model_response(
        model,
        provider,
        secret_configured=service.secret_configured(provider),
    )


@router.post("/models/{model_id}/probe", response_model=ProbeResponse)
def probe_model(
    model_id: int,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> ProbeResponse:
    """Run one connectivity probe.

    Requires the CSRF header because it spends tokens. Exactly one provider call
    is made; the response reports whether usage was measured, and the row keeps
    ``usage_known`` false when the provider omitted it rather than pretending 0.
    """
    service = _service(request)
    outcome = service.probe_model(
        session,
        model_id=model_id,
        actor_user_id=authenticated.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    row = outcome.probe
    return ProbeResponse(
        id=row.id,
        model_id=row.model_id,
        outcome=row.outcome,  # type: ignore[arg-type]
        error_code=row.error_code,
        message=outcome.message,
        started_at=row.started_at,
        finished_at=row.finished_at,
        latency_ms=row.latency_ms,
        json_output_verified=row.json_output_verified,
        resolved_model_name=row.resolved_model_name,
        call_count=row.call_count,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        usage_known=outcome.usage_known,
        token_warning=PROBE_COST_NOTICE,
    )


@router.get("/models/{model_id}/probes", response_model=list[ProbeHistoryEntry])
def probe_history(
    model_id: int, request: Request, _authenticated: AuthenticatedDep, session: DbSession
) -> list[ProbeHistoryEntry]:
    service = _service(request)
    return [_probe_entry(probe) for probe in service.recent_probes(session, model_id)]
