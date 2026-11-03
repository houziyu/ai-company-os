"""Commit-ordered publication cursor for the SSE feed.

Why the primary key is not a cursor
-----------------------------------
``domain_events.id`` comes from a sequence and is allocated at INSERT time, so it
orders *allocation*, not *commit*. Two workers can claim two tasks at once: the
first claim gets id 4, the second id 5, and the second transaction may commit
first. A stream that starts after 5 and stores 5 as its cursor would then never
see 4 — the row is committed and visible, but the cursor has moved past it.

What is used instead
--------------------
``domain_events.published_seq`` is assigned only to rows a publisher can already
see, i.e. rows that are committed. A row that commits late therefore receives a
*higher* cursor than everything delivered before it and is replayed on the next
poll. Ordering is:

* a transaction-scoped advisory lock serialises publishers, so two publishers
  cannot interleave cursor assignment;
* visible rows are numbered in id order inside that lock;
* the unique partial index on ``published_seq`` makes a double assignment a
  database error rather than a silent duplicate.

Publishing is deliberately not done inside business transactions: the publisher
must see *other* transactions' commits, and waiting for them while holding a
business lock would be a deadlock source. The SSE poll calls
:meth:`EventFeedService.publish_pending` before reading, so a new event appears
within one poll interval and never out of the safe order.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.tasks import DomainEvent

logger = logging.getLogger(__name__)

# Arbitrary but fixed application-wide key for ``pg_advisory_xact_lock``. Using a
# single key means exactly one publisher at a time, which is what makes the
# cursor order equal to the assignment order.
PUBLISH_LOCK_KEY = 0x41434F53  # "ACOS"

PUBLISH_BATCH = 500


class EventFeedService:
    """Assigns publication cursors and reads the ordered feed."""

    def publish_pending(self, session: Session, *, limit: int = PUBLISH_BATCH) -> int:
        """Number visible-but-unpublished events, oldest insertion first.

        Returns how many rows were published. Safe to call repeatedly and
        concurrently: the advisory lock serialises the read-modify-write and the
        row lock keeps a second publisher from touching the same rows.
        """
        # Transaction-scoped: released on commit/rollback, never leaked.
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PUBLISH_LOCK_KEY})

        ids = list(
            session.scalars(
                select(DomainEvent.id)
                .where(DomainEvent.published_seq.is_(None))
                .order_by(DomainEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if not ids:
            return 0

        # One statement assigns the next values in the id order selected above.
        # ``WITH ORDINALITY`` turns the array into (id, position) pairs so the
        # sequence values follow the same order explicitly.
        session.execute(
            text(
                """
                WITH ordered AS (
                    SELECT value AS id, ordinality
                    FROM unnest(CAST(:ids AS bigint[])) WITH ORDINALITY AS t(value, ordinality)
                ),
                numbered AS (
                    SELECT id,
                           (SELECT COALESCE(max(published_seq), 0)
                              FROM domain_events) + ordinality AS seq
                    FROM ordered
                )
                UPDATE domain_events AS d
                SET published_seq = numbered.seq
                FROM numbered
                WHERE d.id = numbered.id
                  AND d.published_seq IS NULL
                """
            ),
            {"ids": ids},
        )
        return len(ids)

    def publish(self, factory: sessionmaker[Session]) -> int:
        """Publish in a fresh, short transaction (used by readers)."""
        with factory() as session:
            try:
                published = self.publish_pending(session)
                session.commit()
            except Exception:
                session.rollback()
                raise
        return published

    # ------------------------------------------------------------------ reads
    def read_after(
        self,
        session: Session,
        *,
        cursor: int,
        project_id: int | None = None,
        task_id: int | None = None,
        limit: int,
    ) -> list[DomainEvent]:
        stmt = (
            select(DomainEvent)
            .where(
                DomainEvent.published_seq.is_not(None),
                DomainEvent.published_seq > cursor,
            )
            .order_by(DomainEvent.published_seq)
            .limit(limit)
        )
        if project_id is not None:
            stmt = stmt.where(DomainEvent.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(DomainEvent.task_id == task_id)
        return list(session.scalars(stmt).all())

    def latest_cursor(
        self, session: Session, *, project_id: int | None = None, task_id: int | None = None
    ) -> int:
        stmt = select(func.max(DomainEvent.published_seq))
        if project_id is not None:
            stmt = stmt.where(DomainEvent.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(DomainEvent.task_id == task_id)
        return int(session.scalar(stmt) or 0)


def cursor_of(event: DomainEvent) -> int:
    """The published cursor of an event. Unpublished rows must not be shown."""
    if event.published_seq is None:  # pragma: no cover - filtered by every reader
        raise RuntimeError(f"domain event {event.id} has no publication cursor")
    return event.published_seq


__all__ = ["PUBLISH_LOCK_KEY", "EventFeedService", "cursor_of"]
