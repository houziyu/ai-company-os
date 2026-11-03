"""provider credentials: encrypted at rest (ADR-0006)

Adds ``providers.secret_ciphertext`` so the model centre can accept a key typed
by the operator, and widens ``secret_source`` to ``ENV | DB``.

Additive and non-destructive:

* Existing rows keep ``secret_source = 'ENV'`` and their ``secret_ref`` names;
  nothing is re-encrypted and no value is invented for them.
* No column can hold plaintext: the new column stores an AES-256-GCM envelope
  whose data key lives in ``SECRET_ENCRYPTION_KEY`` on the server.

Downgrade removes the column and cannot preserve stored credentials. It
therefore *refuses* while any row holds a stored key, rather than silently
destroying the only copy of a credential an operator cannot recover from
backup. Clear the stored keys through the UI (or SQL ``UPDATE ... SET
secret_ciphertext = NULL, secret_source = 'ENV', secret_ref = NULL``) first.

Revision ID: a7d2e5c81b64
Revises: f6c8d0e2b4a7
Create Date: 2026-11-02 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7d2e5c81b64"
down_revision = "f6c8d0e2b4a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("secret_ciphertext", sa.Text(), nullable=True))
    op.drop_constraint("secret_source_known", "providers", type_="check")
    op.create_check_constraint(
        "secret_source_known", "providers", "secret_source IN ('ENV', 'DB')"
    )
    op.create_check_constraint(
        "secret_source_single",
        "providers",
        "secret_ref IS NULL OR secret_ciphertext IS NULL",
    )
    op.create_check_constraint(
        "secret_ciphertext_requires_db",
        "providers",
        "secret_ciphertext IS NULL OR secret_source = 'DB'",
    )


def downgrade() -> None:
    # Refuse rather than destroy: the ciphertext is the only copy of a key that
    # this schema version cannot represent. The operator must clear it first.
    stored = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM providers WHERE secret_ciphertext IS NOT NULL")
    )
    if stored:
        raise RuntimeError(
            f"拒绝降级：仍有 {stored} 个供应商保存了加密密钥，降级会删除这些密文且无法恢复。"
            "请先在模型中心清除这些密钥（或在数据库中置空 secret_ciphertext），再执行降级。"
        )
    op.drop_constraint("secret_ciphertext_requires_db", "providers", type_="check")
    op.drop_constraint("secret_source_single", "providers", type_="check")
    op.drop_constraint("secret_source_known", "providers", type_="check")
    op.create_check_constraint("secret_source_known", "providers", "secret_source IN ('ENV')")
    op.drop_column("providers", "secret_ciphertext")
