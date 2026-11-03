"""Re-publish history above both legacy cursor domains without losing events.

Revision ID: f6c8d0e2b4a7
Revises: e5b7c9d1a2f4

Old clients stored event primary keys; e5 clients stored publication sequences.
Those domains overlap, so their cursors cannot be distinguished reliably. Move
all delivery metadata above both high-water marks, preserving publication order.
Existing clients replay history once (at-least-once delivery); payloads, event
IDs, timestamps and all business/ledger rows stay untouched. See ADR-0005.
"""

from alembic import op

revision = "f6c8d0e2b4a7"
down_revision = "e5b7c9d1a2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Same lock order as the publisher, then block concurrent event inserts.
    op.execute("SELECT pg_advisory_xact_lock(1094930259)")
    op.execute("LOCK TABLE domain_events IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        WITH boundary AS (
            SELECT GREATEST(COALESCE(max(id), 0), COALESCE(max(published_seq), 0)) AS floor
            FROM domain_events
        ), ordered AS (
            SELECT id, row_number() OVER (ORDER BY published_seq NULLS LAST, id) AS ordinal
            FROM domain_events
        )
        UPDATE domain_events AS d
        SET published_seq = boundary.floor + ordered.ordinal
        FROM ordered, boundary
        WHERE d.id = ordered.id
        """
    )


def downgrade() -> None:
    # Data-only correction: moving cursors backwards would lose delivery again.
    # The e5 schema and application accept the corrected values unchanged.
    pass
