"""Company initialization and organization editing.

Transaction and concurrency model
---------------------------------
* **Company creation is one-shot and idempotent.** The service locks the
  ``platform_state`` row (the same singleton lock the auth service uses) before
  reading ``companies``, so N concurrent wizard submissions serialise. The first
  one inserts; the rest observe the row and either return it unchanged (same
  idempotency key and same request fingerprint) or raise
  ``COMPANY_ALREADY_EXISTS``. The ``id = 1`` primary key plus check constraint
  is the final backstop if two processes were ever to bypass the lock.
* **Organization edits lock the employee rows of the company** before validating
  the management graph, so a concurrent rewire cannot validate against a graph
  the other transaction is mutating. Validation and write happen in the same
  transaction; a rejected change leaves no partial state.
* **History is append-only.** Duty changes append to ``agent_duty_override_history``
  and position duty changes append a new ``position_versions`` row. Nothing here
  deletes an employee, a position or a version.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.company_template import (
    DEPARTMENT_CODE,
    DEPARTMENT_NAME,
    DEPARTMENT_PURPOSE,
    TEMPLATE_KEY,
    TEMPLATE_VERSION,
)
from app.domain.enums import AgentStatus, AuditAction, AuditActorType
from app.domain.errors import (
    AgentNotFoundError,
    CompanyAlreadyExistsError,
    CompanyNotFoundError,
    ConcurrentUpdateError,
    CrossCompanyRelationError,
    FieldError,
    ModelBindingUnavailableError,
    ModelNotFoundError,
    PositionNotFoundError,
    ReportingCycleError,
    SelfReportError,
    ValidationFailedError,
)
from app.models.organization import (
    Agent,
    AgentModelAssignment,
    Company,
    Department,
    Model,
    Position,
    Provider,
)
from app.repositories.auth_repository import AuditRepository, PlatformRepository, utcnow
from app.repositories.organization_repository import (
    AgentRepository,
    AssignmentRepository,
    CompanyRepository,
    DepartmentRepository,
    ModelRepository,
    PositionRepository,
    ProviderRepository,
)

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 120
MAX_GOAL_LENGTH = 2000
MAX_DUTY_LENGTH = 4000
MAX_NOTE_LENGTH = 200


@dataclass(slots=True)
class CompanySummary:
    company: Company
    department: Department
    positions: Sequence[Position]
    agents: Sequence[Agent]


@dataclass(slots=True)
class AgentDetail:
    agent: Agent
    position: Position
    position_duty: str
    manager: Agent | None
    assignment: AgentModelAssignment | None
    model: Model | None
    provider: Provider | None


def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean(value: str) -> str:
    return value.strip()


class CompanyService:
    def __init__(self) -> None:
        self._companies = CompanyRepository()
        self._departments = DepartmentRepository()
        self._positions = PositionRepository()
        self._agents = AgentRepository()
        self._assignments = AssignmentRepository()
        self._models = ModelRepository()
        self._providers = ProviderRepository()
        self._audit = AuditRepository()

    # ------------------------------------------------------------------ read
    def get_company(self, session: Session) -> Company:
        company = self._companies.get(session)
        if company is None:
            raise CompanyNotFoundError()
        return company

    def overview(self, session: Session) -> CompanySummary:
        company = self.get_company(session)
        departments = self._departments.list_for_company(session, company.id)
        if not departments:
            raise CompanyNotFoundError()
        return CompanySummary(
            company=company,
            department=departments[0],
            positions=self._positions.list_for_company(session, company.id),
            agents=self._agents.list_for_company(session, company.id),
        )

    def agent_details(self, session: Session) -> list[AgentDetail]:
        company = self.get_company(session)
        agents = self._agents.list_for_company(session, company.id)
        positions = {p.id: p for p in self._positions.list_for_company(session, company.id)}
        assignments = {
            a.agent_id: a for a in self._assignments.current_for_company(session, company.id)
        }
        models = {m.id: m for m in self._models.list_all(session)}
        providers = {p.id: p for p in self._providers.list_all(session)}
        by_id = {a.id: a for a in agents}

        details: list[AgentDetail] = []
        for agent in agents:
            position = positions[agent.position_id]
            version = self._positions.current_version(session, position.id)
            assignment = assignments.get(agent.id)
            model = models.get(assignment.model_id) if assignment else None
            provider = providers.get(model.provider_id) if model else None
            details.append(
                AgentDetail(
                    agent=agent,
                    position=position,
                    position_duty=version.duty if version else "",
                    manager=by_id.get(agent.manager_id) if agent.manager_id else None,
                    assignment=assignment,
                    model=model,
                    provider=provider,
                )
            )
        return details

    # -------------------------------------------------------------- create
    def initialize(
        self,
        session: Session,
        *,
        owner_user_id: int,
        name: str,
        goal: str,
        template_key: str = TEMPLATE_KEY,
        template_version: int = TEMPLATE_VERSION,
        idempotency_key: str | None = None,
        model_id: int | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Company, bool]:
        """Create the company, department, five positions and five employees.

        Returns ``(company, created)``. ``created`` is False when this call was a
        repeat of an earlier successful one; the caller then answers 200 instead
        of 201 so a retried wizard submit is not reported as a new company.
        """
        cleaned_name = _clean(name)
        cleaned_goal = _clean(goal)
        field_errors: list[FieldError] = []
        if not cleaned_name:
            field_errors.append(FieldError("name", "请输入公司名称。"))
        elif len(cleaned_name) > MAX_NAME_LENGTH:
            field_errors.append(FieldError("name", f"公司名称不能超过 {MAX_NAME_LENGTH} 个字符。"))
        if not cleaned_goal:
            field_errors.append(FieldError("goal", "请填写公司目标。"))
        elif len(cleaned_goal) > MAX_GOAL_LENGTH:
            field_errors.append(FieldError("goal", f"公司目标不能超过 {MAX_GOAL_LENGTH} 个字符。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        fingerprint = _fingerprint(
            {
                "name": cleaned_name,
                "goal": cleaned_goal,
                "templateKey": template_key,
                "templateVersion": template_version,
                "modelId": model_id,
            }
        )

        # Serialise with owner setup and with other wizard submissions.
        PlatformRepository().lock_state(session)

        existing = self._companies.get(session)
        if existing is not None:
            same_request = (
                idempotency_key is not None
                and existing.initialization_idempotency_key == idempotency_key
                and existing.initialization_fingerprint == fingerprint
            )
            if same_request:
                return existing, False
            raise CompanyAlreadyExistsError()

        template_rows = self._companies.template_positions(session, template_key, template_version)
        if len(template_rows) != 5:
            raise RuntimeError(
                f"template {template_key} v{template_version} has {len(template_rows)} positions,"
                " expected 5; database migrations may be incomplete"
            )

        if model_id is not None:
            model = self._models.get(session, model_id)
            if model is None:
                raise ValidationFailedError(
                    [FieldError("modelId", "所选模型不存在，请先在模型中心创建。")]
                )
            provider = self._providers.get(session, model.provider_id)
            unavailable = _binding_problem(model, provider)
            if unavailable:
                raise ModelBindingUnavailableError(unavailable)

        company = self._companies.create(
            session,
            name=cleaned_name,
            goal=cleaned_goal,
            template_key=template_key,
            template_version=template_version,
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

        department = self._departments.create(
            session,
            company_id=company.id,
            code=DEPARTMENT_CODE,
            name=DEPARTMENT_NAME,
            purpose=DEPARTMENT_PURPOSE,
        )

        positions_by_code: dict[str, Position] = {}
        for row in template_rows:
            position = self._positions.create(
                session,
                company_id=company.id,
                department_id=department.id,
                code=row.code,
                title=row.title,
                sort_order=row.sort_order,
            )
            self._positions.add_version(
                session,
                position_id=position.id,
                version=1,
                title=row.title,
                duty=row.duty,
                changed_by_user_id=owner_user_id,
                note="template",
            )
            positions_by_code[row.code] = position

        # Employees are created in template order so the CEO exists before the
        # employees that report to it. Employee codes derive from the template
        # code, which keeps them stable and human-readable ("CEO-001").
        template_meta = {row.code: row for row in template_rows}
        agents_by_code: dict[str, Agent] = {}
        for row in template_rows:
            manager = agents_by_code.get(row.reports_to_code or "")
            agent = self._agents.create(
                session,
                company_id=company.id,
                position_id=positions_by_code[row.code].id,
                employee_code=f"{row.code}-001",
                display_name=template_meta[row.code].default_agent_name,
                manager_id=manager.id if manager else None,
            )
            agents_by_code[row.code] = agent

        if model_id is not None:
            bound_model = self._models.get(session, model_id)
            bound_provider = (
                self._providers.get(session, bound_model.provider_id) if bound_model else None
            )
            for code, agent in agents_by_code.items():
                self._assignments.open(
                    session,
                    agent_id=agent.id,
                    model_id=model_id,
                    assigned_by_user_id=owner_user_id,
                    note="initial",
                )
                # Initial bindings are audited like any later rebind: "who bound
                # which model to which employee" must be answerable afterwards,
                # otherwise the creation step would be the one change with no
                # evidence.
                self._audit.add(
                    session,
                    action=AuditAction.AGENT_MODEL_ASSIGNED.value,
                    outcome="SUCCESS",
                    actor_type=AuditActorType.OWNER.value,
                    actor_user_id=owner_user_id,
                    target_type="AGENT",
                    target_id=str(agent.id),
                    request_id=request_id,
                    ip_address=ip_address,
                    detail=(
                        f"employee={agent.employee_code};position={code};"
                        f"model={bound_model.logical_name if bound_model else model_id};"
                        f"provider={bound_provider.name if bound_provider else 'unknown'};"
                        "initial=true"
                    ),
                )

        self._audit.add(
            session,
            action=AuditAction.COMPANY_INITIALIZED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=owner_user_id,
            target_type="COMPANY",
            target_id=str(company.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=(
                f"template={template_key}@v{template_version};"
                f"positions={len(positions_by_code)};agents={len(agents_by_code)}"
            ),
        )
        logger.info("company initialized", extra={"user_id": owner_user_id})
        return company, True

    # -------------------------------------------------------------- update
    def update_company(
        self,
        session: Session,
        *,
        name: str,
        goal: str,
        expected_version: int,
        actor_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Company:
        cleaned_name = _clean(name)
        cleaned_goal = _clean(goal)
        field_errors: list[FieldError] = []
        if not cleaned_name:
            field_errors.append(FieldError("name", "请输入公司名称。"))
        elif len(cleaned_name) > MAX_NAME_LENGTH:
            field_errors.append(FieldError("name", f"公司名称不能超过 {MAX_NAME_LENGTH} 个字符。"))
        if not cleaned_goal:
            field_errors.append(FieldError("goal", "请填写公司目标。"))
        elif len(cleaned_goal) > MAX_GOAL_LENGTH:
            field_errors.append(FieldError("goal", f"公司目标不能超过 {MAX_GOAL_LENGTH} 个字符。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        company = self._companies.get(session, for_update=True)
        if company is None:
            raise CompanyNotFoundError()
        if company.version != expected_version:
            raise ConcurrentUpdateError("company")

        changes: list[str] = []
        if company.name != cleaned_name:
            changes.append("name")
            company.name = cleaned_name
        if company.goal != cleaned_goal:
            changes.append("goal")
            company.goal = cleaned_goal
        if not changes:
            return company
        company.version += 1
        session.flush()

        self._audit.add(
            session,
            action=AuditAction.COMPANY_UPDATED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="COMPANY",
            target_id=str(company.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"fields={','.join(changes)};version={company.version}",
        )
        return company


class OrganizationService:
    def __init__(self) -> None:
        self._companies = CompanyRepository()
        self._positions = PositionRepository()
        self._agents = AgentRepository()
        self._assignments = AssignmentRepository()
        self._models = ModelRepository()
        self._providers = ProviderRepository()
        self._audit = AuditRepository()

    # --------------------------------------------------------------- guard
    def _company_id(self, session: Session) -> int:
        company = self._companies.get(session)
        if company is None:
            raise CompanyNotFoundError()
        return company.id

    def _require_agent(self, session: Session, company_id: int, agent_id: int) -> Agent:
        agent = self._agents.get(session, agent_id)
        if agent is None or agent.company_id != company_id:
            raise AgentNotFoundError()
        return agent

    # -------------------------------------------------------------- update
    def update_position(
        self,
        session: Session,
        *,
        position_id: int,
        actor_user_id: int,
        title: str | None = None,
        duty: str | None = None,
        expected_version: int | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[Position, bool]:
        """Update a position title and/or duty, appending a version when duty changes.

        Returns ``(position, version_created)``. A title-only change does not spam
        the version table; a duty change always appends, and the old row is kept
        verbatim so historical executions remain attributable.
        """
        company_id = self._company_id(session)
        position = self._positions.get(session, position_id, for_update=True)
        if position is None or position.company_id != company_id:
            raise PositionNotFoundError()
        if expected_version is not None and position.row_version != expected_version:
            raise ConcurrentUpdateError("position")

        current = self._positions.current_version(session, position.id)
        if current is None:
            raise RuntimeError(f"position {position.id} has no version row")

        cleaned_title = _clean(title) if title is not None else position.title
        cleaned_duty = _clean(duty) if duty is not None else current.duty

        field_errors: list[FieldError] = []
        if not cleaned_title:
            field_errors.append(FieldError("title", "请输入岗位名称。"))
        elif len(cleaned_title) > 80:
            field_errors.append(FieldError("title", "岗位名称不能超过 80 个字符。"))
        if not cleaned_duty:
            field_errors.append(FieldError("duty", "岗位职责不能为空。"))
        elif len(cleaned_duty) > MAX_DUTY_LENGTH:
            field_errors.append(FieldError("duty", f"岗位职责不能超过 {MAX_DUTY_LENGTH} 个字符。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        title_changed = cleaned_title != position.title
        duty_changed = cleaned_duty != current.duty
        if not title_changed and not duty_changed:
            return position, False

        if title_changed:
            position.title = cleaned_title
        position.row_version += 1
        version_created = False
        if duty_changed:
            next_version = position.current_version + 1
            position.current_version = next_version
            self._positions.add_version(
                session,
                position_id=position.id,
                version=next_version,
                title=cleaned_title,
                duty=cleaned_duty,
                changed_by_user_id=actor_user_id,
                note="duty updated",
            )
            version_created = True
        session.flush()

        self._audit.add(
            session,
            action=AuditAction.POSITION_UPDATED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="POSITION",
            target_id=str(position.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=(
                f"code={position.code};title_changed={title_changed};"
                f"duty_changed={duty_changed};duty_version={position.current_version};"
                f"row_version={position.row_version}"
            ),
        )
        return position, version_created

    def update_agent(
        self,
        session: Session,
        *,
        agent_id: int,
        actor_user_id: int,
        display_name: str | None = None,
        duty_override: str | None = None,
        clear_duty_override: bool = False,
        manager_id: int | None = None,
        change_manager: bool = False,
        status: str | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Agent:
        """Edit an employee's profile, duty, manager or status.

        Every accepted change writes audit evidence. Management changes are
        validated against the whole graph while all employee rows of the company
        are locked, so a cycle cannot be introduced by concurrent edits. Nothing
        here grants resource permissions: ``manager_id`` is only a reporting
        edge, and no authorization check reads it.
        """
        company_id = self._company_id(session)

        # Lock the graph first: the cycle check must see a stable tree.
        self._agents.lock_company_agents(session, company_id)
        agent = self._require_agent(session, company_id, agent_id)

        if expected_version is not None and agent.version != expected_version:
            raise ConcurrentUpdateError("agent")

        field_errors: list[FieldError] = []
        cleaned_name = _clean(display_name) if display_name is not None else agent.display_name
        if not cleaned_name:
            field_errors.append(FieldError("displayName", "请输入员工显示名称。"))
        elif len(cleaned_name) > 80:
            field_errors.append(FieldError("displayName", "员工显示名称不能超过 80 个字符。"))

        cleaned_override: str | None = agent.duty_override
        if clear_duty_override:
            cleaned_override = None
        elif duty_override is not None:
            cleaned_override = _clean(duty_override) or None
            if cleaned_override and len(cleaned_override) > MAX_DUTY_LENGTH:
                field_errors.append(
                    FieldError("dutyOverride", f"职责覆盖不能超过 {MAX_DUTY_LENGTH} 个字符。")
                )

        new_status = status if status is not None else agent.status
        if new_status not in {item.value for item in AgentStatus}:
            field_errors.append(FieldError("status", "未知的员工状态。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        changed_fields: list[str] = []

        if cleaned_name != agent.display_name:
            agent.display_name = cleaned_name
            changed_fields.append("displayName")

        if cleaned_override != agent.duty_override:
            agent.duty_override = cleaned_override
            self._agents.add_duty_history(
                session,
                agent_id=agent.id,
                duty=cleaned_override,
                changed_by_user_id=actor_user_id,
                reason=(_clean(reason) if reason else None),
            )
            changed_fields.append("dutyOverride")

        if change_manager:
            new_manager_id = manager_id
            if new_manager_id is not None:
                if new_manager_id == agent.id:
                    raise SelfReportError()
                manager = self._agents.get(session, new_manager_id)
                if manager is None or manager.company_id != company_id:
                    raise CrossCompanyRelationError()
                if manager.status == AgentStatus.ARCHIVED.value:
                    raise ValidationFailedError(
                        [FieldError("managerId", "已归档的员工不能作为主管。")]
                    )
                # A cycle exists exactly when the proposed manager already
                # reports to this employee.
                if new_manager_id in self._agents.descendants(session, company_id, agent.id):
                    raise ReportingCycleError(
                        "该汇报关系会形成循环：目标主管当前直接或间接向你汇报。"
                    )
            if new_manager_id != agent.manager_id:
                agent.manager_id = new_manager_id
                changed_fields.append("managerId")

        status_changed = new_status != agent.status
        if status_changed:
            previous = agent.status
            agent.status = new_status
            agent.status_changed_at = utcnow()
            agent.archived_at = utcnow() if new_status == AgentStatus.ARCHIVED.value else None
            changed_fields.append("status")
            self._audit.add(
                session,
                action=AuditAction.AGENT_STATUS_CHANGED.value,
                outcome="SUCCESS",
                actor_type=AuditActorType.OWNER.value,
                actor_user_id=actor_user_id,
                target_type="AGENT",
                target_id=str(agent.id),
                request_id=request_id,
                ip_address=ip_address,
                detail=f"from={previous};to={new_status};employee={agent.employee_code}",
            )

        if not changed_fields:
            return agent

        agent.version += 1
        session.flush()

        profile_changes = [f for f in changed_fields if f != "status"]
        if profile_changes:
            self._audit.add(
                session,
                action=AuditAction.AGENT_UPDATED.value,
                outcome="SUCCESS",
                actor_type=AuditActorType.OWNER.value,
                actor_user_id=actor_user_id,
                target_type="AGENT",
                target_id=str(agent.id),
                request_id=request_id,
                ip_address=ip_address,
                detail=(
                    f"employee={agent.employee_code};fields={','.join(profile_changes)};"
                    f"version={agent.version}"
                ),
            )
        return agent

    def unbind_model(
        self,
        session: Session,
        *,
        agent_id: int,
        actor_user_id: int,
        note: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> AgentModelAssignment | None:
        """End an employee's current model binding without deleting it.

        The UI offers "未绑定" as a real choice, so it must do something: silently
        ignoring the selection was review finding R11. The binding row is closed
        (``ended_at`` set) rather than removed, so history and any execution
        recorded against it stay attributable.
        """
        company_id = self._company_id(session)
        agent = self._agents.get(session, agent_id, for_update=True)
        if agent is None or agent.company_id != company_id:
            raise AgentNotFoundError()

        closed = self._assignments.close_current(
            session,
            agent.id,
            at=utcnow(),
            note=_clean(note) if note else "unbound",
        )
        if closed is None:
            return None

        self._audit.add(
            session,
            action=AuditAction.AGENT_MODEL_ASSIGNED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="AGENT",
            target_id=str(agent.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"employee={agent.employee_code};model=unbound",
        )
        return closed

    # ------------------------------------------------------------ binding
    def assign_model(
        self,
        session: Session,
        *,
        agent_id: int,
        model_id: int,
        actor_user_id: int,
        note: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[AgentModelAssignment, AgentModelAssignment | None]:
        """Rebind an employee's model, closing the previous binding.

        The previous row is closed rather than deleted, so when Phase 05 records
        executions against a binding id the historical configuration stays
        readable.
        """
        company_id = self._company_id(session)
        agent = self._agents.get(session, agent_id, for_update=True)
        if agent is None or agent.company_id != company_id:
            raise AgentNotFoundError()

        model = self._models.get(session, model_id)
        if model is None:
            raise ModelNotFoundError()
        provider = self._providers.get(session, model.provider_id)
        unavailable = _binding_problem(model, provider)
        if unavailable:
            raise ModelBindingUnavailableError(unavailable)

        previous = self._assignments.current(session, agent.id)
        if previous is not None and previous.model_id == model.id:
            return previous, None

        now = utcnow()
        if previous is not None:
            self._assignments.close(session, previous, at=now)
        assignment = self._assignments.open(
            session,
            agent_id=agent.id,
            model_id=model.id,
            assigned_by_user_id=actor_user_id,
            note=(_clean(note) if note else None),
        )

        self._audit.add(
            session,
            action=AuditAction.AGENT_MODEL_ASSIGNED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="AGENT",
            target_id=str(agent.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=(
                f"employee={agent.employee_code};model={model.logical_name};"
                f"provider={provider.name if provider else 'unknown'}"
            ),
        )
        return assignment, previous


def _binding_problem(model: Model, provider: Provider | None) -> str | None:
    if provider is None:
        return "所选模型缺少供应商记录，无法分配。"
    if provider.status != "ENABLED":
        return "该模型所属供应商已停用，不能分配。"
    if model.status != "ENABLED":
        return "该模型已停用，不能分配。"
    return None
