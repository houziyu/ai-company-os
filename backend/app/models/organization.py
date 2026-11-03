"""Phase 02 tables: company, organization and model centre.

Design notes
------------
* **Single company.** ``companies`` has a check constraint on ``id = 1`` for the
  same reason ``platform_state`` does: the product is single-company, and a
  unique index would allow gaps (2, 3, ...) while a fixed key makes a second row
  physically impossible. The row is *not* created by the migration — the company
  wizard creates it — so ``GET /company`` legitimately returns 404 before setup.
* **Template vs. instance.** ``company_template_positions`` is reference data
  owned by the migration (seeded rows, no runtime writes). Instantiating a
  company copies it into ``positions`` + ``positions_versions`` + ``agents``, so
  later template edits never rewrite a customer's organization.
* **Position duty vs. instance override.** ``positions_versions`` holds the
  versioned responsibility text and the default title. ``agents`` holds the
  employee's own display name, model binding and status; an employee's duty text
  override lives in ``agent_duty_overrides`` (append-only history) so a duty
  change never erases what the position originally said or who said what before.
* **Management relation** is ``agents.manager_id`` (self-referencing FK). A
  cycle check runs in the service inside the same transaction that writes, and
  ``CHECK (manager_id IS NULL OR manager_id <> id)`` blocks the trivial case at
  the database level. Management never grants resource permissions: no
  authorization code reads ``manager_id``.
* **Secrets.** ``providers.secret_ref`` (Phase 02) stores an *environment
  variable name*; ``providers.secret_ciphertext`` (ADR-0006) stores a directly
  typed credential encrypted with a server-held AES-GCM key. A row has at most
  one of the two, enforced by a check constraint; no column ever holds plaintext
  and no read path returns a value.
* **Usage.** ``provider_usage_records`` records one row per probe call that
  actually reached the provider, keeping probe spend separately attributable
  before Phase 03 introduces the unified budget ledger.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Company(Base, TimestampMixin):
    """The single company. Created by the initialization wizard, never by DDL."""

    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_company"),
        CheckConstraint("status IN ('ACTIVE', 'PAUSED')", name="status_known"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    # Phase 03: company-wide pause stops new paid calls and new task creation.
    # It is an operational state, not a deletion: history is untouched.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Which template produced this company; kept even if the template changes.
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    initialized_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Optimistic concurrency for company-level edits.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Idempotency of the one-shot creation command: a retried wizard submit with
    # the same key and the same body returns the existing company instead of a
    # misleading 409. A different body under the same key is refused.
    initialization_idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    initialization_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CompanyTemplatePosition(Base):
    """Reference data describing the default five-role technology company.

    Rows are seeded by the migration and read-only at runtime. ``sort_order``
    fixes the display order so the org chart is stable across renders.
    """

    __tablename__ = "company_template_positions"
    __table_args__ = (
        UniqueConstraint("template_key", "template_version", "code", name="unique_template_code"),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
        CheckConstraint("max_reports >= 0", name="max_reports_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    default_agent_name: Mapped[str] = mapped_column(String(80), nullable=False)
    duty: Mapped[str] = mapped_column(Text, nullable=False)
    reports_to_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # Informational limit used by later phases; Phase 02 does not enforce hiring.
    max_reports: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Department(Base, TimestampMixin):
    """A grouping unit. Phase 02 instantiates one from the template."""

    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_departments_company_code"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)


class Position(Base, TimestampMixin):
    """A job slot. The current duty snapshot is ``current_version`` (1-based).

    ``row_version`` is a separate optimistic-locking counter bumped on *every*
    accepted edit, including a title-only change. Reusing ``current_version`` for
    that purpose would let two clients that each only renamed the position
    overwrite one another, because a rename does not append a version row.
    """

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_positions_company_code"),
        CheckConstraint("current_version >= 1", name="current_version_positive"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class PositionVersion(Base):
    """Immutable snapshot of a position's duty text.

    A duty change appends a new version; it never updates an old row. Historical
    executions will point at the version that was in force when they ran.
    """

    __tablename__ = "position_versions"
    __table_args__ = (
        UniqueConstraint("position_id", "version", name="uq_position_versions_position_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(btrim(duty)) > 0", name="duty_not_blank"),
        Index("ix_position_versions_position", "position_id", "version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("positions.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    duty: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Agent(Base, TimestampMixin):
    """An employee instance occupying a position.

    ``employee_code`` is a stable, human-readable identifier written once at
    hire time. It survives status changes, model rebinding and duty overrides so
    audit records and future executions stay attributable.
    """

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("company_id", "employee_code", name="uq_agents_company_employee_code"),
        UniqueConstraint("position_id", name="uq_agents_position_id"),
        CheckConstraint("status IN ('ACTIVE', 'PAUSED', 'ARCHIVED')", name="status_known"),
        CheckConstraint("manager_id IS NULL OR manager_id <> id", name="no_self_management"),
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_not_blank"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_agents_company_status", "company_id", "status"),
        Index("ix_agents_manager", "manager_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("positions.id", ondelete="RESTRICT"), nullable=False
    )
    employee_code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    # Management relation only. It never grants resource permissions.
    manager_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
    )
    # The employee's own duty override; null means "use the position version".
    duty_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optimistic concurrency for concurrent edits from two browsers.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class AgentDutyOverrideHistory(Base):
    """Append-only record of every duty override an employee ever had.

    Keeping history here (instead of overwriting ``agents.duty_override``) is what
    makes "职责变更保留履历" verifiable.
    """

    __tablename__ = "agent_duty_override_history"
    __table_args__ = (Index("ix_agent_duty_history_agent", "agent_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    duty: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Provider(Base, TimestampMixin):
    """A model vendor endpoint.

    ``secret_source`` says how the credential is supplied and is authoritative:

    * ``ENV`` — ``secret_ref`` holds an environment variable *name* (Phase 02).
    * ``DB``  — ``secret_ciphertext`` holds an AES-GCM envelope (ADR-0006).

    At most one of the two columns is non-null, so "where does the key come
    from" is never ambiguous. Neither column can hold plaintext.
    """

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("name", name="uq_providers_name"),
        CheckConstraint("kind IN ('DEEPSEEK', 'OPENAI_COMPATIBLE')", name="kind_known"),
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="status_known"),
        CheckConstraint("secret_source IN ('ENV', 'DB')", name="secret_source_known"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("base_url ~ '^https?://'", name="base_url_scheme"),
        CheckConstraint(
            "secret_ref IS NULL OR secret_ref ~ '^[A-Z][A-Z0-9_]*$'",
            name="secret_ref_env_name",
        ),
        # One credential source per row: never both, and a stored ciphertext only
        # under the DB source. The reverse (DB with no blob) is legitimate: it is
        # how "clear the stored key" is represented without inventing a third
        # source value.
        CheckConstraint(
            "secret_ref IS NULL OR secret_ciphertext IS NULL",
            name="secret_source_single",
        ),
        CheckConstraint(
            "secret_ciphertext IS NULL OR secret_source = 'DB'",
            name="secret_ciphertext_requires_db",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # AES-256-GCM envelope ``v1.<kid>.<nonce>.<ciphertext>``; the data key is in
    # the server environment, never here (ADR-0006).
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_source: Mapped[str] = mapped_column(String(16), nullable=False, default="ENV")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED")
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timeout_seconds: Mapped[float] = mapped_column(nullable=False, server_default="30")
    # Optimistic-locking counter for the configuration form. Without it two
    # operators editing the same provider silently overwrite each other, because
    # a row lock only serialises writes; it cannot tell a stale form from a fresh
    # one (review finding R7).
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Fingerprint of the fields that determine *where and how* a call is made
    # (kind, base_url, credential marker, timeout). Probe evidence is only valid
    # for the configuration that produced it: when this changes, previously
    # verified capability no longer describes the current setup (review finding
    # R3). The credential marker is the env-var name for ENV rows and a hash of
    # the ciphertext for DB rows (ADR-0006) — never the credential itself.
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")


class Model(Base, TimestampMixin):
    """A logical model name available at a provider.

    ``logical_name`` is what the operator configures (e.g. ``deepseek-v4.1-flash``).
    ``resolved_name`` is whatever the provider reported back, when it does; a
    provider that never reports the concrete revision leaves it null, and the UI
    says "未知" instead of guessing.
    """

    __tablename__ = "provider_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "logical_name", name="uq_provider_models_provider_name"),
        CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="status_known"),
        CheckConstraint("length(btrim(logical_name)) > 0", name="logical_name_not_blank"),
        CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens > 0", name="output_positive"
        ),
        Index("ix_provider_models_provider", "provider_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    logical_name: Mapped[str] = mapped_column(String(160), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ENABLED")
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Optimistic-locking counter, same reason as providers.version.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Facts learned from a probe, never assumed. All null until a probe succeeds.
    supports_json_output: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The provider config fingerprint that the capability fields above describe.
    # When it differs from the provider's current fingerprint, the evidence is
    # stale and the UI must report "未验证" rather than the old result.
    probe_config_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_probe_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_detail: Mapped[str | None] = mapped_column(String(300), nullable=True)


class AgentModelAssignment(Base, TimestampMixin):
    """Current model binding for an employee.

    Bindings are versioned by history rather than overwritten in place: a model
    change appends a new row and closes the previous one with ``ended_at``. That
    keeps "改绑后旧执行保持历史配置" true once Phase 05 records executions
    against a binding id.
    """

    __tablename__ = "agent_model_assignments"
    __table_args__ = (
        # At most one open binding per agent.
        Index(
            "uq_agent_model_assignments_open",
            "agent_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="range_ordered"),
        Index("ix_agent_model_assignments_agent", "agent_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_models.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ModelProbe(Base, TimestampMixin):
    """One connectivity probe against one model.

    Two-phase persistence (review findings R2 and R5):

    1. The *intent* row is committed in its own short transaction before the
       network call, with ``outcome = 'PENDING'`` and ``call_count = 1``. A
       process that dies mid-call therefore leaves evidence that a paid request
       may have happened.
    2. The result is written in a second transaction. If that never happens, the
       row stays PENDING and is read as "结果与用量未知" — never as success, never
       as a zero cost, and never automatically retried.

    ``call_count`` is always <= 1: probes never retry.
    """

    __tablename__ = "model_probes"
    __table_args__ = (
        CheckConstraint("call_count >= 0 AND call_count <= 1", name="single_call"),
        CheckConstraint("outcome IN ('PENDING', 'SUCCESS', 'FAILURE')", name="outcome_known"),
        Index("ix_model_probes_model_started", "model_id", "started_at"),
        Index("ix_model_probes_outcome_started", "outcome", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_models.id", ondelete="RESTRICT"), nullable=False
    )
    provider_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False
    )
    initiated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The provider configuration that this call actually used. A result recorded
    # against a config that has since changed must not update current capability.
    config_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sanitized: a fixed sentence for the category, never the provider's body.
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    json_output_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ProviderUsageRecord(Base):
    """Token usage attributable to one probe.

    Phase 03 will introduce the unified budget ledger; until then this table
    keeps probe cost separately visible so it is never silently folded into a
    task budget or reported as zero.
    """

    __tablename__ = "provider_usage_records"
    __table_args__ = (
        CheckConstraint("source IN ('PROBE')", name="source_known"),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0", name="prompt_tokens_non_negative"
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="completion_tokens_non_negative",
        ),
        Index("ix_provider_usage_source_created", "source", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    model_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("provider_models.id", ondelete="RESTRICT"), nullable=True
    )
    provider_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("providers.id", ondelete="RESTRICT"), nullable=True
    )
    probe_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("model_probes.id", ondelete="RESTRICT"), nullable=True
    )
    # Nullable on purpose: an unknown count must stay unknown, never 0.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True only when every dimension is known. A partial report is *not* a
    # complete measurement (review finding R4); the two flags below keep which
    # dimension was actually reported.
    usage_known: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    prompt_tokens_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    completion_tokens_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
