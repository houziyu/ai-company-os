"""Authentication application service.

All state changes happen in one transaction. The caller (API layer) supplies
the ``Session`` so a single commit covers user creation, platform state,
session issuance and the audit record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import (
    AuditAction,
    AuditActorType,
    SessionRevokeReason,
    UserRole,
)
from app.domain.errors import (
    CsrfValidationError,
    FieldError,
    InvalidCredentialsError,
    LoginBlockedError,
    NotAuthenticatedError,
    SetupAlreadyCompletedError,
    ValidationFailedError,
)
from app.domain.security import (
    PasswordPolicyError,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    tokens_match,
    validate_password_policy,
    verify_password,
)
from app.models.entities import AuthSession, User
from app.repositories.auth_repository import (
    AuditRepository,
    LoginAttemptRepository,
    PlatformRepository,
    SessionRepository,
    UserRepository,
    utcnow,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime
    user: User


@dataclass(slots=True)
class AuthenticatedSession:
    session: AuthSession
    user: User


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._platform = PlatformRepository()
        self._users = UserRepository()
        self._sessions = SessionRepository()
        self._attempts = LoginAttemptRepository()
        self._audit = AuditRepository()

    # ---------------------------------------------------------------- setup
    def is_setup_complete(self, session: Session) -> bool:
        state = self._platform.get_state(session)
        return bool(state and state.owner_user_id)

    def ensure_setup_allowed(self, session: Session) -> None:
        if self.is_setup_complete(session):
            raise SetupAlreadyCompletedError()

    def complete_setup(
        self,
        session: Session,
        *,
        username: str,
        display_name: str,
        password: str,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        """Create the single owner account and log them in.

        Concurrency: the ``platform_state`` row (inserted by the initial
        migration) is locked ``FOR UPDATE``. Concurrent setup requests therefore
        serialise; the second one observes ``owner_user_id`` already set and
        raises :class:`SetupAlreadyCompletedError`.
        """
        username = username.strip()
        display_name = display_name.strip()

        field_errors: list[FieldError] = []
        if not username:
            field_errors.append(FieldError("username", "请输入登录名。"))
        elif len(username) < 3 or len(username) > 64:
            field_errors.append(FieldError("username", "登录名长度需为 3 到 64 个字符。"))
        elif not all(ch.isalnum() or ch in "_.-" for ch in username):
            field_errors.append(
                FieldError("username", "登录名只能包含字母、数字、下划线、点和连字符。")
            )
        if not display_name:
            field_errors.append(FieldError("displayName", "请输入显示名称。"))
        elif len(display_name) > 80:
            field_errors.append(FieldError("displayName", "显示名称不能超过 80 个字符。"))
        try:
            validate_password_policy(password, self._settings.password_min_length)
        except PasswordPolicyError as exc:
            field_errors.append(FieldError("password", exc.message))

        if password and password != password.strip():
            pass  # policy violation already recorded by validate_password_policy
        if field_errors:
            raise ValidationFailedError(field_errors)

        state = self._platform.lock_state(session)

        if state.owner_user_id is not None:
            raise SetupAlreadyCompletedError()
        if self._users.count(session) > 0:
            # Defensive: an owner row exists but the platform pointer was lost.
            raise SetupAlreadyCompletedError()

        user = self._users.create(
            session,
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            role=UserRole.OWNER.value,
        )
        state.owner_user_id = user.id
        state.owner_created_at = utcnow()
        session.flush()

        self._audit.add(
            session,
            action=AuditAction.OWNER_SETUP_COMPLETED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.SYSTEM.value,
            actor_user_id=user.id,
            target_type="USER",
            target_id=str(user.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"username={username}",
        )

        issued = self._issue_session(session, user, ip_address=ip_address, user_agent=user_agent)
        logger.info("owner setup prepared", extra={"user_id": user.id})
        return issued

    # ---------------------------------------------------------------- login
    def login(
        self,
        session: Session,
        *,
        username: str,
        password: str,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        username = username.strip()
        # Phase 01 is single-owner. Reuse the persistent singleton lock so
        # count -> verify -> record/clear -> commit is one critical section,
        # including unknown usernames and requests from other API instances.
        self._platform.lock_state(session)
        now = utcnow()
        window_start = now - timedelta(minutes=self._settings.lockout_minutes)

        recent_failures = self._attempts.count_failures_since(session, username, window_start)
        if recent_failures >= self._settings.lockout_threshold:
            retry_after = self._settings.lockout_minutes * 60
            self._audit.add(
                session,
                action=AuditAction.LOGIN_BLOCKED.value,
                outcome="BLOCKED",
                actor_type=AuditActorType.ANONYMOUS.value,
                request_id=request_id,
                ip_address=ip_address,
                detail=f"username={username}; failures={recent_failures}",
            )
            # Commit before raising: the request transaction rolls back on error,
            # and a failed-login record that disappears would defeat lockout.
            session.commit()
            raise LoginBlockedError(retry_after)

        user = self._users.get_by_username(session, username)

        if user is not None and user.locked_until and user.locked_until > now:
            self._audit.add(
                session,
                action=AuditAction.LOGIN_BLOCKED.value,
                outcome="BLOCKED",
                actor_type=AuditActorType.ANONYMOUS.value,
                actor_user_id=user.id,
                request_id=request_id,
                ip_address=ip_address,
                detail="account_locked",
            )
            session.commit()
            raise LoginBlockedError(int((user.locked_until - now).total_seconds()))

        password_ok = verify_password(password, user.password_hash if user else None)

        if not password_ok or user is None:
            if user is not None:
                user.failed_login_count += 1
            self._attempts.record(
                session, username=username, succeeded=False, ip_address=ip_address
            )
            self._audit.add(
                session,
                action=AuditAction.LOGIN_FAILED.value,
                outcome="FAILURE",
                actor_type=AuditActorType.ANONYMOUS.value,
                actor_user_id=user.id if user else None,
                request_id=request_id,
                ip_address=ip_address,
                detail=f"username={username}",
            )
            session.commit()
            raise InvalidCredentialsError()

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        self._attempts.clear(session, username)
        self._audit.add(
            session,
            action=AuditAction.LOGIN_SUCCEEDED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=user.id,
            request_id=request_id,
            ip_address=ip_address,
            detail=f"username={username}",
        )
        issued = self._issue_session(session, user, ip_address=ip_address, user_agent=user_agent)
        logger.info("login prepared", extra={"user_id": user.id})
        return issued

    # -------------------------------------------------------------- sessions
    def _issue_session(
        self,
        session: Session,
        user: User,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> IssuedSession:
        token = generate_token()
        csrf_token = generate_token()
        expires_at = utcnow() + timedelta(hours=self._settings.session_ttl_hours)
        self._sessions.create(
            session,
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token_hash=hash_token(csrf_token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return IssuedSession(token=token, csrf_token=csrf_token, expires_at=expires_at, user=user)

    def authenticate(self, session: Session, token: str | None) -> AuthenticatedSession:
        """Resolve a cookie token to an active session or raise 401."""
        if not token:
            raise NotAuthenticatedError()
        # Serialize expiry/logout transitions for this session. Authentication
        # runs before application writes; callers must not pass pending work.
        record = self._sessions.get_by_token_hash(session, hash_token(token), for_update=True)
        if record is None:
            raise NotAuthenticatedError()
        now = utcnow()
        if record.revoked_at is not None:
            raise NotAuthenticatedError()
        if record.expires_at <= now:
            if record.revoked_at is None:
                self._sessions.revoke(
                    session, record, reason=SessionRevokeReason.EXPIRED.value, at=now
                )
                self._audit.add(
                    session,
                    action=AuditAction.SESSION_EXPIRED.value,
                    outcome="EXPIRED",
                    actor_type=AuditActorType.OWNER.value,
                    actor_user_id=record.user_id,
                    target_type="SESSION",
                    target_id=str(record.id),
                )
            # Persist the expiry evidence before returning 401. A failed
            # commit propagates as a database error, never as successful expiry.
            session.commit()
            raise NotAuthenticatedError()
        user = self._users.get(session, record.user_id)
        if user is None:
            raise NotAuthenticatedError()
        self._sessions.touch(session, record)
        return AuthenticatedSession(session=record, user=user)

    def verify_csrf(self, record: AuthSession, csrf_token: str | None) -> None:
        if not csrf_token or not tokens_match(csrf_token, record.csrf_token_hash):
            raise CsrfValidationError()

    def rotate_csrf(
        self, session: Session, record: AuthSession, *, request_id: str | None = None
    ) -> str:
        csrf_token = generate_token()
        record.csrf_token_hash = hash_token(csrf_token)
        session.flush()
        return csrf_token

    def logout(
        self,
        session: Session,
        record: AuthSession,
        *,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        self._sessions.revoke(session, record, reason=SessionRevokeReason.LOGOUT.value)
        self._audit.add(
            session,
            action=AuditAction.LOGOUT.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=record.user_id,
            target_type="SESSION",
            target_id=str(record.id),
            request_id=request_id,
            ip_address=ip_address,
        )
        session.flush()

    def revoke_all_sessions(
        self, session: Session, *, user_id: int, reason: SessionRevokeReason
    ) -> int:
        return self._sessions.revoke_all_for_user(session, user_id, reason=reason.value)


def summarize_audit_detail(detail: str | None) -> str | None:
    """Guard against accidentally logging secrets in ``detail``."""
    if not detail:
        return detail
    lowered = detail.lower()
    for marker in ("password", "token", "secret", "authorization"):
        if marker in lowered:
            return "redacted"
    return detail
