"""phase 03 fixes: commit-ordered publication cursor for the SSE feed

Additive only. ``domain_events.published_seq`` is the replay cursor that orders
events by *commit visibility* instead of sequence allocation (review finding R8).

Existing rows are backfilled in ``id`` order. That preserves today's order for
history and gives every already-committed row a cursor, so a client with an old
``Last-Event-ID`` keeps working: the migration guarantees
``published_seq(id_a) < published_seq(id_b)`` whenever ``id_a < id_b`` for all
rows that existed before it ran.

Nothing is dropped or rewritten otherwise; downgrade removes the column and its
indexes and the feed falls back to id order (the pre-fix behaviour).

Revision ID: e5b7c9d1a2f4
Revises: c41f7b93e2a5
Create Date: 2026-11-02 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5b7c9d1a2f4"
down_revision = "c41f7b93e2a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("domain_events", sa.Column("published_seq", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "published_seq_positive", "domain_events", "published_seq IS NULL OR published_seq >= 1"
    )
    # Backfill in primary-key order so historical rows keep their relative order
    # and a stored cursor from before the upgrade still resumes correctly.
    op.execute(
        """
        UPDATE domain_events AS d
        SET published_seq = ordered.seq
        FROM (
            SELECT id, row_number() OVER (ORDER BY id) AS seq
            FROM domain_events
        ) AS ordered
        WHERE d.id = ordered.id
        """
    )
    # Bring the cursor space past the backfilled values: the publisher derives
    # the next cursor from ``max(published_seq)``, so history is never reused.
    op.create_index(
        "uq_domain_events_published_seq",
        "domain_events",
        ["published_seq"],
        unique=True,
        postgresql_where=sa.text("published_seq IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_events_unpublished",
        "domain_events",
        ["id"],
        postgresql_where=sa.text("published_seq IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_domain_events_unpublished", table_name="domain_events")
    op.drop_index("uq_domain_events_published_seq", table_name="domain_events")
    # Unprefixed for the same reason as ``create_check_constraint`` above:
    # Alembic applies the naming convention to both directions, so passing the
    # already-prefixed name would look for ``ck_domain_events_ck_domain_events_...``.
    op.drop_constraint("published_seq_positive", "domain_events", type_="check")
    op.drop_column("domain_events", "published_seq")
