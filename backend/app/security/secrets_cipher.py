"""Encryption at rest for provider credentials (ADR-0006).

The operator types an API key in the model centre; the server stores it
encrypted in ``providers.secret_ciphertext``. This module is the only place that
turns a key into a stored blob and back.

Rules that keep "the key is stored, but never exposed" verifiable:

* **AES-256-GCM with a server-held key.** The data key comes from
  ``SECRET_ENCRYPTION_KEY`` (32 random bytes, base64). It never enters the
  database, an API response, a log line or the audit trail.
* **Authenticated envelope, versioned.** ``v1.<kid>.<nonce>.<ciphertext>``. The
  ``kid`` is a short hash of the data key, so a row encrypted under a replaced
  key is *recognisably* stale instead of silently failing as "wrong password"
  at the provider.
* **AAD binds the blob to its purpose.** A blob copied into another column or
  into a different record's ``secret_ciphertext`` still decrypts only as
  "provider credential", and the nonce makes cross-row replay of the same
  plaintext produce different blobs.
* **Failures never throw details.** :meth:`SecretCipher.decrypt` returns
  ``None`` on any problem — a raised exception message could leak lengths or
  library internals into a log.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Envelope version. A future format change bumps this and keeps ``v1`` readable.
ENVELOPE_VERSION = "v1"
# Additional authenticated data: the purpose, not the record. Binding the record
# id would make a row's blob unreadable after a legitimate copy/restore.
AAD = b"acos/provider-secret/v1"

KEY_BYTES = 32
NONCE_BYTES = 12
KID_HEX_CHARS = 8
# Refuse absurd inputs before they reach the database column (Text) or memory.
MAX_PLAINTEXT_CHARS = 512


class EncryptionNotConfiguredError(RuntimeError):
    """The data key is absent. Callers must refuse to store a credential."""


class EncryptionKeyInvalidError(RuntimeError):
    """The configured data key is not 32 base64-encoded bytes."""


def generate_key() -> str:
    """Return a fresh base64 data key (for ``scripts/generate_secrets.py``)."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")


@dataclass(frozen=True, slots=True)
class SecretCipher:
    """AES-256-GCM codec for provider credentials.

    ``data_key`` may be ``None`` when the deployment has not configured one. The
    cipher then refuses to encrypt (so a key is never stored in the clear) but
    still recognises stored blobs as the wrong key id, which lets the UI say
    "无法解密" instead of "未配置".
    """

    data_key: bytes | None = None

    @classmethod
    def from_setting(cls, value: str | None) -> SecretCipher:
        """Build from the raw ``SECRET_ENCRYPTION_KEY`` setting.

        A malformed value raises at startup: silently accepting it would either
        reject every save at runtime (confusing) or, worse, tempt a fallback to
        plaintext storage.
        """
        if value is None or not value.strip():
            return cls(data_key=None)
        try:
            raw = base64.b64decode(value.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EncryptionKeyInvalidError(
                "SECRET_ENCRYPTION_KEY 必须是 32 字节随机值的 base64 编码。"
            ) from exc
        if len(raw) != KEY_BYTES:
            raise EncryptionKeyInvalidError(
                "SECRET_ENCRYPTION_KEY 必须是 32 字节随机值的 base64 编码。"
            )
        return cls(data_key=raw)

    @property
    def available(self) -> bool:
        return self.data_key is not None

    @property
    def kid(self) -> str | None:
        """Short, non-secret identifier of the current data key."""
        if self.data_key is None:
            return None
        return hashlib.sha256(self.data_key).hexdigest()[:KID_HEX_CHARS]

    def encrypt(self, plaintext: str) -> str:
        """Encrypt one credential. Raises when no data key is configured."""
        if self.data_key is None:
            raise EncryptionNotConfiguredError(
                "服务端未配置 SECRET_ENCRYPTION_KEY，无法保存供应商密钥。"
            )
        if not plaintext or len(plaintext) > MAX_PLAINTEXT_CHARS:
            raise ValueError("credential length out of range")
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self.data_key).encrypt(nonce, plaintext.encode("utf-8"), AAD)
        return ".".join(
            (
                ENVELOPE_VERSION,
                self.kid or "",
                _b64(nonce),
                _b64(ciphertext),
            )
        )

    def can_decrypt(self, blob: str | None) -> bool:
        """Cheap, non-decrypting check used at every read boundary.

        Only the version and key id are compared. Authentication is deliberately
        not run here: a list response must not do AES work for every row, and
        "the blob looks like ours" is enough to distinguish "未配置" from
        "无法解密" in the UI. :meth:`decrypt` is the authoritative check and runs
        once, immediately before a paid call.
        """
        if not blob:
            return False
        parts = blob.split(".")
        if len(parts) != 4 or parts[0] != ENVELOPE_VERSION:
            return False
        return self.kid is not None and parts[1] == self.kid

    def decrypt(self, blob: str | None) -> str | None:
        """Return the credential, or ``None`` when it cannot be read here."""
        if not self.can_decrypt(blob) or blob is None:
            return None
        parts = blob.split(".")
        try:
            nonce = _unb64(parts[2])
            ciphertext = _unb64(parts[3])
        except (binascii.Error, ValueError):
            return None
        if len(nonce) != NONCE_BYTES:
            return None
        try:
            plaintext = AESGCM(self.data_key).decrypt(  # type: ignore[arg-type]
                nonce, ciphertext, AAD
            )
        except (InvalidTag, ValueError):
            return None
        try:
            text = plaintext.decode("utf-8")
        except UnicodeDecodeError:  # pragma: no cover - only a corrupt blob
            return None
        return text or None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
