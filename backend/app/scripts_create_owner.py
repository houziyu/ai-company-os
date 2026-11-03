"""One-off operator task: create an additional OWNER account.

Run inside the API/worker container so it uses the SAME Argon2 hasher, password
policy and audit repository as the application — no second implementation of
security rules.

It does NOT bypass the password policy: the password is validated exactly like
the login/setup path does. It does not touch ``platform_state.owner_user_id``
(that pointer keeps naming the original owner) and it never prints a hash or the
password.

Usage (inside a container):
    python -m app.scripts_create_owner --username admin --display-name 管理员
The password is read from STDIN so it does not appear in argv or shell history.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.config import Settings
from app.db.session import build_engine, build_session_factory
from app.domain.enums import AuditAction, AuditActorType, UserRole
from app.domain.security import PasswordPolicyError, hash_password, validate_password_policy
from app.models.entities import User
from app.repositories.auth_repository import AuditRepository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()

    settings = Settings()
    username = args.username.strip()
    display_name = args.display_name.strip()

    if not (3 <= len(username) <= 64):
        print("username must be 3-64 characters", file=sys.stderr)
        return 2
    if not all(ch.isalnum() or ch in "_.-" for ch in username):
        print("username may only contain letters, digits, _ . -", file=sys.stderr)
        return 2
    if not display_name or len(display_name) > 80:
        print("display name must be 1-80 characters", file=sys.stderr)
        return 2

    password = getpass.getpass("password: ")
    try:
        validate_password_policy(password, settings.password_min_length)
    except PasswordPolicyError as exc:
        print(f"password rejected by policy: {exc.message}", file=sys.stderr)
        return 3

    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            existing = session.scalars(
                select(User).where(User.username == username)
            ).one_or_none()
            if existing is not None:
                print(f"user '{username}' already exists (id={existing.id}); nothing done")
                return 1
            user = User(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                role=UserRole.OWNER.value,
            )
            session.add(user)
            session.flush()
            AuditRepository().add(
                session,
                action=AuditAction.OWNER_SETUP_COMPLETED.value,
                outcome="SUCCESS",
                actor_type=AuditActorType.SYSTEM.value,
                target_type="USER",
                target_id=str(user.id),
                detail=f"operator-created owner account username={username}",
            )
            session.commit()
            print(f"created user id={user.id} username={username} role={user.role}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
