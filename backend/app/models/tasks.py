"""Phase 03 tables: projects, tasks, executions, events, messages and budgets.

Design notes
------------
* **Task vs. execution.** ``tasks`` is the business objective and its current
  state; ``task_executions`` is one attempt, with its own id, lease, fencing
  token, usage and outcome. A retry always inserts a *new* execution row, so
  earlier evidence (usage, steps, ledger) is never overwritten.
* **Persistent PostgreSQL queue.** A worker claims work with
  ``SELECT ... FOR UPDATE SKIP LOCKED`` plus a conditional ``UPDATE ... WHERE
  status = 'QUEUED'``. The database decides the winner; no application-level
  mutex is involved, and the losing worker sees zero updated rows.
* **Leases and fencing.** ``task_executions.lease_expires_at`` plus a monotonic
  ``fencing_token`` per execution. Every write from a worker carries the token it
  held at claim time and is conditional on it, so a worker whose lease expired
  cannot overwrite a newer attempt (the "old lease must not write back" rule).
* **Money-like values are integers.** Budget and usage are token counts
  (``BIGINT``); there is no binary float in any ledger column. Unknown usage is
  ``NULL`` and ``usage_known = false`` — never 0.
* **Append-only evidence.** ``task_events``, ``budget_ledger_entries`` and
  ``task_messages`` have no update/delete endpoints. ``budget_reservations`` has a
  status transition (RESERVED → SETTLED/RELEASED) but its amounts are immutable.
* **Event ids for SSE replay.** ``domain_events`` has a global ascending bigint
  id. A client reconnects with ``Last-Event-ID`` and the server replays every
  event after it, so a dropped connection cannot silently lose progress.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


def content_fingerprint(payload: dict[str, Any]) -> str:
    """Stable digest of a command body, used with an idempotency key.

    The key alone is not enough: two different requests must not share one
    result, so the server compares this digest and answers 409 on a mismatch.
    """
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Project(Base, TimestampMixin):
    """A bounded body of work with its own token allowance."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("company_id", "key", name="uq_projects_company_key"),
        UniqueConstraint("company_id", "name", name="uq_projects_company_name"),
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="status_known"),
        CheckConstraint("run_mode IN ('DEMO', 'REAL')", name="run_mode_known"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(btrim(key)) > 0", name="key_not_blank"),
        CheckConstraint("length(btrim(objective)) > 0", name="objective_not_blank"),
        CheckConstraint("budget_tokens >= 0", name="budget_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_projects_company_status", "company_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    # Human-readable key, unique within the company (e.g. "AUTH-REFRESH").
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="DEMO")
    # Project envelope. Task allowances are carved out of this value.
    budget_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class Task(Base, TimestampMixin):
    """A unit of work. State changes only through the task service."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_tasks_company_idempotency"),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'BLOCKED', 'SUCCEEDED',"
            " 'FAILED', 'CANCELLED', 'INTERRUPTED')",
            name="status_known",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("length(btrim(objective)) > 0", name="objective_not_blank"),
        CheckConstraint("budget_tokens >= 0", name="budget_non_negative"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_auto_retries >= 0 AND max_auto_retries <= 5", name="retry_bound"),
        CheckConstraint("fencing_counter >= 0", name="fencing_counter_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_tasks_project_status_created", "project_id", "status", "created_at"),
        Index("ix_tasks_company_status", "company_id", "status"),
        Index("ix_tasks_queue", "status", "queued_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    # Who is expected to do it. Nullable because Phase 04/05 assign positions.
    assignee_agent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    # Deterministic demo behaviour label; never presented as a real model result.
    demo_behavior: Mapped[str] = mapped_column(String(32), nullable=False, server_default="SUCCESS")
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="DEMO")
    budget_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # Idempotency and its content digest (see content_fingerprint).
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_auto_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Monotonic claim counter for this task. Every claim increments it and writes
    # the new value into the execution's ``fencing_token``, so a worker holding an
    # older token can never write a result that belongs to a newer claim.
    fencing_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cooperative cancellation: the request is recorded, then the executor stops
    # at the next safe point. The browser closing is never treated as a cancel.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    # Human-readable last failure/blocked reason, sanitized (no provider bodies).
    last_error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class TaskRetryCommand(Base):
    """An accepted owner retry, retained even after its execution finishes."""

    __tablename__ = "task_retry_commands"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_task_retry_commands_key"),
        CheckConstraint("length(btrim(idempotency_key)) > 0", name="key_not_blank"),
        CheckConstraint("target_attempt >= 1", name="target_attempt_positive"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT")
    )
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    target_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TaskExecution(Base, TimestampMixin):
    """One attempt at a task: its own lease, fencing token, usage and outcome.

    ``fencing_token`` increases per execution and is stored on the execution. A
    worker write is only accepted while ``fencing_token`` still matches and the
    lease has not expired, which is what makes a late result from an evicted
    worker harmless.
    """

    __tablename__ = "task_executions"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt", name="uq_task_executions_task_attempt"),
        CheckConstraint(
            "status IN ('RUNNING', 'BLOCKED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED')",
            name="status_known",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("fencing_token >= 1", name="fencing_positive"),
        CheckConstraint(
            "reserved_tokens IS NULL OR reserved_tokens >= 0", name="reserved_non_negative"
        ),
        CheckConstraint(
            "settled_tokens IS NULL OR settled_tokens >= 0", name="settled_non_negative"
        ),
        CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0", name="prompt_non_negative"),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="completion_non_negative",
        ),
        Index("ix_task_executions_task_started", "task_id", "attempt"),
        Index("ix_task_executions_status_lease", "status", "lease_expires_at"),
        Index("ix_task_executions_worker", "worker_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    # Frozen configuration of this attempt, so history explains itself.
    run_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    demo_behavior: Mapped[str] = mapped_column(String(32), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Budget facts for this attempt. ``settled_tokens`` is NULL while unsettled:
    # an unknown amount is never reported as zero.
    reserved_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settled_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_known: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # A conservative reservation that has not been reconciled against a report.
    needs_reconciliation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True when the process died and the outcome of the outside world is unknown.
    interrupted_unknown_side_effects: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Recovery decision, recorded so a later operator can see why it re-ran or not.
    recovery_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    retried_from_execution_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskExecutionStep(Base, TimestampMixin):
    """An ordered step inside one execution.

    Steps make retries auditable: a retried attempt has its own set of steps and
    the old attempt's steps stay untouched.
    """

    __tablename__ = "task_execution_steps"
    __table_args__ = (
        UniqueConstraint("execution_id", "sequence", name="uq_task_execution_steps_sequence"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED', 'INTERRUPTED')",
            name="status_known",
        ),
        CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0", name="prompt_non_negative"),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="completion_non_negative",
        ),
        CheckConstraint("attempts >= 0 AND attempts <= 3", name="attempts_bounded"),
        Index("ix_task_execution_steps_execution_sequence", "execution_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Conservative estimate held before the call, then the reported usage.
    estimated_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_known: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TaskEvent(Base):
    """Human-facing task timeline (append-only).

    Distinct from ``domain_events``: this is the per-task audit trail the detail
    page renders, while ``domain_events`` is the cross-task SSE feed.
    """

    __tablename__ = "task_events"
    __table_args__ = (
        CheckConstraint("length(btrim(event_type)) > 0", name="type_not_blank"),
        Index("ix_task_events_task_created", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False
    )
    execution_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status_to: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainEvent(Base):
    """Global ordered event feed for SSE with resumable ids."""

    __tablename__ = "domain_events"
    __table_args__ = (
        CheckConstraint("length(btrim(event_type)) > 0", name="type_not_blank"),
        CheckConstraint(
            "published_seq IS NULL OR published_seq >= 1", name="published_seq_positive"
        ),
        Index("ix_domain_events_created", "created_at"),
        Index("ix_domain_events_project", "project_id", "id"),
        Index("ix_domain_events_task", "task_id", "id"),
        # One cursor per event, and the feed is read by cursor order.
        Index(
            "uq_domain_events_published_seq",
            "published_seq",
            unique=True,
            postgresql_where=text("published_seq IS NOT NULL"),
        ),
        Index(
            "ix_domain_events_unpublished",
            "id",
            postgresql_where=text("published_seq IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    # Publication cursor, assigned by EventFeedService in *commit visibility*
    # order. The primary key is allocated by a sequence *before* commit, so two
    # transactions can commit out of id order; a consumer that advanced past the
    # higher id would then never see the lower one. ``published_seq`` is only
    # assigned to rows the publisher can actually see (i.e. committed), so a
    # late-committed row always receives a *higher* cursor and is replayed.
    published_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    execution_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskMessage(Base):
    """Structured message: sender, receiver, type, task and correlation.

    A message is a communication record, not an authorization. Nothing here can
    grant a permission, and payloads are treated as untrusted text when rendered.
    """

    __tablename__ = "task_messages"
    __table_args__ = (
        CheckConstraint("sender_type IN ('OWNER', 'AGENT', 'SYSTEM')", name="sender_type_known"),
        CheckConstraint(
            "receiver_type IN ('OWNER', 'AGENT', 'SYSTEM')", name="receiver_type_known"
        ),
        CheckConstraint("length(btrim(message_type)) > 0", name="type_not_blank"),
        Index("ix_task_messages_task_created", "task_id", "created_at"),
        Index("ix_task_messages_correlation", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    execution_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=True
    )
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_agent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
    )
    sender_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    receiver_type: Mapped[str] = mapped_column(String(16), nullable=False)
    receiver_agent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
    )
    message_type: Mapped[str] = mapped_column(String(48), nullable=False)
    # Groups a request and its replies without relying on prose.
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BudgetAccount(Base, TimestampMixin):
    """A token allowance at company, project or task scope.

    ``limit_tokens`` is the ceiling. Consumed and reserved are *derived* from the
    ledger and open reservations at read time, and also cached here for cheap
    listing; every mutation goes through the budget service so the cached value
    and the ledger cannot drift silently. A check constraint keeps the cache
    non-negative even if a bug tried to write it.
    """

    __tablename__ = "budget_accounts"
    __table_args__ = (
        UniqueConstraint("scope", "scope_id", name="uq_budget_accounts_scope"),
        CheckConstraint("scope IN ('COMPANY', 'PROJECT', 'TASK')", name="scope_known"),
        CheckConstraint("limit_tokens >= 0", name="limit_non_negative"),
        CheckConstraint("consumed_tokens >= 0", name="consumed_non_negative"),
        CheckConstraint("reserved_tokens >= 0", name="reserved_non_negative"),
        Index("ix_budget_accounts_scope", "scope", "scope_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # Company id, project id or task id depending on ``scope``.
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    limit_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    consumed_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class BudgetReservation(Base):
    """A held amount. Amount fields are immutable; only the status changes.

    ``owner_ref`` is the entity the hold belongs to, and it is part of a unique
    index together with the account and kind. That is what makes a duplicated
    request a no-op instead of a second hold:

    * ``PLANNING`` → the task id (one envelope per task, at company and project);
    * ``CALL``     → the step id (one hold per step, at task scope);
    * ``PROBE``    → the probe id (one hold per probe, at company scope).

    Using one explicit integer instead of three nullable foreign keys keeps the
    uniqueness provable in the database rather than only in service code.
    """

    __tablename__ = "budget_reservations"
    __table_args__ = (
        # At most one *open* hold per (account, kind, owner). A task that is
        # retried legitimately opens a second planning hold later, so the
        # uniqueness is partial on the open state rather than global.
        Index(
            "uq_budget_reservations_open",
            "account_id",
            "kind",
            "owner_ref",
            unique=True,
            postgresql_where=text("status = 'RESERVED'"),
        ),
        CheckConstraint("kind IN ('PLANNING', 'CALL', 'PROBE')", name="kind_known"),
        CheckConstraint("status IN ('RESERVED', 'SETTLED', 'RELEASED')", name="status_known"),
        CheckConstraint("owner_ref >= 1", name="owner_ref_positive"),
        CheckConstraint("amount_tokens >= 0", name="amount_non_negative"),
        CheckConstraint(
            "settled_tokens IS NULL OR settled_tokens >= 0", name="settled_non_negative"
        ),
        CheckConstraint(
            "(kind <> 'PLANNING' OR task_id IS NOT NULL)"
            " AND (kind <> 'CALL' OR step_id IS NOT NULL)"
            " AND (kind <> 'PROBE' OR probe_id IS NOT NULL)",
            name="owner_present",
        ),
        CheckConstraint(
            "(status = 'RESERVED' AND settled_tokens IS NULL)"
            " OR (status <> 'RESERVED' AND settled_tokens IS NOT NULL)",
            name="settled_only_when_resolved",
        ),
        Index("ix_budget_reservations_account_status", "account_id", "status"),
        Index("ix_budget_reservations_task", "task_id"),
        Index("ix_budget_reservations_execution", "execution_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("budget_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    execution_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=True
    )
    step_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_execution_steps.id", ondelete="RESTRICT"), nullable=True
    )
    probe_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("model_probes.id", ondelete="RESTRICT"), nullable=True
    )
    # The unique part of the ownership triple; see the class docstring.
    owner_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RESERVED")
    amount_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # True when the amount is a conservative estimate the provider never confirmed.
    estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    needs_reconciliation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Why this hold exists ("step:生成演练结果", "auto-retry:12"). Code-owned.
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetLedgerEntry(Base):
    """Immutable record of every change to an allowance bucket.

    ``delta_reserved`` and ``delta_consumed`` are signed. A reservation adds to
    the reserved bucket; settling removes the hold and adds the *actually used*
    amount to the consumed bucket; releasing removes the hold and adds nothing.
    Unknown usage is stored as ``usage_tokens = NULL`` with
    ``needs_reconciliation = true`` — it is never written as a zero consumption.
    """

    __tablename__ = "budget_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "entry_kind",
            "reservation_id",
            name="uq_budget_ledger_entries_once",
        ),
        CheckConstraint("entry_kind IN ('RESERVE', 'SETTLE', 'RELEASE')", name="entry_kind_known"),
        CheckConstraint(
            "(entry_kind = 'RESERVE' AND delta_reserved > 0 AND delta_consumed = 0"
            " AND usage_tokens IS NULL)"
            # A settlement may report no usage: then it must be flagged for
            # reconciliation rather than stored as a zero-consumption entry.
            " OR (entry_kind = 'SETTLE' AND delta_reserved <= 0 AND delta_consumed >= 0"
            " AND (usage_tokens IS NOT NULL OR needs_reconciliation))"
            " OR (entry_kind = 'RELEASE' AND delta_reserved <= 0 AND delta_consumed = 0"
            " AND usage_tokens IS NULL)",
            name="entry_shape",
        ),
        CheckConstraint("source IN ('TASK_CALL', 'PROBE')", name="source_known"),
        Index("ix_budget_ledger_account_created", "account_id", "created_at"),
        Index("ix_budget_ledger_company_created", "company_id", "created_at"),
        Index("ix_budget_ledger_task_created", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("budget_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True
    )
    execution_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("task_executions.id", ondelete="RESTRICT"), nullable=True
    )
    reservation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("budget_reservations.id", ondelete="RESTRICT"), nullable=True
    )
    entry_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Signed changes to the two buckets. See the class docstring for the shapes.
    delta_reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    delta_consumed: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # The provider-reported total this entry accounted for; NULL = unknown.
    usage_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_known: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    needs_reconciliation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
