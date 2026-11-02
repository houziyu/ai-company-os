"""phase 02 company, organization and model centre

Creates the tables for the single company, its instantiated organization and the
model centre, and seeds the read-only technology-company template.

Design decisions worth stating in the migration itself:

* ``companies`` is keyed on ``id = 1`` so a second company is impossible at the
  database level. The row is *not* inserted here: the company does not exist
  until the owner completes the wizard, and ``GET /company`` must be able to
  answer 404 honestly.
* ``company_template_positions`` is reference data. Seeding it in the migration
  (rather than at import time) means a fresh database and an upgraded database
  hold byte-identical template content, and the app never writes to it.
* ``positions_versions`` and ``agent_duty_override_history`` are append-only by
  design; nothing cascades on delete, so an organization's history cannot be
  removed by deleting an employee or position.

Revision ID: 7b41c9d2f0aa
Revises: 6a790d8c2b88
Create Date: 2026-10-30 11:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b41c9d2f0aa"
down_revision = "6a790d8c2b88"
branch_labels = None
depends_on = None

# Seeded from app.domain.company_template; kept literal here so the migration is
# a stable historical artifact even if the Python module evolves later.
TEMPLATE_ROWS: tuple[dict[str, object], ...] = (
    {
        "template_key": "tech-company",
        "template_version": 1,
        "code": "CEO",
        "title": "CEO",
        "default_agent_name": "首席执行官",
        "duty": (
            "设定公司目标与优先级，裁决跨部门冲突，对超出部门负责人权限的决策负责。"
            "不直接领取实现任务，除非明确说明原因。"
        ),
        "reports_to_code": None,
        "sort_order": 10,
        "max_reports": 10,
    },
    {
        "template_key": "tech-company",
        "template_version": 1,
        "code": "PRODUCT_MANAGER",
        "title": "产品经理",
        "default_agent_name": "产品经理",
        "duty": (
            "把公司目标拆成可验收的产品需求，维护需求优先级与验收标准，"
            "对“做什么、为什么做”负责，不替代技术负责人做实现决策。"
        ),
        "reports_to_code": "CEO",
        "sort_order": 20,
        "max_reports": 0,
    },
    {
        "template_key": "tech-company",
        "template_version": 1,
        "code": "TECH_LEAD",
        "title": "技术负责人/开发",
        "default_agent_name": "技术负责人",
        "duty": (
            "负责技术方案、代码实现与技术评审，把需求转化为可测试的变更，"
            "并对实现质量与可维护性负责。"
        ),
        "reports_to_code": "CEO",
        "sort_order": 30,
        "max_reports": 10,
    },
    {
        "template_key": "tech-company",
        "template_version": 1,
        "code": "QA",
        "title": "QA",
        "default_agent_name": "质量工程师",
        "duty": (
            "独立验证交付是否满足验收标准，编写与运行测试，报告可复现的缺陷，"
            "不因为实现者自称完成而判定通过。"
        ),
        "reports_to_code": "TECH_LEAD",
        "sort_order": 40,
        "max_reports": 0,
    },
    {
        "template_key": "tech-company",
        "template_version": 1,
        "code": "DEVOPS",
        "title": "DevOps",
        "default_agent_name": "DevOps 工程师",
        "duty": (
            "维护构建、部署、可观测性与环境配置，保证变更可以安全发布与回滚，"
            "不获得超出部署需要的凭据。"
        ),
        "reports_to_code": "TECH_LEAD",
        "sort_order": 50,
        "max_reports": 0,
    },
)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column(
            "initialized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("initialized_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("initialization_idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("initialization_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_companies_single_company")),
        sa.CheckConstraint("status IN ('ACTIVE')", name=op.f("ck_companies_status_known")),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_companies_name_not_blank")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_companies_version_positive")),
        sa.ForeignKeyConstraint(
            ["initialized_by_user_id"],
            ["users.id"],
            name=op.f("fk_companies_initialized_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )

    op.create_table(
        "company_template_positions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("default_agent_name", sa.String(length=80), nullable=False),
        sa.Column("duty", sa.Text(), nullable=False),
        sa.Column("reports_to_code", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("max_reports", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "sort_order >= 0", name=op.f("ck_company_template_positions_sort_order_non_negative")
        ),
        sa.CheckConstraint(
            "max_reports >= 0", name=op.f("ck_company_template_positions_max_reports_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_template_positions")),
        sa.UniqueConstraint(
            "template_key",
            "template_version",
            "code",
            name="unique_template_code",
        ),
    )
    template_table = sa.table(
        "company_template_positions",
        sa.column("template_key", sa.String),
        sa.column("template_version", sa.Integer),
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("default_agent_name", sa.String),
        sa.column("duty", sa.Text),
        sa.column("reports_to_code", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("max_reports", sa.Integer),
    )
    op.bulk_insert(template_table, list(TEMPLATE_ROWS))

    op.create_table(
        "departments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_departments_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_departments_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("company_id", "code", name="uq_departments_company_code"),
    )

    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_version >= 1", name=op.f("ck_positions_current_version_positive")
        ),
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_positions_row_version_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_positions_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_positions_department_id_departments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions")),
        sa.UniqueConstraint("company_id", "code", name="uq_positions_company_code"),
    )

    op.create_table(
        "position_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("duty", sa.Text(), nullable=False),
        sa.Column("changed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_position_versions_version_positive")),
        sa.CheckConstraint(
            "length(btrim(duty)) > 0", name=op.f("ck_position_versions_duty_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_position_versions_position_id_positions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            name=op.f("fk_position_versions_changed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position_versions")),
        sa.UniqueConstraint("position_id", "version", name="uq_position_versions_position_version"),
    )
    op.create_index(
        "ix_position_versions_position",
        "position_versions",
        ["position_id", "version"],
        unique=False,
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("manager_id", sa.BigInteger(), nullable=True),
        sa.Column("duty_override", sa.Text(), nullable=True),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'PAUSED', 'ARCHIVED')", name=op.f("ck_agents_status_known")
        ),
        sa.CheckConstraint(
            "manager_id IS NULL OR manager_id <> id", name=op.f("ck_agents_no_self_management")
        ),
        sa.CheckConstraint(
            "length(btrim(display_name)) > 0", name=op.f("ck_agents_display_name_not_blank")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agents_version_positive")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_agents_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_agents_position_id_positions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["agents.id"],
            name=op.f("fk_agents_manager_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("company_id", "employee_code", name="uq_agents_company_employee_code"),
        sa.UniqueConstraint("position_id", name="uq_agents_position_id"),
    )
    op.create_index("ix_agents_company_status", "agents", ["company_id", "status"], unique=False)
    op.create_index("ix_agents_manager", "agents", ["manager_id"], unique=False)

    op.create_table(
        "agent_duty_override_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("duty", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_duty_override_history_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_duty_override_history_changed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_duty_override_history")),
    )
    op.create_index(
        "ix_agent_duty_history_agent",
        "agent_duty_override_history",
        ["agent_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "providers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=False),
        sa.Column("secret_ref", sa.String(length=128), nullable=True),
        sa.Column("secret_source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), server_default="30", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('DEEPSEEK', 'OPENAI_COMPATIBLE')", name=op.f("ck_providers_kind_known")
        ),
        sa.CheckConstraint(
            "status IN ('ENABLED', 'DISABLED')", name=op.f("ck_providers_status_known")
        ),
        sa.CheckConstraint(
            "secret_source IN ('ENV')", name=op.f("ck_providers_secret_source_known")
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_providers_name_not_blank")),
        sa.CheckConstraint("base_url ~ '^https?://'", name=op.f("ck_providers_base_url_scheme")),
        sa.CheckConstraint(
            "secret_ref IS NULL OR secret_ref ~ '^[A-Z][A-Z0-9_]*$'",
            name=op.f("ck_providers_secret_ref_env_name"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
        sa.UniqueConstraint("name", name="uq_providers_name"),
    )

    op.create_table(
        "provider_models",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("logical_name", sa.String(length=160), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("supports_json_output", sa.Boolean(), nullable=True),
        sa.Column("resolved_name", sa.String(length=160), nullable=True),
        sa.Column("last_probe_outcome", sa.String(length=16), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_detail", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ENABLED', 'DISABLED')", name=op.f("ck_provider_models_status_known")
        ),
        sa.CheckConstraint(
            "length(btrim(logical_name)) > 0",
            name=op.f("ck_provider_models_logical_name_not_blank"),
        ),
        sa.CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens > 0",
            name=op.f("ck_provider_models_output_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_provider_models_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_models")),
        sa.UniqueConstraint("provider_id", "logical_name", name="uq_provider_models_provider_name"),
    )
    op.create_index("ix_provider_models_provider", "provider_models", ["provider_id"], unique=False)

    op.create_table(
        "agent_model_assignments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_agent_model_assignments_range_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_model_assignments_agent_id_agents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["provider_models.id"],
            name=op.f("fk_agent_model_assignments_model_id_provider_models"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_model_assignments_assigned_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_model_assignments")),
    )
    op.create_index(
        "uq_agent_model_assignments_open",
        "agent_model_assignments",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_agent_model_assignments_agent",
        "agent_model_assignments",
        ["agent_id", "started_at"],
        unique=False,
    )

    op.create_table(
        "model_probes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("initiated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("json_output_verified", sa.Boolean(), nullable=True),
        sa.Column("resolved_model_name", sa.String(length=160), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "call_count >= 0 AND call_count <= 1", name=op.f("ck_model_probes_single_call")
        ),
        sa.CheckConstraint(
            "outcome IN ('SUCCESS', 'FAILURE')", name=op.f("ck_model_probes_outcome_known")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["provider_models.id"],
            name=op.f("fk_model_probes_model_id_provider_models"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_model_probes_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"],
            ["users.id"],
            name=op.f("fk_model_probes_initiated_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_probes")),
    )
    op.create_index(
        "ix_model_probes_model_started", "model_probes", ["model_id", "started_at"], unique=False
    )

    op.create_table(
        "provider_usage_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_id", sa.BigInteger(), nullable=True),
        sa.Column("probe_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("usage_known", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('PROBE')", name=op.f("ck_provider_usage_records_source_known")
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name=op.f("ck_provider_usage_records_prompt_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name=op.f("ck_provider_usage_records_completion_tokens_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["provider_models.id"],
            name=op.f("fk_provider_usage_records_model_id_provider_models"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_provider_usage_records_provider_id_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["probe_id"],
            ["model_probes.id"],
            name=op.f("fk_provider_usage_records_probe_id_model_probes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_usage_records")),
    )
    op.create_index(
        "ix_provider_usage_source_created",
        "provider_usage_records",
        ["source", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_usage_source_created", table_name="provider_usage_records")
    op.drop_table("provider_usage_records")
    op.drop_index("ix_model_probes_model_started", table_name="model_probes")
    op.drop_table("model_probes")
    op.drop_index("ix_agent_model_assignments_agent", table_name="agent_model_assignments")
    op.drop_index(
        "uq_agent_model_assignments_open",
        table_name="agent_model_assignments",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_table("agent_model_assignments")
    op.drop_index("ix_provider_models_provider", table_name="provider_models")
    op.drop_table("provider_models")
    op.drop_table("providers")
    op.drop_index("ix_agent_duty_history_agent", table_name="agent_duty_override_history")
    op.drop_table("agent_duty_override_history")
    op.drop_index("ix_agents_manager", table_name="agents")
    op.drop_index("ix_agents_company_status", table_name="agents")
    op.drop_table("agents")
    op.drop_index("ix_position_versions_position", table_name="position_versions")
    op.drop_table("position_versions")
    op.drop_table("positions")
    op.drop_table("departments")
    op.drop_table("company_template_positions")
    op.drop_table("companies")
