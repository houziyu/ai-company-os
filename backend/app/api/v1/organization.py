"""Company and organization routes.

Every route here requires an authenticated owner session. Authorization is
enforced server-side: no code path reads ``agents.manager_id``, a duty text or a
model field to decide access. A management relation is a reporting edge only,
which is what the phase requires ("汇报关系不授予资源权限").

Write routes require the session-bound CSRF header via :data:`WriteGuard`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.orm import Session

from app.api.context import client_ip, get_request_id
from app.api.deps import AuthenticatedDep, DbSession, WriteGuard
from app.api.schemas_organization import (
    AgentAssignmentRequest,
    AgentHistoryEntry,
    AgentHistoryResponse,
    AgentModelRef,
    AgentSummary,
    AgentUpdateRequest,
    CompanyInitRequest,
    CompanyResponse,
    CompanyTemplateResponse,
    CompanyUpdateRequest,
    ModelUnbindRequest,
    OrganizationResponse,
    PositionSummary,
    PositionUpdateRequest,
    PositionUpdateResponse,
    TemplatePositionInfo,
)
from app.domain.company_template import TEMPLATE_KEY, TEMPLATE_VERSION
from app.domain.errors import AgentNotFoundError, CompanyNotFoundError
from app.domain.model_evidence import json_capability
from app.models.organization import Company, Model, Position, Provider
from app.repositories.organization_repository import (
    AgentRepository,
    AssignmentRepository,
    CompanyRepository,
    DepartmentRepository,
    ModelRepository,
    PositionRepository,
)
from app.services.organization_service import AgentDetail, CompanyService, OrganizationService

router = APIRouter(tags=["organization"])

TEMPLATE_LABEL = "科技公司（默认模板）"
TEMPLATE_DESCRIPTION = (
    "五个岗位：CEO、产品经理、技术负责人/开发、QA、DevOps。"
    "每岗位实例化一名独立员工，汇报关系可在组织页调整。"
)


def _company_service() -> CompanyService:
    return CompanyService()


def _org_service() -> OrganizationService:
    return OrganizationService()


def _company_response(
    company: Company, *, position_count: int, agent_count: int
) -> CompanyResponse:
    return CompanyResponse(
        id=company.id,
        name=company.name,
        goal=company.goal,
        status=company.status,  # type: ignore[arg-type]
        template_key=company.template_key,
        template_version=company.template_version,
        version=company.version,
        initialized_at=company.initialized_at,
        position_count=position_count,
        agent_count=agent_count,
    )


def _model_ref(model: Model | None, provider: Provider | None) -> AgentModelRef | None:
    if model is None or provider is None:
        return None
    return AgentModelRef(
        model_id=model.id,
        logical_name=model.logical_name,
        label=model.label,
        provider_id=provider.id,
        provider_name=provider.name,
        provider_kind=provider.kind,
        supports_json_output=json_capability(model, provider),
    )


def _agent_summary(detail: AgentDetail) -> AgentSummary:
    agent = detail.agent
    return AgentSummary(
        id=agent.id,
        employee_code=agent.employee_code,
        display_name=agent.display_name,
        status=agent.status,  # type: ignore[arg-type]
        position_id=detail.position.id,
        position_code=detail.position.code,
        position_title=detail.position.title,
        manager_id=agent.manager_id,
        manager_name=detail.manager.display_name if detail.manager else None,
        duty_override=agent.duty_override,
        effective_duty=agent.duty_override or detail.position_duty,
        model=_model_ref(detail.model, detail.provider),
        version=agent.version,
        status_changed_at=agent.status_changed_at,
        archived_at=agent.archived_at,
    )


def _position_summary(position: Position, duty: str, department_name: str) -> PositionSummary:
    return PositionSummary(
        id=position.id,
        code=position.code,
        title=position.title,
        duty=duty,
        version=position.current_version,
        row_version=position.row_version,
        sort_order=position.sort_order,
        department_name=department_name,
    )


def _duty_for(session: Session, position_id: int) -> str:
    version = PositionRepository().current_version(session, position_id)
    return version.duty if version else ""


def _department_name(session: Session, company_id: int) -> str:
    departments = DepartmentRepository().list_for_company(session, company_id)
    return departments[0].name if departments else ""


def _agent_summary_by_id(session: Session, agent_id: int) -> AgentSummary:
    for detail in _company_service().agent_details(session):
        if detail.agent.id == agent_id:
            return _agent_summary(detail)
    raise RuntimeError(f"agent {agent_id} disappeared after a successful write")


# ------------------------------------------------------------------- template
@router.get("/company/template", response_model=CompanyTemplateResponse)
def company_template(
    _authenticated: AuthenticatedDep, session: DbSession
) -> CompanyTemplateResponse:
    """Describe the built-in template so the wizard can show what will be created.

    Read from the seeded table rather than the Python module, so the wizard shows
    exactly what this database will instantiate.
    """
    rows = CompanyRepository().template_positions(session, TEMPLATE_KEY, TEMPLATE_VERSION)
    return CompanyTemplateResponse(
        template_key=TEMPLATE_KEY,
        template_version=TEMPLATE_VERSION,
        label=TEMPLATE_LABEL,
        description=TEMPLATE_DESCRIPTION,
        positions=[
            TemplatePositionInfo(
                code=row.code,
                title=row.title,
                default_agent_name=row.default_agent_name,
                duty=row.duty,
                reports_to_code=row.reports_to_code,
                sort_order=row.sort_order,
            )
            for row in rows
        ],
    )


# -------------------------------------------------------------------- company
@router.get("/company", response_model=CompanyResponse)
def get_company(_authenticated: AuthenticatedDep, session: DbSession) -> CompanyResponse:
    summary = _company_service().overview(session)
    return _company_response(
        summary.company,
        position_count=len(summary.positions),
        agent_count=len(summary.agents),
    )


@router.post("/company", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyInitRequest,
    request: Request,
    response: Response,
    authenticated: WriteGuard,
    session: DbSession,
) -> CompanyResponse:
    """Initialize the company from the template.

    Idempotency: resubmitting with the same key and the same content returns 200
    with the existing company instead of creating a second one. A different
    submission after a company exists is refused with 409.
    """
    company, created = _company_service().initialize(
        session,
        owner_user_id=authenticated.user.id,
        name=payload.name,
        goal=payload.goal,
        template_key=payload.template_key,
        template_version=payload.template_version,
        idempotency_key=payload.idempotency_key,
        model_id=payload.model_id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    summary = _company_service().overview(session)
    return _company_response(
        company,
        position_count=len(summary.positions),
        agent_count=len(summary.agents),
    )


@router.patch("/company", response_model=CompanyResponse)
def update_company(
    payload: CompanyUpdateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> CompanyResponse:
    company = _company_service().update_company(
        session,
        name=payload.name,
        goal=payload.goal,
        expected_version=payload.expected_version,
        actor_user_id=authenticated.user.id,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    summary = _company_service().overview(session)
    return _company_response(
        company, position_count=len(summary.positions), agent_count=len(summary.agents)
    )


# --------------------------------------------------------------- organization
@router.get("/organization", response_model=OrganizationResponse)
def get_organization(_authenticated: AuthenticatedDep, session: DbSession) -> OrganizationResponse:
    service = _company_service()
    summary = service.overview(session)
    details = service.agent_details(session)
    return OrganizationResponse(
        company=_company_response(
            summary.company,
            position_count=len(summary.positions),
            agent_count=len(summary.agents),
        ),
        department_name=summary.department.name,
        department_purpose=summary.department.purpose,
        positions=[
            _position_summary(position, _duty_for(session, position.id), summary.department.name)
            for position in summary.positions
        ],
        agents=[_agent_summary(detail) for detail in details],
    )


@router.patch("/positions/{position_id}", response_model=PositionUpdateResponse)
def update_position(
    position_id: int,
    payload: PositionUpdateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> PositionUpdateResponse:
    position, version_created = _org_service().update_position(
        session,
        position_id=position_id,
        actor_user_id=authenticated.user.id,
        title=payload.title,
        duty=payload.duty,
        expected_version=payload.expected_version,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return PositionUpdateResponse(
        position=_position_summary(
            position,
            _duty_for(session, position.id),
            _department_name(session, position.company_id),
        ),
        version_created=version_created,
    )


@router.patch("/agents/{agent_id}", response_model=AgentSummary)
def update_agent(
    agent_id: int,
    payload: AgentUpdateRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> AgentSummary:
    _org_service().update_agent(
        session,
        agent_id=agent_id,
        actor_user_id=authenticated.user.id,
        display_name=payload.display_name,
        duty_override=payload.duty_override,
        clear_duty_override=payload.clear_duty_override,
        manager_id=payload.manager_id,
        change_manager=payload.change_manager,
        status=payload.status,
        expected_version=payload.expected_version,
        reason=payload.reason,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return _agent_summary_by_id(session, agent_id)


@router.post("/agents/{agent_id}/model-assignment", response_model=AgentSummary)
def assign_agent_model(
    agent_id: int,
    payload: AgentAssignmentRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> AgentSummary:
    _org_service().assign_model(
        session,
        agent_id=agent_id,
        model_id=payload.model_id,
        actor_user_id=authenticated.user.id,
        note=payload.note,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return _agent_summary_by_id(session, agent_id)


@router.post("/agents/{agent_id}/model-unbind", response_model=AgentHistoryResponse)
def unbind_agent_model(
    agent_id: int,
    payload: ModelUnbindRequest,
    request: Request,
    authenticated: WriteGuard,
    session: DbSession,
) -> AgentHistoryResponse:
    """End the current model binding, keeping its history.

    Returning the history makes the effect visible in the same response: the
    client sees the binding marked "（已结束）" rather than having to guess.
    """
    _org_service().unbind_model(
        session,
        agent_id=agent_id,
        actor_user_id=authenticated.user.id,
        note=payload.note,
        request_id=get_request_id(),
        ip_address=client_ip(request),
    )
    return agent_history(agent_id, authenticated, session)


@router.get("/agents/{agent_id}/history", response_model=AgentHistoryResponse)
def agent_history(
    agent_id: int, _authenticated: AuthenticatedDep, session: DbSession
) -> AgentHistoryResponse:
    """Duty-override and model-binding history for one employee.

    This is what makes "身份与履历保留" inspectable: an archived employee keeps its
    full record, and a model change does not erase the previous binding.
    """
    company = CompanyRepository().get(session)
    if company is None:
        raise CompanyNotFoundError()
    agent = AgentRepository().get(session, agent_id)
    if agent is None or agent.company_id != company.id:
        raise AgentNotFoundError()

    entries: list[AgentHistoryEntry] = []
    for record in AgentRepository().list_duty_history(session, agent_id):
        entries.append(
            AgentHistoryEntry(
                kind="DUTY_OVERRIDE",
                occurred_at=record.created_at,
                summary="设置职责覆盖" if record.duty else "清除职责覆盖，回到岗位版本",
                detail=record.duty,
            )
        )

    models = {model.id: model for model in ModelRepository().list_all(session)}
    for assignment in AssignmentRepository().history(session, agent_id):
        model = models.get(assignment.model_id)
        label = model.label if model else f"模型 #{assignment.model_id}"
        entries.append(
            AgentHistoryEntry(
                kind="MODEL_ASSIGNMENT",
                occurred_at=assignment.started_at,
                summary=f"绑定模型 {label}" + ("（已结束）" if assignment.ended_at else "（当前）"),
                detail=model.logical_name if model else None,
            )
        )

    entries.sort(key=lambda item: item.occurred_at, reverse=True)
    return AgentHistoryResponse(agent_id=agent_id, entries=entries)
