"""Repair historical provider fingerprints and invalidate unowned capabilities.

Revision ID: 8c24a71d9e03
Revises: 3f8d2c6a91b4

Data-only migration. Probe history, reported usage, and audit rows are preserved.
The fingerprint algorithm is frozen here rather than importing live app code.
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "8c24a71d9e03"
down_revision = "3f8d2c6a91b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    providers = (
        connection.execute(
            sa.text("SELECT id, kind, base_url, secret_ref, timeout_seconds FROM providers")
        )
        .mappings()
        .all()
    )
    for provider in providers:
        payload = {
            "kind": provider["kind"],
            "baseUrl": provider["base_url"].rstrip("/"),
            "secretRef": provider["secret_ref"] or "",
            "timeoutSeconds": round(float(provider["timeout_seconds"]), 3),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        parameters = {"id": provider["id"], "fingerprint": fingerprint}
        connection.execute(
            sa.text("UPDATE providers SET config_fingerprint=:fingerprint WHERE id=:id"),
            parameters,
        )
        connection.execute(
            sa.text(
                "UPDATE provider_models SET supports_json_output=NULL, resolved_name=NULL,"
                " last_probe_outcome=NULL, probe_config_fingerprint=NULL,"
                " last_probe_detail='配置验证归属未知，请重新测试连接。'"
                " WHERE provider_id=:id AND probe_config_fingerprint IS DISTINCT FROM :fingerprint"
            ),
            parameters,
        )


def downgrade() -> None:
    # No schema change. Keep repaired fingerprints and do not resurrect invalid
    # capability claims. Original probe/usage/audit history is still available.
    pass
