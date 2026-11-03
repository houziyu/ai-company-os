"""phase 03: projects, persistent task queue, executions, events and budgets

Additive schema only. Two things change on existing tables:

* ``companies.status`` gains ``PAUSED`` (Phase 03 company-wide pause) and two
  nullable pause columns. Existing rows are ``ACTIVE`` and stay valid.
* nothing else on Phase 01/02 tables is rewritten, and no Phase 02 migration is
  edited. Published revisions stay byte-identical.

New tables carry the queue, execution evidence, event feed and token ledger. All
amounts are BIGINT token counts; unknown usage is NULL, never 0.

Revision ID: c41f7b93e2a5
Revises: 8c24a71d9e03
Create Date: 2026-11-02 09:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c41f7b93e2a5"
down_revision = "8c24a71d9e03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- companies: PAUSED state -------------------------------------------
    op.add_column("companies", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("pause_reason", sa.String(length=200), nullable=True))
    # Constraint names here are passed *unprefixed*: Alembic applies the project
    # naming convention (``ck_%(table_name)s_%(constraint_name)s``) itself.
    op.drop_constraint("status_known", "companies", type_="check")
    op.create_check_constraint("status_known", "companies", "status IN ('ACTIVE', 'PAUSED')")
    # A paused company must record when; an active one must not claim a pause.
    op.create_check_constraint(
        "pause_pairing",
        "companies",
        "(status = 'PAUSED' AND paused_at IS NOT NULL)"
        " OR (status = 'ACTIVE' AND paused_at IS NULL)",
    )

    # --- projects -----------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("run_mode", sa.String(length=16), server_default="DEMO", nullable=False),
        sa.Column("budget_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_projects_status_known"),
        sa.CheckConstraint("run_mode IN ('DEMO', 'REAL')", name="ck_projects_run_mode_known"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_projects_name_not_blank"),
        sa.CheckConstraint("length(btrim(key)) > 0", name="ck_projects_key_not_blank"),
        sa.CheckConstraint("length(btrim(objective)) > 0", name="ck_projects_objective_not_blank"),
        sa.CheckConstraint("budget_tokens >= 0", name="ck_projects_budget_non_negative"),
        sa.CheckConstraint("version >= 1", name="ck_projects_version_positive"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_projects_company_id_companies",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("company_id", "key", name="uq_projects_company_key"),
        sa.UniqueConstraint("company_id", "name", name="uq_projects_company_name"),
    )
    op.create_index("ix_projects_company_status", "projects", ["company_id", "status"])

    # --- tasks --------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("assignee_agent_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("acceptance", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="QUEUED", nullable=False),
        sa.Column("demo_behavior", sa.String(length=32), server_default="SUCCESS", nullable=False),
        sa.Column("run_mode", sa.String(length=16), server_default="DEMO", nullable=False),
        sa.Column("budget_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_auto_retries", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fencing_counter", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error_code", sa.String(length=48), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'BLOCKED', 'SUCCEEDED',"
            " 'FAILED', 'CANCELLED', 'INTERRUPTED')",
            name="ck_tasks_status_known",
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_tasks_title_not_blank"),
        sa.CheckConstraint("length(btrim(objective)) > 0", name="ck_tasks_objective_not_blank"),
        sa.CheckConstraint("budget_tokens >= 0", name="ck_tasks_budget_non_negative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_non_negative"),
        sa.CheckConstraint(
            "max_auto_retries >= 0 AND max_auto_retries <= 5", name="ck_tasks_retry_bound"
        ),
        sa.CheckConstraint("fencing_counter >= 0", name="ck_tasks_fencing_counter_non_negative"),
        sa.CheckConstraint("version >= 1", name="ck_tasks_version_positive"),
        sa.ForeignKeyConstraint(
            ["assignee_agent_id"],
            ["agents.id"],
            ondelete="RESTRICT",
            name="fk_tasks_assignee_agent_id_agents",
        ),
        sa.ForeignKeyConstraint(
            ["cancel_requested_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_tasks_cancel_requested_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_tasks_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
            name="fk_tasks_project_id_projects",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.UniqueConstraint("company_id", "idempotency_key", name="uq_tasks_company_idempotency"),
    )
    op.create_index(
        "ix_tasks_project_status_created", "tasks", ["project_id", "status", "created_at"]
    )
    op.create_index("ix_tasks_company_status", "tasks", ["company_id", "status"])
    op.create_index("ix_tasks_queue", "tasks", ["status", "queued_at"])

    # --- executions ---------------------------------------------------------
    op.create_table(
        "task_executions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="RUNNING", nullable=False),
        sa.Column("run_mode", sa.String(length=16), nullable=False),
        sa.Column("demo_behavior", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=True),
        sa.Column("settled_tokens", sa.BigInteger(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("usage_known", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("needs_reconciliation", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "interrupted_unknown_side_effects",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("recovery_note", sa.String(length=300), nullable=True),
        sa.Column("retried_from_execution_id", sa.BigInteger(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'BLOCKED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED')",
            name="ck_task_executions_status_known",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_task_executions_attempt_positive"),
        sa.CheckConstraint("fencing_token >= 1", name="ck_task_executions_fencing_positive"),
        sa.CheckConstraint(
            "reserved_tokens IS NULL OR reserved_tokens >= 0",
            name="ck_task_executions_reserved_non_negative",
        ),
        sa.CheckConstraint(
            "settled_tokens IS NULL OR settled_tokens >= 0",
            name="ck_task_executions_settled_non_negative",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_task_executions_prompt_non_negative",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_task_executions_completion_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_task_executions_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
            name="fk_task_executions_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["retried_from_execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_task_executions_retried_from_execution_id_task_executions",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="RESTRICT",
            name="fk_task_executions_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_executions"),
        sa.UniqueConstraint("task_id", "attempt", name="uq_task_executions_task_attempt"),
    )
    op.create_index("ix_task_executions_task_started", "task_executions", ["task_id", "attempt"])
    op.create_index(
        "ix_task_executions_status_lease", "task_executions", ["status", "lease_expires_at"]
    )
    op.create_index("ix_task_executions_worker", "task_executions", ["worker_id", "status"])

    # --- execution steps ----------------------------------------------------
    op.create_table(
        "task_execution_steps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("execution_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="RUNNING", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.Column("usage_known", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_task_execution_steps_sequence_non_negative"),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED', 'INTERRUPTED')",
            name="ck_task_execution_steps_status_known",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_task_execution_steps_prompt_non_negative",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_task_execution_steps_completion_non_negative",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="ck_task_execution_steps_attempts_bounded"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_task_execution_steps_execution_id_task_executions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_execution_steps"),
        sa.UniqueConstraint("execution_id", "sequence", name="uq_task_execution_steps_sequence"),
    )
    op.create_index(
        "ix_task_execution_steps_execution_sequence",
        "task_execution_steps",
        ["execution_id", "sequence"],
    )

    # --- task timeline ------------------------------------------------------
    op.create_table(
        "task_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("execution_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("status_from", sa.String(length=16), nullable=True),
        sa.Column("status_to", sa.String(length=16), nullable=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(event_type)) > 0", name="ck_task_events_type_not_blank"),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_task_events_execution_id_task_executions",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="RESTRICT",
            name="fk_task_events_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_events"),
    )
    op.create_index("ix_task_events_task_created", "task_events", ["task_id", "created_at"])

    # --- SSE event feed -----------------------------------------------------
    op.create_table(
        "domain_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("execution_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(event_type)) > 0", name="ck_domain_events_type_not_blank"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_domain_events_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_domain_events_execution_id_task_executions",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
            name="fk_domain_events_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="RESTRICT",
            name="fk_domain_events_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_domain_events"),
    )
    op.create_index("ix_domain_events_created", "domain_events", ["created_at"])
    op.create_index("ix_domain_events_project", "domain_events", ["project_id", "id"])
    op.create_index("ix_domain_events_task", "domain_events", ["task_id", "id"])

    # --- structured messages ------------------------------------------------
    op.create_table(
        "task_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("execution_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_type", sa.String(length=16), nullable=False),
        sa.Column("sender_agent_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_user_id", sa.BigInteger(), nullable=True),
        sa.Column("receiver_type", sa.String(length=16), nullable=False),
        sa.Column("receiver_agent_id", sa.BigInteger(), nullable=True),
        sa.Column("message_type", sa.String(length=48), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "sender_type IN ('OWNER', 'AGENT', 'SYSTEM')",
            name="ck_task_messages_sender_type_known",
        ),
        sa.CheckConstraint(
            "receiver_type IN ('OWNER', 'AGENT', 'SYSTEM')",
            name="ck_task_messages_receiver_type_known",
        ),
        sa.CheckConstraint(
            "length(btrim(message_type)) > 0", name="ck_task_messages_type_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_task_messages_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_task_messages_execution_id_task_executions",
        ),
        sa.ForeignKeyConstraint(
            ["receiver_agent_id"],
            ["agents.id"],
            ondelete="RESTRICT",
            name="fk_task_messages_receiver_agent_id_agents",
        ),
        sa.ForeignKeyConstraint(
            ["sender_agent_id"],
            ["agents.id"],
            ondelete="RESTRICT",
            name="fk_task_messages_sender_agent_id_agents",
        ),
        sa.ForeignKeyConstraint(
            ["sender_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_task_messages_sender_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="RESTRICT",
            name="fk_task_messages_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_messages"),
    )
    op.create_index("ix_task_messages_task_created", "task_messages", ["task_id", "created_at"])
    op.create_index("ix_task_messages_correlation", "task_messages", ["correlation_id"])

    # --- budget accounts ----------------------------------------------------
    op.create_table(
        "budget_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("limit_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("consumed_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "scope IN ('COMPANY', 'PROJECT', 'TASK')", name="ck_budget_accounts_scope_known"
        ),
        sa.CheckConstraint("limit_tokens >= 0", name="ck_budget_accounts_limit_non_negative"),
        sa.CheckConstraint("consumed_tokens >= 0", name="ck_budget_accounts_consumed_non_negative"),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_budget_accounts_reserved_non_negative"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_budget_accounts_company_id_companies",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_budget_accounts"),
        sa.UniqueConstraint("scope", "scope_id", name="uq_budget_accounts_scope"),
    )
    op.create_index("ix_budget_accounts_scope", "budget_accounts", ["scope", "scope_id"])

    # --- reservations -------------------------------------------------------
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("execution_id", sa.BigInteger(), nullable=True),
        sa.Column("step_id", sa.BigInteger(), nullable=True),
        sa.Column("probe_id", sa.BigInteger(), nullable=True),
        sa.Column("owner_ref", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="RESERVED", nullable=False),
        sa.Column("amount_tokens", sa.BigInteger(), nullable=False),
        sa.Column("settled_tokens", sa.BigInteger(), nullable=True),
        sa.Column("estimated", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("needs_reconciliation", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('PLANNING', 'CALL', 'PROBE')",
            name="ck_budget_reservations_kind_known",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'SETTLED', 'RELEASED')",
            name="ck_budget_reservations_status_known",
        ),
        sa.CheckConstraint("owner_ref >= 1", name="ck_budget_reservations_owner_ref_positive"),
        sa.CheckConstraint("amount_tokens >= 0", name="ck_budget_reservations_amount_non_negative"),
        sa.CheckConstraint(
            "settled_tokens IS NULL OR settled_tokens >= 0",
            name="ck_budget_reservations_settled_non_negative",
        ),
        sa.CheckConstraint(
            "(kind <> 'PLANNING' OR task_id IS NOT NULL)"
            " AND (kind <> 'CALL' OR step_id IS NOT NULL)"
            " AND (kind <> 'PROBE' OR probe_id IS NOT NULL)",
            name="ck_budget_reservations_owner_present",
        ),
        sa.CheckConstraint(
            "(status = 'RESERVED' AND settled_tokens IS NULL)"
            " OR (status <> 'RESERVED' AND settled_tokens IS NOT NULL)",
            name="ck_budget_reservations_settled_only_when_resolved",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["budget_accounts.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_account_id_budget_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_execution_id_task_executions",
        ),
        sa.ForeignKeyConstraint(
            ["probe_id"],
            ["model_probes.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_probe_id_model_probes",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["task_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_step_id_task_execution_steps",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="RESTRICT",
            name="fk_budget_reservations_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_budget_reservations"),
    )
    # Partial uniqueness: one *open* hold per (account, kind, owner). A retried
    # task opens a fresh planning hold only after the previous one resolved.
    op.create_index(
        "uq_budget_reservations_open",
        "budget_reservations",
        ["account_id", "kind", "owner_ref"],
        unique=True,
        postgresql_where=sa.text("status = 'RESERVED'"),
    )
    op.create_index(
        "ix_budget_reservations_account_status", "budget_reservations", ["account_id", "status"]
    )
    op.create_index("ix_budget_reservations_task", "budget_reservations", ["task_id"])
    op.create_index("ix_budget_reservations_execution", "budget_reservations", ["execution_id"])

    # --- ledger -------------------------------------------------------------
    op.create_table(
        "budget_ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("execution_id", sa.BigInteger(), nullable=True),
        sa.Column("reservation_id", sa.BigInteger(), nullable=True),
        sa.Column("entry_kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("delta_reserved", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("delta_consumed", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("usage_tokens", sa.BigInteger(), nullable=True),
        sa.Column("usage_known", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("needs_reconciliation", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "entry_kind IN ('RESERVE', 'SETTLE', 'RELEASE')",
            name="ck_budget_ledger_entries_entry_kind_known",
        ),
        sa.CheckConstraint(
            "(entry_kind = 'RESERVE' AND delta_reserved > 0 AND delta_consumed = 0"
            " AND usage_tokens IS NULL)"
            # A settlement may report no usage: then it must be flagged for
            # reconciliation rather than stored as a zero-consumption entry.
            " OR (entry_kind = 'SETTLE' AND delta_reserved <= 0 AND delta_consumed >= 0"
            " AND (usage_tokens IS NOT NULL OR needs_reconciliation))"
            " OR (entry_kind = 'RELEASE' AND delta_reserved <= 0 AND delta_consumed = 0"
            " AND usage_tokens IS NULL)",
            name="ck_budget_ledger_entries_entry_shape",
        ),
        sa.CheckConstraint(
            "source IN ('TASK_CALL', 'PROBE')",
            name="ck_budget_ledger_entries_source_known",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["budget_accounts.id"],
            ondelete="RESTRICT",
            name="fk_budget_ledger_entries_account_id_budget_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
            name="fk_budget_ledger_entries_company_id_companies",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["task_executions.id"],
            ondelete="RESTRICT",
            name="fk_budget_ledger_entries_execution_id_task_executions",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="RESTRICT",
            name="fk_budget_ledger_entries_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["budget_reservations.id"],
            ondelete="RESTRICT",
            name="fk_budget_ledger_entries_reservation_id_budget_reservations",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="RESTRICT",
            name="fk_budget_ledger_entries_task_id_tasks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_budget_ledger_entries"),
        sa.UniqueConstraint(
            "account_id",
            "entry_kind",
            "reservation_id",
            name="uq_budget_ledger_entries_once",
        ),
    )
    op.create_index(
        "ix_budget_ledger_account_created", "budget_ledger_entries", ["account_id", "created_at"]
    )
    op.create_index(
        "ix_budget_ledger_company_created", "budget_ledger_entries", ["company_id", "created_at"]
    )
    op.create_index(
        "ix_budget_ledger_task_created", "budget_ledger_entries", ["task_id", "created_at"]
    )

    # --- provided usage records now distinguish probe vs task call ----------
    # Phase 02 constrained ``source`` to PROBE only. Phase 03 writes probe usage
    # through the ledger and keeps the row for the model centre summary, so the
    # constraint stays PROBE-only on purpose; task spend lives in the ledger.
    #
    # NOTE: no change is required here. Documented so the decision is explicit.

    # Existing companies must have a company budget account before any task can
    # be created. Create a zero limit account rather than inventing a limit: an
    # operator sets the real allowance, and a zero limit refuses spend loudly.
    bind.execute(
        sa.text(
            "INSERT INTO budget_accounts (scope, scope_id, company_id, limit_tokens,"
            " consumed_tokens, reserved_tokens, version)"
            " SELECT 'COMPANY', id, id, 0, 0, 0, 1 FROM companies"
            " ON CONFLICT (scope, scope_id) DO NOTHING"
        )
    )
    # Existing projects do not exist yet in this revision, so nothing else to seed.


def downgrade() -> None:
    op.drop_index("ix_budget_ledger_task_created", table_name="budget_ledger_entries")
    op.drop_index("ix_budget_ledger_company_created", table_name="budget_ledger_entries")
    op.drop_index("ix_budget_ledger_account_created", table_name="budget_ledger_entries")
    op.drop_table("budget_ledger_entries")
    op.drop_index("ix_budget_reservations_execution", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_task", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_account_status", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_index("ix_budget_accounts_scope", table_name="budget_accounts")
    op.drop_table("budget_accounts")
    op.drop_index("ix_task_messages_correlation", table_name="task_messages")
    op.drop_index("ix_task_messages_task_created", table_name="task_messages")
    op.drop_table("task_messages")
    op.drop_index("ix_domain_events_task", table_name="domain_events")
    op.drop_index("ix_domain_events_project", table_name="domain_events")
    op.drop_index("ix_domain_events_created", table_name="domain_events")
    op.drop_table("domain_events")
    op.drop_index("ix_task_events_task_created", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_execution_steps_execution_sequence", table_name="task_execution_steps")
    op.drop_table("task_execution_steps")
    op.drop_index("ix_task_executions_worker", table_name="task_executions")
    op.drop_index("ix_task_executions_status_lease", table_name="task_executions")
    op.drop_index("ix_task_executions_task_started", table_name="task_executions")
    op.drop_table("task_executions")
    op.drop_index("ix_tasks_queue", table_name="tasks")
    op.drop_index("ix_tasks_company_status", table_name="tasks")
    op.drop_index("ix_tasks_project_status_created", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_projects_company_status", table_name="projects")
    op.drop_table("projects")

    # Revert the company state additions. Any row that is PAUSED must be put back
    # to ACTIVE first, otherwise the old constraint would reject it; the pause
    # columns are dropped with the data, which is why this downgrade is only
    # safe on a database without live pause history.
    op.execute("UPDATE companies SET status='ACTIVE', paused_at=NULL, pause_reason=NULL")
    op.drop_constraint("pause_pairing", "companies", type_="check")
    op.drop_constraint("status_known", "companies", type_="check")
    op.create_check_constraint("status_known", "companies", "status IN ('ACTIVE')")
    op.drop_column("companies", "pause_reason")
    op.drop_column("companies", "paused_at")
