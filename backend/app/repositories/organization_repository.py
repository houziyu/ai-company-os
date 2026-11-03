"""Data access for the company, organization and model centre.

Repositories take an existing :class:`Session`; the service layer owns the
transaction boundary so a single commit covers the business change, its audit
event and any usage record.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import ProbeOutcome
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
from app.repositories.auth_repository import utcnow

COMPANY_ID = 1


class CompanyRepository:
    def get(self, session: Session, *, for_update: bool = False) -> Company | None:
        stmt = select(Company).where(Company.id == COMPANY_ID)
        if for_update:
            stmt = stmt.with_for_update()
        return session.scalars(stmt).one_or_none()

    def create(
        self,
        session: Session,
        *,
        name: str,
        goal: str,
        template_key: str,
        template_version: int,
        owner_user_id: int,
        idempotency_key: str | None,
        fingerprint: str | None,
    ) -> Company:
        company = Company(
            id=COMPANY_ID,
            name=name,
            goal=goal,
            status="ACTIVE",
            template_key=template_key,
            template_version=template_version,
            initialized_by_user_id=owner_user_id,
            initialization_idempotency_key=idempotency_key,
            initialization_fingerprint=fingerprint,
        )
        session.add(company)
        session.flush()
        return company

    def count_template_positions(self, session: Session, template_key: str, version: int) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(CompanyTemplatePosition)
                .where(
                    CompanyTemplatePosition.template_key == template_key,
                    CompanyTemplatePosition.template_version == version,
                )
            )
            or 0
        )

    def template_positions(
        self, session: Session, template_key: str, version: int
    ) -> Sequence[CompanyTemplatePosition]:
        return session.scalars(
            select(CompanyTemplatePosition)
            .where(
                CompanyTemplatePosition.template_key == template_key,
                CompanyTemplatePosition.template_version == version,
            )
            .order_by(CompanyTemplatePosition.sort_order)
        ).all()


class DepartmentRepository:
    def create(
        self, session: Session, *, company_id: int, code: str, name: str, purpose: str
    ) -> Department:
        department = Department(company_id=company_id, code=code, name=name, purpose=purpose)
        session.add(department)
        session.flush()
        return department

    def list_for_company(self, session: Session, company_id: int) -> Sequence[Department]:
        return session.scalars(
            select(Department)
            .where(Department.company_id == company_id)
            .order_by(Department.name, Department.id)
        ).all()


class PositionRepository:
    def create(
        self,
        session: Session,
        *,
        company_id: int,
        department_id: int | None,
        code: str,
        title: str,
        sort_order: int,
    ) -> Position:
        position = Position(
            company_id=company_id,
            department_id=department_id,
            code=code,
            title=title,
            sort_order=sort_order,
            current_version=1,
        )
        session.add(position)
        session.flush()
        return position

    def get(
        self, session: Session, position_id: int, *, for_update: bool = False
    ) -> Position | None:
        stmt = select(Position).where(Position.id == position_id)
        if for_update:
            stmt = stmt.with_for_update()
        return session.scalars(stmt).one_or_none()

    def list_for_company(self, session: Session, company_id: int) -> Sequence[Position]:
        return session.scalars(
            select(Position)
            .where(Position.company_id == company_id)
            .order_by(Position.sort_order, Position.id)
        ).all()

    def current_version(
        self, session: Session, position_id: int, version: int | None = None
    ) -> PositionVersion | None:
        stmt = select(PositionVersion).where(PositionVersion.position_id == position_id)
        if version is None:
            stmt = stmt.order_by(PositionVersion.version.desc()).limit(1)
        else:
            stmt = stmt.where(PositionVersion.version == version)
        return session.scalars(stmt).one_or_none()

    def add_version(
        self,
        session: Session,
        *,
        position_id: int,
        version: int,
        title: str,
        duty: str,
        changed_by_user_id: int | None,
        note: str | None,
    ) -> PositionVersion:
        record = PositionVersion(
            position_id=position_id,
            version=version,
            title=title,
            duty=duty,
            changed_by_user_id=changed_by_user_id,
            note=note,
        )
        session.add(record)
        session.flush()
        return record

    def list_versions(self, session: Session, position_id: int) -> Sequence[PositionVersion]:
        return session.scalars(
            select(PositionVersion)
            .where(PositionVersion.position_id == position_id)
            .order_by(PositionVersion.version.desc())
        ).all()


class AgentRepository:
    def create(
        self,
        session: Session,
        *,
        company_id: int,
        position_id: int,
        employee_code: str,
        display_name: str,
        manager_id: int | None,
        status: str = "ACTIVE",
    ) -> Agent:
        agent = Agent(
            company_id=company_id,
            position_id=position_id,
            employee_code=employee_code,
            display_name=display_name,
            manager_id=manager_id,
            status=status,
        )
        session.add(agent)
        session.flush()
        return agent

    def get(self, session: Session, agent_id: int, *, for_update: bool = False) -> Agent | None:
        stmt = select(Agent).where(Agent.id == agent_id)
        if for_update:
            stmt = stmt.with_for_update()
        return session.scalars(stmt).one_or_none()

    def list_for_company(self, session: Session, company_id: int) -> Sequence[Agent]:
        return session.scalars(
            select(Agent).where(Agent.company_id == company_id).order_by(Agent.id)
        ).all()

    def lock_company_agents(self, session: Session, company_id: int) -> Sequence[Agent]:
        """Lock every employee row of the company for the duration of a rewire.

        Manager changes are validated against the whole graph, so all rows must
        be stable while the cycle check runs. Concurrent rewires therefore
        serialise instead of each validating against a graph the other is
        changing. Lock order is by id, which the driver issues consistently.
        """
        return session.scalars(
            select(Agent).where(Agent.company_id == company_id).order_by(Agent.id).with_for_update()
        ).all()

    def descendants(self, session: Session, company_id: int, agent_id: int) -> set[int]:
        """Return every employee id that reports (transitively) to ``agent_id``."""
        edges = session.execute(
            select(Agent.id, Agent.manager_id).where(Agent.company_id == company_id)
        ).all()
        children: dict[int, list[int]] = {}
        for employee_id, manager_id in edges:
            if manager_id is not None:
                children.setdefault(manager_id, []).append(employee_id)
        seen: set[int] = set()
        stack = [agent_id]
        while stack:
            current = stack.pop()
            for child in children.get(current, []):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def add_duty_history(
        self,
        session: Session,
        *,
        agent_id: int,
        duty: str | None,
        changed_by_user_id: int | None,
        reason: str | None,
    ) -> AgentDutyOverrideHistory:
        record = AgentDutyOverrideHistory(
            agent_id=agent_id,
            duty=duty,
            changed_by_user_id=changed_by_user_id,
            reason=reason,
        )
        session.add(record)
        session.flush()
        return record

    def list_duty_history(
        self, session: Session, agent_id: int
    ) -> Sequence[AgentDutyOverrideHistory]:
        return session.scalars(
            select(AgentDutyOverrideHistory)
            .where(AgentDutyOverrideHistory.agent_id == agent_id)
            .order_by(
                AgentDutyOverrideHistory.created_at.desc(), AgentDutyOverrideHistory.id.desc()
            )
        ).all()


class ProviderRepository:
    def get(
        self, session: Session, provider_id: int, *, for_update: bool = False
    ) -> Provider | None:
        stmt = select(Provider).where(Provider.id == provider_id)
        if for_update:
            # These updates never change keys. NO KEY UPDATE serializes writers
            # without deadlocking concurrent probe inserts' FK KEY SHARE locks.
            stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
        return session.scalars(stmt).one_or_none()

    def get_by_name(self, session: Session, name: str) -> Provider | None:
        return session.scalars(select(Provider).where(Provider.name == name)).one_or_none()

    def list_all(self, session: Session) -> Sequence[Provider]:
        return session.scalars(select(Provider).order_by(Provider.id)).all()

    def create(
        self,
        session: Session,
        *,
        name: str,
        kind: str,
        base_url: str,
        secret_ref: str | None,
        secret_ciphertext: str | None,
        secret_source: str,
        status: str,
        notes: str | None,
        timeout_seconds: float,
        config_fingerprint: str,
    ) -> Provider:
        provider = Provider(
            name=name,
            kind=kind,
            base_url=base_url,
            secret_ref=secret_ref,
            secret_ciphertext=secret_ciphertext,
            secret_source=secret_source,
            status=status,
            notes=notes,
            timeout_seconds=timeout_seconds,
            config_fingerprint=config_fingerprint,
        )
        session.add(provider)
        session.flush()
        return provider


class ModelRepository:
    def get(self, session: Session, model_id: int, *, for_update: bool = False) -> Model | None:
        stmt = select(Model).where(Model.id == model_id)
        if for_update:
            stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
        return session.scalars(stmt).one_or_none()

    def get_by_logical_name(
        self, session: Session, provider_id: int, logical_name: str
    ) -> Model | None:
        return session.scalars(
            select(Model).where(
                Model.provider_id == provider_id, Model.logical_name == logical_name
            )
        ).one_or_none()

    def list_all(self, session: Session) -> Sequence[Model]:
        return session.scalars(select(Model).order_by(Model.provider_id, Model.id)).all()

    def list_for_provider(self, session: Session, provider_id: int) -> Sequence[Model]:
        return session.scalars(
            select(Model).where(Model.provider_id == provider_id).order_by(Model.id)
        ).all()

    def create(
        self,
        session: Session,
        *,
        provider_id: int,
        logical_name: str,
        label: str,
        status: str,
        max_output_tokens: int | None,
    ) -> Model:
        model = Model(
            provider_id=provider_id,
            logical_name=logical_name,
            label=label,
            status=status,
            max_output_tokens=max_output_tokens,
        )
        session.add(model)
        session.flush()
        return model


class AssignmentRepository:
    def current(self, session: Session, agent_id: int) -> AgentModelAssignment | None:
        return session.scalars(
            select(AgentModelAssignment).where(
                AgentModelAssignment.agent_id == agent_id,
                AgentModelAssignment.ended_at.is_(None),
            )
        ).one_or_none()

    def current_for_company(
        self, session: Session, company_id: int
    ) -> Sequence[AgentModelAssignment]:
        return session.scalars(
            select(AgentModelAssignment)
            .join(Agent, Agent.id == AgentModelAssignment.agent_id)
            .where(Agent.company_id == company_id, AgentModelAssignment.ended_at.is_(None))
        ).all()

    def history(self, session: Session, agent_id: int) -> Sequence[AgentModelAssignment]:
        return session.scalars(
            select(AgentModelAssignment)
            .where(AgentModelAssignment.agent_id == agent_id)
            .order_by(AgentModelAssignment.started_at.desc(), AgentModelAssignment.id.desc())
        ).all()

    def open(
        self,
        session: Session,
        *,
        agent_id: int,
        model_id: int,
        assigned_by_user_id: int | None,
        note: str | None,
    ) -> AgentModelAssignment:
        record = AgentModelAssignment(
            agent_id=agent_id,
            model_id=model_id,
            assigned_by_user_id=assigned_by_user_id,
            note=note,
        )
        session.add(record)
        session.flush()
        return record

    def close(self, session: Session, record: AgentModelAssignment, *, at: datetime) -> None:
        if record.ended_at is None:
            record.ended_at = at if at >= record.started_at else record.started_at
            session.flush()

    def close_current(
        self, session: Session, agent_id: int, *, at: datetime, note: str | None = None
    ) -> AgentModelAssignment | None:
        """End the open binding for an employee, keeping the row.

        Unbinding is a history-preserving operation: the row stays with its
        ``ended_at`` so a later phase can still attribute executions to the model
        that was bound at the time (review finding R11).
        """
        record = self.current(session, agent_id)
        if record is None:
            return None
        if note:
            record.note = note[:200]
        self.close(session, record, at=at)
        return record


class ProbeRepository:
    def create_intent(
        self,
        session: Session,
        *,
        model_id: int,
        provider_id: int,
        initiated_by_user_id: int | None,
        request_id: str | None,
        config_fingerprint: str,
    ) -> ModelProbe:
        """Insert the PENDING intent row and flush it.

        The caller commits this in its own short transaction *before* the network
        call, so a process that dies mid-call still leaves the fact that a paid
        request may have happened (review finding R2). ``call_count`` is 1 from
        the start: a call is being attempted now.
        """
        probe = ModelProbe(
            model_id=model_id,
            provider_id=provider_id,
            initiated_by_user_id=initiated_by_user_id,
            request_id=request_id,
            started_at=utcnow(),
            outcome=ProbeOutcome.PENDING.value,
            config_fingerprint=config_fingerprint,
            call_count=1,
        )
        session.add(probe)
        session.flush()
        return probe

    def create_refusal(
        self,
        session: Session,
        *,
        model_id: int,
        provider_id: int,
        initiated_by_user_id: int | None,
        request_id: str | None,
        config_fingerprint: str,
    ) -> ModelProbe:
        """Record a probe refused locally: no call was made (call_count = 0).

        Kept as its own row so the UI can explain *why* nothing was attempted,
        without inventing spend that did not happen.
        """
        probe = ModelProbe(
            model_id=model_id,
            provider_id=provider_id,
            initiated_by_user_id=initiated_by_user_id,
            request_id=request_id,
            started_at=utcnow(),
            outcome=ProbeOutcome.PENDING.value,
            config_fingerprint=config_fingerprint,
            call_count=0,
        )
        session.add(probe)
        session.flush()
        return probe

    def get(self, session: Session, probe_id: int) -> ModelProbe | None:
        return session.get(ModelProbe, probe_id)

    def recent_for_model(
        self, session: Session, model_id: int, limit: int = 10
    ) -> Sequence[ModelProbe]:
        return session.scalars(
            select(ModelProbe)
            .where(ModelProbe.model_id == model_id)
            .order_by(ModelProbe.started_at.desc(), ModelProbe.id.desc())
            .limit(limit)
        ).all()

    def recent(self, session: Session, limit: int = 20) -> Sequence[ModelProbe]:
        return session.scalars(
            select(ModelProbe)
            .order_by(ModelProbe.started_at.desc(), ModelProbe.id.desc())
            .limit(limit)
        ).all()

    def count_pending(self, session: Session) -> int:
        """How many probes were attempted but never completed.

        Surfaced in the model centre so an interrupted call is visible as
        "结果与用量未知" rather than silently absent (review finding R2).
        """
        return (
            session.scalar(
                select(func.count())
                .select_from(ModelProbe)
                .where(ModelProbe.outcome == ProbeOutcome.PENDING.value)
            )
            or 0
        )


class UsageRepository:
    def add(
        self,
        session: Session,
        *,
        source: str,
        model_id: int | None,
        provider_id: int | None,
        probe_id: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> ProviderUsageRecord:
        """Record what was reported, keeping per-dimension completeness.

        ``usage_known`` is derived here (every dimension present) rather than passed
        in, so an OR-ed partial report cannot be stored as a complete measurement
        (review finding R4).
        """
        prompt_known = prompt_tokens is not None
        completion_known = completion_tokens is not None
        record = ProviderUsageRecord(
            source=source,
            model_id=model_id,
            provider_id=provider_id,
            probe_id=probe_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usage_known=prompt_known and completion_known,
            prompt_tokens_known=prompt_known,
            completion_tokens_known=completion_known,
        )
        session.add(record)
        session.flush()
        return record

    def summarize(self, session: Session) -> dict[str, int | None]:
        """Probe usage totals for the model centre footer.

        Three states, because "we were told" and "we were not told" are different
        facts (review finding R4):

        * fully measured — every dimension reported;
        * partially measured — at least one dimension reported, at least one not;
        * unmeasured — nothing reported.

        Each dimension is summed only over rows that actually reported it, and the
        matching ``*_unreported_calls`` count says how many rows are missing from
        that number. A dimension with no reports stays ``None``, never 0.
        """
        rows = session.execute(
            select(
                ProviderUsageRecord.usage_known,
                ProviderUsageRecord.prompt_tokens_known,
                ProviderUsageRecord.completion_tokens_known,
                ProviderUsageRecord.prompt_tokens,
                ProviderUsageRecord.completion_tokens,
            ).where(ProviderUsageRecord.source == "PROBE")
        ).all()
        fully = [row for row in rows if row[0]]
        partially = [row for row in rows if not row[0] and (row[1] or row[2])]
        prompt_reported = [row[3] for row in rows if row[1] and row[3] is not None]
        completion_reported = [row[4] for row in rows if row[2] and row[4] is not None]
        return {
            "probe_calls": len(rows),
            "measured_calls": len(fully),
            "partially_measured_calls": len(partially),
            "unmeasured_calls": len(rows) - len(fully) - len(partially),
            "prompt_tokens": sum(prompt_reported) if prompt_reported else None,
            "completion_tokens": (sum(completion_reported) if completion_reported else None),
            "prompt_tokens_unreported_calls": len(rows) - len(prompt_reported),
            "completion_tokens_unreported_calls": len(rows) - len(completion_reported),
        }
