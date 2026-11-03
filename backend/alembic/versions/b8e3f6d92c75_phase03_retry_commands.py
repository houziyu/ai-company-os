"""Persist accepted retry commands without changing existing task evidence.

Revision ID: b8e3f6d92c75
Revises: a7d2e5c81b64
"""

import sqlalchemy as sa
from alembic import op

revision = "b8e3f6d92c75"
down_revision = "a7d2e5c81b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_retry_commands",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("target_attempt", sa.Integer(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("company_id", "idempotency_key", name="uq_task_retry_commands_key"),
        sa.CheckConstraint("length(btrim(idempotency_key)) > 0", name="key_not_blank"),
        sa.CheckConstraint("target_attempt >= 1", name="target_attempt_positive"),
    )


def downgrade() -> None:
    # Silently deleting accepted keys would make network retries execute again.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM task_retry_commands)")):
        raise RuntimeError(
            "Retry history exists; retain this schema or restore a verified pre-upgrade backup."
        )
    op.drop_table("task_retry_commands")
