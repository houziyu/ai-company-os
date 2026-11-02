"""phase 02 review fixes: config versioning, probe evidence ownership, usage dimension

Adds the columns the independent review's R3, R4, R5, R7 findings require. All of
them are additive with server defaults, so existing rows stay valid and no data
backfill is needed beyond one derived value (``providers.config_fingerprint``).

What each change is for:

* ``providers.version`` / ``provider_models.version`` — optimistic concurrency for
  the configuration forms. A row lock serialises writes but cannot distinguish a
  stale form from a fresh one (R7).
* ``providers.config_fingerprint`` — a hash of exactly the fields that decide
  where and how a call is made (kind, base_url, secret_ref, timeout). Probe
  evidence is only meaningful for the configuration that produced it (R3).
* ``provider_models.probe_config_fingerprint`` — the fingerprint the capability
  fields (``supports_json_output``, ``resolved_name``, ``last_probe_*``) were
  learned under. When it no longer matches the provider, the evidence is stale.
* ``model_probes.config_fingerprint`` — the configuration a specific call used, so
  a late result from an old configuration cannot overwrite current capability.
* ``model_probes.outcome`` now also accepts ``PENDING``: the intent row is
  committed before the network call, and an interrupted probe stays PENDING
  ("result and usage unknown") instead of vanishing (R2).
* ``provider_usage_records.prompt_tokens_known`` / ``completion_tokens_known`` —
  per-dimension completeness, so a provider that reports only input tokens is not
  summarised as "fully measured" (R4). ``usage_known`` keeps its meaning of "all
  dimensions known".

Backfill: existing probe rows predate the fingerprint columns and are set to the
empty string, which never equals a real fingerprint (they are treated as stale,
which is the honest reading — they were taken under an unknown configuration).
Existing usage rows get the per-dimension flags from their stored values so the
historical summary does not change meaning.

Revision ID: 3f8d2c6a91b4
Revises: 7b41c9d2f0aa
Create Date: 2026-10-30 14:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3f8d2c6a91b4"
down_revision = "7b41c9d2f0aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- providers: optimistic lock + configuration fingerprint ---------------
    op.add_column(
        "providers",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "providers",
        sa.Column("config_fingerprint", sa.String(length=64), server_default="", nullable=False),
    )
    op.create_check_constraint(op.f("ck_providers_version_positive"), "providers", "version >= 1")

    # --- provider_models: optimistic lock + evidence ownership ----------------
    op.add_column(
        "provider_models",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "provider_models",
        sa.Column("probe_config_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_provider_models_version_positive"), "provider_models", "version >= 1"
    )

    # --- model_probes: PENDING outcome + the config a call used --------------
    op.add_column(
        "model_probes",
        sa.Column("config_fingerprint", sa.String(length=64), nullable=True),
    )
    op.drop_constraint(op.f("ck_model_probes_outcome_known"), "model_probes", type_="check")
    op.create_check_constraint(
        op.f("ck_model_probes_outcome_known"),
        "model_probes",
        "outcome IN ('PENDING', 'SUCCESS', 'FAILURE')",
    )
    op.create_index(
        "ix_model_probes_outcome_started",
        "model_probes",
        ["outcome", "started_at"],
        unique=False,
    )

    # --- provider_usage_records: per-dimension completeness ------------------
    op.add_column(
        "provider_usage_records",
        sa.Column("prompt_tokens_known", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "provider_usage_records",
        sa.Column("completion_tokens_known", sa.Boolean(), server_default="false", nullable=False),
    )
    # Historical rows: derive the flags from what was actually stored, and demote
    # usage_known to "all dimensions known" so the older OR-based semantics do not
    # keep a partial measurement looking complete.
    op.execute(
        "UPDATE provider_usage_records"
        " SET prompt_tokens_known = (prompt_tokens IS NOT NULL),"
        "     completion_tokens_known = (completion_tokens IS NOT NULL)"
    )
    op.execute(
        "UPDATE provider_usage_records"
        " SET usage_known = (prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_column("provider_usage_records", "completion_tokens_known")
    op.drop_column("provider_usage_records", "prompt_tokens_known")
    op.drop_index("ix_model_probes_outcome_started", table_name="model_probes")
    op.drop_constraint(op.f("ck_model_probes_outcome_known"), "model_probes", type_="check")
    op.create_check_constraint(
        op.f("ck_model_probes_outcome_known"),
        "model_probes",
        "outcome IN ('SUCCESS', 'FAILURE')",
    )
    op.drop_column("model_probes", "config_fingerprint")
    op.drop_constraint(
        op.f("ck_provider_models_version_positive"), "provider_models", type_="check"
    )
    op.drop_column("provider_models", "probe_config_fingerprint")
    op.drop_column("provider_models", "version")
    op.drop_constraint(op.f("ck_providers_version_positive"), "providers", type_="check")
    op.drop_column("providers", "config_fingerprint")
    op.drop_column("providers", "version")
