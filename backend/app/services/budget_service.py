"""Token budget: atomic reservation, settlement and release.

Why this module exists as its own service
-----------------------------------------
Every paid call must hold an allowance *before* it starts and report what it
actually used afterwards. Three properties make that safe under concurrency:

1. **A single lock order.** Company → project → task. Deadlocks are impossible
   because every writer takes the same order, and the caller never holds these
   rows across a network call.
2. **Conditional arithmetic in SQL.** A hold is granted only by an ``UPDATE ...
   WHERE limit - consumed - reserved >= amount``; the row's own check constraints
   forbid a negative bucket. Application-level "read then write" would let two
   transactions both see enough room.
3. **Unknown is not zero.** A provider that reports no usage is stored as
   ``usage_tokens = NULL`` and flagged ``needs_reconciliation``. The reserved
   amount is then treated as *consumed* (the conservative reading), because
   releasing it would silently hand back tokens that may already have been spent.

Idempotency of settlement comes from a unique index on
``(account_id, entry_kind, reservation_id)``: a second settlement of the same hold
is a database-level no-op, so retries and duplicate deliveries cannot double count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import BudgetScope, LedgerSource, ReservationKind, ReservationStatus
from app.domain.errors import BudgetInsufficientError, CompanyNotFoundError
from app.models.organization import Company
from app.models.tasks import (
    BudgetAccount,
    BudgetLedgerEntry,
    BudgetReservation,
    Project,
    Task,
)
from app.repositories.auth_repository import utcnow

logger = logging.getLogger(__name__)

# A reserve is refused when it would exceed the available room. These numbers are
# only defaults for the demo processor; a real provider estimate arrives with the
# request (Phase 04/05).
DEFAULT_PROBE_RESERVATION_TOKENS = 512


@dataclass(slots=True)
class AccountView:
    """One allowance as the UI shows it: four independent facts.

    ``available_tokens`` is clamped at zero for display but the underlying rows
    can never go negative (check constraints), so the clamp is presentation only.
    """

    account: BudgetAccount
    limit_tokens: int
    consumed_tokens: int
    reserved_tokens: int
    available_tokens: int
    needs_reconciliation_count: int


class BudgetService:
    """All budget mutations go through this class."""

    def __init__(self, *, default_company_limit: int = 1_000_000) -> None:
        self._default_company_limit = default_company_limit

    # ------------------------------------------------------------------ reads
    def _get_company(self, session: Session) -> Company:
        company = session.get(Company, 1)
        if company is None:
            raise CompanyNotFoundError()
        return company

    def _insert_account_or_read_winner(
        self,
        session: Session,
        *,
        scope: BudgetScope,
        scope_id: int,
        company_id: int,
        limit_tokens: int,
    ) -> BudgetAccount:
        """Insert one account under a SAVEPOINT.

        If a concurrent transaction inserted the same ``(scope, scope_id)`` first,
        the savepoint is rolled back and the winner read. A bare ``rollback()``
        would discard the caller's other pending writes (for example a task row
        created in the same transaction), which would be a silent data loss.
        """
        savepoint = session.begin_nested()
        account = BudgetAccount(
            scope=scope.value,
            scope_id=scope_id,
            company_id=company_id,
            limit_tokens=limit_tokens,
            consumed_tokens=0,
            reserved_tokens=0,
        )
        session.add(account)
        try:
            session.flush()
        except IntegrityError:
            savepoint.rollback()
            winner = session.scalars(
                select(BudgetAccount).where(
                    BudgetAccount.scope == scope.value,
                    BudgetAccount.scope_id == scope_id,
                )
            ).one_or_none()
            if winner is None:  # pragma: no cover - unique violation without a row
                raise
            return winner
        savepoint.commit()
        return account

    def ensure_company_account(self, session: Session) -> BudgetAccount:
        """Return the company account, creating it at the configured default.

        The default is a real, visible allowance (``Settings.company_default_budget_tokens``),
        not an invented "unlimited": it appears on the budget page and can be
        changed. A zero default would block every paid call.
        """
        account = session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == BudgetScope.COMPANY.value,
                BudgetAccount.scope_id == 1,
            )
        ).one_or_none()
        if account is not None:
            return account
        return self._insert_account_or_read_winner(
            session,
            scope=BudgetScope.COMPANY,
            scope_id=1,
            company_id=1,
            limit_tokens=self._default_company_limit,
        )

    def ensure_project_account(self, session: Session, project: Project) -> BudgetAccount:
        account = session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == BudgetScope.PROJECT.value,
                BudgetAccount.scope_id == project.id,
            )
        ).one_or_none()
        if account is not None:
            return account
        return self._insert_account_or_read_winner(
            session,
            scope=BudgetScope.PROJECT,
            scope_id=project.id,
            company_id=project.company_id,
            limit_tokens=project.budget_tokens,
        )

    def ensure_task_account(self, session: Session, task: Task) -> BudgetAccount:
        account = session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == BudgetScope.TASK.value,
                BudgetAccount.scope_id == task.id,
            )
        ).one_or_none()
        if account is not None:
            return account
        return self._insert_account_or_read_winner(
            session,
            scope=BudgetScope.TASK,
            scope_id=task.id,
            company_id=task.company_id,
            limit_tokens=task.budget_tokens,
        )

    def account_view(self, account: BudgetAccount, session: Session) -> AccountView:
        # A reservation that needs reconciliation is *counted until an operator
        # resolves it*, whether it is still open or was conservatively settled.
        # Filtering on ``status = RESERVED`` hid every settled-unknown call, which
        # is exactly the case the flag exists for (review finding R10).
        pending = session.scalar(
            select(func.count())
            .select_from(BudgetReservation)
            .where(
                BudgetReservation.account_id == account.id,
                BudgetReservation.needs_reconciliation.is_(True),
            )
        )
        available = account.limit_tokens - account.consumed_tokens - account.reserved_tokens
        return AccountView(
            account=account,
            limit_tokens=account.limit_tokens,
            consumed_tokens=account.consumed_tokens,
            reserved_tokens=account.reserved_tokens,
            available_tokens=max(available, 0),
            needs_reconciliation_count=int(pending or 0),
        )

    def pending_reconciliation(
        self, session: Session, *, limit: int = 100
    ) -> list[BudgetReservation]:
        """Every reservation whose real usage was never confirmed.

        Both shapes are returned on purpose: ``RESERVED`` is a call that has not
        ended yet, ``SETTLED`` is one that was charged conservatively. The caller
        must show which is which instead of merging them.
        """
        return list(
            session.scalars(
                select(BudgetReservation)
                .where(BudgetReservation.needs_reconciliation.is_(True))
                .order_by(BudgetReservation.id.desc())
                .limit(limit)
            ).all()
        )

    # --------------------------------------------------------------- locking
    def lock_company(self, session: Session) -> Company:
        # Task mutations take this before task/execution/budget locks. PostgreSQL
        # NO KEY UPDATE serialises these mutations without blocking KEY SHARE
        # locks acquired by concurrent claims when inserting foreign keys.
        company = session.scalars(
            select(Company)
            .where(Company.id == 1)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if company is None:
            raise CompanyNotFoundError()
        return company

    def _lock_account(self, session: Session, account_id: int) -> BudgetAccount:
        account = session.scalars(
            select(BudgetAccount).where(BudgetAccount.id == account_id).with_for_update()
        ).one()
        return account

    # ------------------------------------------------------------- reserving
    def _try_hold(
        self,
        session: Session,
        *,
        account: BudgetAccount,
        kind: ReservationKind,
        owner_ref: int,
        amount: int,
        scope: BudgetScope,
        scope_id: int,
        company_id: int,
        project_id: int | None,
        task_id: int | None,
        execution_id: int | None,
        step_id: int | None,
        probe_id: int | None,
        estimated: bool,
        detail: str | None = None,
    ) -> BudgetReservation | None:
        """Attempt one hold; return None when the allowance is insufficient.

        The amount is added with a conditional UPDATE so the database, not the
        application, decides whether there is room. ``FOR UPDATE`` on the account
        row means the following read reflects every committed change.
        """
        account = self._lock_account(session, account.id)
        available = account.limit_tokens - account.consumed_tokens - account.reserved_tokens
        if amount > available:
            return None

        result = session.execute(
            update(BudgetAccount)
            .where(
                BudgetAccount.id == account.id,
                BudgetAccount.limit_tokens
                - BudgetAccount.consumed_tokens
                - BudgetAccount.reserved_tokens
                >= amount,
            )
            .values(
                reserved_tokens=BudgetAccount.reserved_tokens + amount,
                version=BudgetAccount.version + 1,
            )
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:  # pragma: no cover
            # Unreachable while the row is locked; kept as a loud guard because a
            # silent no-op here would grant a hold that was never recorded.
            raise RuntimeError("budget hold update affected no row")
        session.expire(account)

        reservation = BudgetReservation(
            account_id=account.id,
            scope=scope.value,
            scope_id=scope_id,
            company_id=company_id,
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            step_id=step_id,
            probe_id=probe_id,
            owner_ref=owner_ref,
            kind=kind.value,
            status=ReservationStatus.RESERVED.value,
            amount_tokens=amount,
            estimated=estimated,
        )
        session.add(reservation)
        session.flush()
        session.add(
            BudgetLedgerEntry(
                account_id=account.id,
                company_id=company_id,
                project_id=project_id,
                task_id=task_id,
                execution_id=execution_id,
                reservation_id=reservation.id,
                entry_kind="RESERVE",
                source=(
                    LedgerSource.PROBE.value
                    if kind is ReservationKind.PROBE
                    else LedgerSource.TASK_CALL.value
                ),
                delta_reserved=amount,
                delta_consumed=0,
                usage_tokens=None,
                usage_known=False,
                detail=detail,
            )
        )
        session.flush()
        return reservation

    def find_reservation(
        self, session: Session, *, account_id: int, kind: ReservationKind, owner_ref: int
    ) -> BudgetReservation | None:
        return session.scalars(
            select(BudgetReservation).where(
                BudgetReservation.account_id == account_id,
                BudgetReservation.kind == kind.value,
                BudgetReservation.owner_ref == owner_ref,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
        ).one_or_none()

    def reserve_company_allowance(
        self,
        session: Session,
        *,
        amount: int,
        kind: ReservationKind,
        owner_ref: int,
        project_id: int | None,
        task_id: int | None,
        execution_id: int | None,
        step_id: int | None = None,
        probe_id: int | None = None,
        detail: str | None = None,
        estimated: bool = True,
    ) -> BudgetReservation:
        """Hold at company scope. Refuses when the company allowance is short."""
        company = self.lock_company(session)
        account = self.ensure_company_account(session)
        existing = self.find_reservation(
            session, account_id=account.id, kind=kind, owner_ref=owner_ref
        )
        if existing is not None:
            # The same owner still has an open hold: this is a repeated command,
            # so return the existing hold instead of charging the allowance twice.
            return existing
        reservation = self._try_hold(
            session,
            account=account,
            kind=kind,
            owner_ref=owner_ref,
            amount=amount,
            scope=BudgetScope.COMPANY,
            scope_id=company.id,
            company_id=company.id,
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            step_id=step_id,
            probe_id=probe_id,
            estimated=estimated,
            detail=detail,
        )
        if reservation is None:
            raise BudgetInsufficientError(
                scope="COMPANY",
                requested=amount,
                available=account.limit_tokens - account.consumed_tokens - account.reserved_tokens,
            )
        return reservation

    def reserve_project_allowance(
        self,
        session: Session,
        *,
        project: Project,
        amount: int,
        kind: ReservationKind,
        owner_ref: int,
        task_id: int | None,
        execution_id: int | None,
        detail: str | None = None,
    ) -> BudgetReservation:
        account = self.ensure_project_account(session, project)
        reservation = self._try_hold(
            session,
            account=account,
            kind=kind,
            owner_ref=owner_ref,
            amount=amount,
            scope=BudgetScope.PROJECT,
            scope_id=project.id,
            company_id=project.company_id,
            project_id=project.id,
            task_id=task_id,
            execution_id=execution_id,
            step_id=None,
            probe_id=None,
            estimated=True,
            detail=detail,
        )
        if reservation is None:
            raise BudgetInsufficientError(
                scope="PROJECT",
                requested=amount,
                available=account.limit_tokens - account.consumed_tokens - account.reserved_tokens,
            )
        return reservation

    def reserve_task_allowance(
        self,
        session: Session,
        *,
        task: Task,
        step_id: int,
        execution_id: int,
        amount: int,
        detail: str | None = None,
    ) -> BudgetReservation:
        account = self.ensure_task_account(session, task)
        reservation = self._try_hold(
            session,
            account=account,
            kind=ReservationKind.CALL,
            owner_ref=step_id,
            amount=amount,
            scope=BudgetScope.TASK,
            scope_id=task.id,
            company_id=task.company_id,
            project_id=task.project_id,
            task_id=task.id,
            execution_id=execution_id,
            step_id=step_id,
            probe_id=None,
            estimated=True,
            detail=detail,
        )
        if reservation is None:
            raise BudgetInsufficientError(
                scope="TASK",
                requested=amount,
                available=account.limit_tokens - account.consumed_tokens - account.reserved_tokens,
            )
        return reservation

    # -------------------------------------------------------------- settling
    def _write_settlement(
        self,
        session: Session,
        *,
        reservation: BudgetReservation,
        actual_tokens: int | None,
        known: bool,
        detail: str | None,
    ) -> BudgetLedgerEntry:
        """Resolve one hold exactly once.

        ``actual_tokens is None`` means the provider did not report usage. In that
        case the full reservation is treated as consumed and flagged for manual
        reconciliation — the alternative (releasing it) would under-report spend.
        """
        account = self._lock_account(session, reservation.account_id)
        released = min(reservation.amount_tokens, account.reserved_tokens)
        consumed = reservation.amount_tokens if actual_tokens is None else actual_tokens
        needs_reconciliation = not known

        session.execute(
            update(BudgetAccount)
            .where(BudgetAccount.id == account.id)
            .values(
                reserved_tokens=BudgetAccount.reserved_tokens - released,
                consumed_tokens=BudgetAccount.consumed_tokens + consumed,
                version=BudgetAccount.version + 1,
            )
        )

        reservation.status = ReservationStatus.SETTLED.value
        reservation.settled_tokens = consumed
        reservation.needs_reconciliation = needs_reconciliation
        reservation.resolved_at = utcnow()

        entry = BudgetLedgerEntry(
            account_id=account.id,
            company_id=reservation.company_id,
            project_id=reservation.project_id,
            task_id=reservation.task_id,
            execution_id=reservation.execution_id,
            reservation_id=reservation.id,
            entry_kind="SETTLE",
            source=(
                LedgerSource.PROBE.value
                if reservation.kind == ReservationKind.PROBE.value
                else LedgerSource.TASK_CALL.value
            ),
            delta_reserved=-released,
            delta_consumed=consumed,
            # An unknown amount keeps NULL here; only the flag says "reconcile".
            usage_tokens=actual_tokens,
            usage_known=known,
            needs_reconciliation=needs_reconciliation,
            detail=detail,
        )
        session.add(entry)
        session.flush()
        return entry

    def settle(
        self,
        session: Session,
        *,
        reservation_id: int,
        actual_tokens: int | None,
        known: bool,
        detail: str | None = None,
    ) -> BudgetLedgerEntry | None:
        """Settle a hold. Returns None if it was already resolved.

        The duplicate check is explicit *and* backed by the ledger unique index,
        so a duplicate delivery cannot double count even if two transactions race.
        """
        reservation = session.scalars(
            select(BudgetReservation)
            .where(BudgetReservation.id == reservation_id)
            .with_for_update()
        ).one_or_none()
        if reservation is None:
            return None
        if reservation.status != ReservationStatus.RESERVED.value:
            return None
        return self._write_settlement(
            session,
            reservation=reservation,
            actual_tokens=actual_tokens,
            known=known,
            detail=detail,
        )

    def settle_all_open_for_task(
        self,
        session: Session,
        *,
        task_id: int,
        actual_tokens: int | None,
        known: bool,
        detail: str | None = None,
    ) -> int:
        """Resolve every still-open *call* hold of a task at once.

        Only ``CALL`` holds are resolved here. The company/project planning
        envelope is a separate decision (see :meth:`settle_planning_holds` and
        :meth:`release_planning_holds`), because a cancelled task that never made
        a call must give the envelope back rather than consume it.

        Used when an attempt ends without settling each step individually (for
        example an interrupted worker). Amounts already resolved are untouched, so
        this is safe to call repeatedly.
        """
        reservations = session.scalars(
            select(BudgetReservation)
            .where(
                BudgetReservation.task_id == task_id,
                BudgetReservation.kind == ReservationKind.CALL.value,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
            .order_by(BudgetReservation.id)
            .with_for_update()
        ).all()
        settled = 0
        for reservation in reservations:
            self._write_settlement(
                session,
                reservation=reservation,
                actual_tokens=actual_tokens,
                known=known,
                detail=detail,
            )
            settled += 1
        return settled

    def release(
        self, session: Session, *, reservation_id: int, detail: str | None = None
    ) -> BudgetLedgerEntry | None:
        """Return a hold that was never used (cancelled before the call)."""
        reservation = session.scalars(
            select(BudgetReservation)
            .where(BudgetReservation.id == reservation_id)
            .with_for_update()
        ).one_or_none()
        if reservation is None or reservation.status != ReservationStatus.RESERVED.value:
            return None

        account = self._lock_account(session, reservation.account_id)
        released = min(reservation.amount_tokens, account.reserved_tokens)
        session.execute(
            update(BudgetAccount)
            .where(BudgetAccount.id == account.id)
            .values(
                reserved_tokens=BudgetAccount.reserved_tokens - released,
                version=BudgetAccount.version + 1,
            )
        )
        reservation.status = ReservationStatus.RELEASED.value
        reservation.settled_tokens = 0
        reservation.resolved_at = utcnow()

        entry = BudgetLedgerEntry(
            account_id=account.id,
            company_id=reservation.company_id,
            project_id=reservation.project_id,
            task_id=reservation.task_id,
            execution_id=reservation.execution_id,
            reservation_id=reservation.id,
            entry_kind="RELEASE",
            source=(
                LedgerSource.PROBE.value
                if reservation.kind == ReservationKind.PROBE.value
                else LedgerSource.TASK_CALL.value
            ),
            delta_reserved=-released,
            delta_consumed=0,
            usage_tokens=None,
            usage_known=False,
            detail=detail,
        )
        session.add(entry)
        session.flush()
        return entry

    def release_planning_holds(
        self, session: Session, *, task_id: int, detail: str | None = None
    ) -> int:
        """Return the task's company/project planning envelope unused.

        Only valid when no paid call happened (a queued task cancelled before it
        started). A task that did make calls settles instead — see
        :meth:`settle_planning_holds`.
        """
        reservations = session.scalars(
            select(BudgetReservation)
            .where(
                BudgetReservation.task_id == task_id,
                BudgetReservation.kind == ReservationKind.PLANNING.value,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
            .order_by(BudgetReservation.id)
            .with_for_update()
        ).all()
        released = 0
        for reservation in reservations:
            self.release(session, reservation_id=reservation.id, detail=detail)
            released += 1
        return released

    def settle_planning_holds(
        self,
        session: Session,
        *,
        task_id: int,
        actual_tokens: int | None,
        known: bool,
        detail: str | None = None,
    ) -> int:
        """Settle the company and project envelope against real usage.

        Without this step the company ledger would only ever show released holds
        and never the tokens actually spent, which would make the company balance
        a fiction. ``actual_tokens is None`` consumes the whole envelope and flags
        reconciliation: an unknown amount is not a free one.
        """
        reservations = session.scalars(
            select(BudgetReservation)
            .where(
                BudgetReservation.task_id == task_id,
                BudgetReservation.kind == ReservationKind.PLANNING.value,
                BudgetReservation.status == ReservationStatus.RESERVED.value,
            )
            .order_by(BudgetReservation.id)
            .with_for_update()
        ).all()
        settled = 0
        for reservation in reservations:
            # Never account for more than the envelope held at this scope.
            amount = reservation.amount_tokens if actual_tokens is None else actual_tokens
            self._write_settlement(
                session,
                reservation=reservation,
                actual_tokens=min(amount, reservation.amount_tokens)
                if actual_tokens is not None
                else None,
                known=known,
                detail=detail,
            )
            settled += 1
        return settled

    # ------------------------------------------------------------ projections
    def ledger_for_company(self, session: Session, *, limit: int = 100) -> list[BudgetLedgerEntry]:
        return list(
            session.scalars(
                select(BudgetLedgerEntry)
                .where(BudgetLedgerEntry.company_id == 1)
                .order_by(BudgetLedgerEntry.id.desc())
                .limit(limit)
            )
        )

    def ledger_for_task(self, session: Session, task_id: int) -> list[BudgetLedgerEntry]:
        return list(
            session.scalars(
                select(BudgetLedgerEntry)
                .where(BudgetLedgerEntry.task_id == task_id)
                .order_by(BudgetLedgerEntry.id)
            )
        )

    def task_account(self, session: Session, task_id: int) -> BudgetAccount | None:
        return session.scalars(
            select(BudgetAccount).where(
                BudgetAccount.scope == BudgetScope.TASK.value,
                BudgetAccount.scope_id == task_id,
            )
        ).one_or_none()

    def company_spend_since(self, session: Session, since: datetime) -> int | None:
        """Consumed tokens since a timestamp, or None when any entry is unknown.

        Returning None instead of a partial sum keeps "we don't know" honest: the
        overview shows 待核对 rather than a number that looks complete.
        """
        rows = session.execute(
            select(
                BudgetLedgerEntry.usage_known,
                BudgetLedgerEntry.usage_tokens,
                BudgetLedgerEntry.delta_consumed,
                BudgetLedgerEntry.needs_reconciliation,
            ).where(
                BudgetLedgerEntry.entry_kind == "SETTLE",
                BudgetLedgerEntry.created_at >= since,
                BudgetLedgerEntry.company_id == 1,
            )
        ).all()
        total = 0
        unknown = False
        for usage_known, usage_tokens, delta_consumed, needs_reconciliation in rows:
            if needs_reconciliation or not usage_known or usage_tokens is None:
                unknown = True
            total += int(delta_consumed or 0)
        return None if unknown else total
