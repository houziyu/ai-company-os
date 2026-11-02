"""Password hashing, token generation and constant-time comparison.

Argon2id is used through argon2-cffi. Verification is performed even when the
user does not exist (dummy hash) so that response timing does not reveal
whether an account exists.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

# A fixed hash of a random throwaway password; verifying against it keeps the
# unknown-user path roughly as expensive as the known-user path.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


class PasswordPolicyError(ValueError):
    """Raised when a password fails local policy."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify ``password`` against ``password_hash``.

    When ``password_hash`` is ``None`` a dummy verification still runs so the
    caller cannot be timed to discover missing accounts.
    """
    target = password_hash or _DUMMY_HASH
    try:
        return _hasher.verify(target, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_policy(password: str, min_length: int) -> None:
    if len(password) < min_length:
        raise PasswordPolicyError(f"密码至少需要 {min_length} 个字符。")
    if len(password) > 256:
        raise PasswordPolicyError("密码不能超过 256 个字符。")
    if password.strip() != password:
        raise PasswordPolicyError("密码首尾不能是空白字符。")
    if password.isdigit():
        raise PasswordPolicyError("密码不能全是数字。")
    if len(set(password)) < 4:
        raise PasswordPolicyError("密码包含的字符种类太少，请换一个更复杂的密码。")


def generate_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(raw: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(raw), expected_hash)
