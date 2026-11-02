"""Data access for authentication-related tables.

Repositories take an existing :class:`Session` so the caller controls the
transaction boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    AuditEvent,
    AuthSession,
    LoginAttempt,
    PlatformState,
    User,
    WorkerHeartbeat,
)


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class PlatformRepository:
    def get_state(self, session: Session, *, for_update: bool = False) -> PlatformState | None:
        stmt = select(PlatformState).where(PlatformState.id == 1)
        if for_update:
            stmt = stmt.with_for_update()
        return session.scalars(stmt).one_or_none()

    def lock_state(self, session: Session) -> PlatformState:
        """Lock the single state row created by the initial migration.

        A missing row means the migration was not applied; that is a deployment
        error rather than something to paper over by inserting here.
        """
        state = self.get_state(session, for_update=True)
        if state is None:
            raise RuntimeError("platform_state row missing: database migrations were not applied")
        return state


class UserRepository:
    def get_by_username(self, session: Session, username: str) -> User | None:
        return session.scalars(select(User).where(User.username == username)).one_or_none()

    def get(self, session: Session, user_id: int) -> User | None:
        return session.get(User, user_id)

    def count(self, session: Session) -> int:
        return session.scalar(select(func.count()).select_from(User)) or 0

    def create(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
    ) -> User:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=role,
        )
        session.add(user)
        session.flush()
        return user


class SessionRepository:
    def create(
        self,
        session: Session,
        *,
        user_id: int,
        token_hash: str,
        csrf_token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> AuthSession:
        record = AuthSession(
            user_id=user_id,
            token_hash=token_hash,
            csrf_token_hash=csrf_token_hash,
            expires_at=expires_at,
            user_agent=(user_agent or "")[:256] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
        session.add(record)
        session.flush()
        return record

    def get_by_token_hash(
        self, session: Session, token_hash: str, *, for_update: bool = False
    ) -> AuthSession | None:
        stmt = select(AuthSession).where(AuthSession.token_hash == token_hash)
        if for_update:
            stmt = stmt.with_for_update()
        return session.scalars(stmt).one_or_none()

    def revoke(
        self,
        session: Session,
        record: AuthSession,
        *,
        reason: str,
        at: datetime | None = None,
    ) -> None:
        if record.revoked_at is not None:
            return
        record.revoked_at = at or utcnow()
        record.revoke_reason = reason
        session.flush()

    def revoke_all_for_user(
        self, session: Session, user_id: int, *, reason: str, except_id: int | None = None
    ) -> int:
        stmt = select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
        if except_id is not None:
            stmt = stmt.where(AuthSession.id != except_id)
        count = 0
        for record in session.scalars(stmt):
            self.revoke(session, record, reason=reason)
            count += 1
        return count

    def touch(self, session: Session, record: AuthSession) -> None:
        record.last_seen_at = utcnow()
        session.flush()

    def delete_expired(self, session: Session, *, older_than_days: int = 30) -> int:
        cutoff = utcnow() - timedelta(days=older_than_days)
        result = session.execute(delete(AuthSession).where(AuthSession.expires_at < cutoff))
        return int(getattr(result, "rowcount", 0) or 0)


class LoginAttemptRepository:
    def record(
        self, session: Session, *, username: str, succeeded: bool, ip_address: str | None
    ) -> None:
        session.add(
            LoginAttempt(
                username=username,
                succeeded=succeeded,
                ip_address=(ip_address or "")[:64] or None,
            )
        )
        session.flush()

    def count_failures_since(self, session: Session, username: str, since: datetime) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(LoginAttempt)
                .where(
                    LoginAttempt.username == username,
                    LoginAttempt.succeeded.is_(False),
                    LoginAttempt.created_at >= since,
                )
            )
            or 0
        )

    def clear(self, session: Session, username: str) -> None:
        session.execute(delete(LoginAttempt).where(LoginAttempt.username == username))


class AuditRepository:
    def add(
        self,
        session: Session,
        *,
        action: str,
        outcome: str,
        actor_type: str,
        actor_user_id: int | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        detail: str | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                action=action,
                outcome=outcome,
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                ip_address=(ip_address or "")[:64] or None,
                detail=detail,
            )
        )
        session.flush()

    def list_recent(self, session: Session, limit: int = 20) -> list[AuditEvent]:
        return list(
            session.scalars(
                select(AuditEvent)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .limit(limit)
            )
        )


class WorkerHeartbeatRepository:
    def upsert(
        self,
        session: Session,
        *,
        worker_name: str,
        version: str,
        note: str | None = None,
    ) -> WorkerHeartbeat:
        record = session.scalars(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_name == worker_name)
        ).one_or_none()
        now = utcnow()
        if record is None:
            record = WorkerHeartbeat(
                worker_name=worker_name,
                started_at=now,
                last_heartbeat_at=now,
                version=version,
                note=note,
            )
            session.add(record)
        else:
            record.last_heartbeat_at = now
            record.version = version
            record.note = note
        session.flush()
        return record

    def list_all(self, session: Session) -> list[WorkerHeartbeat]:
        return list(session.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.worker_name)))
