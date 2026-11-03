"""Application settings loaded from environment variables.

Secrets are never accepted as defaults: DATABASE_URL is required so that a
misconfigured deployment fails loudly instead of silently connecting to a
wrong database.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_origin(value: str) -> str:
    """Return ``scheme://host[:port]`` in lowercase, or an empty string."""
    value = value.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    port = parts.port
    default_port = 443 if parts.scheme == "https" else 80
    if port and port != default_port:
        return f"{parts.scheme.lower()}://{host}:{port}"
    return f"{parts.scheme.lower()}://{host}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Company OS"
    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"

    database_url: str
    public_base_url: str = "http://localhost:8080"
    extra_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    session_ttl_hours: int = Field(default=12, ge=1, le=720)
    session_cookie_name: str = "acos_session"
    csrf_cookie_name: str = "acos_csrf"
    cookie_secure: bool = False

    enable_api_docs: bool = False
    log_level: str = "INFO"

    # ADR-0006: base64 of 32 random bytes, used to encrypt provider credentials
    # at rest. Unset means "cannot store a key" (saves are refused with a clear
    # error); it never means "store it in the clear".
    secret_encryption_key: str | None = None

    lockout_threshold: int = Field(default=10, ge=3, le=100)
    lockout_minutes: int = Field(default=5, ge=1, le=120)
    password_min_length: int = Field(default=12, ge=12, le=128)

    worker_name: str = "worker-1"
    worker_health_port: int = Field(default=9100, ge=1, le=65535)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, gt=0.5, le=300)
    # Phase 03: how often an idle worker polls the queue, and how long a claimed
    # attempt keeps its lease without a heartbeat. The lease must comfortably
    # exceed one demo step; tests lower it to observe recovery quickly.
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0.05, le=60)
    worker_lease_seconds: float = Field(default=30.0, ge=2.0, le=600)

    # Demo processor pacing. Only used by the labelled rehearsal processor; the
    # value is exposed so acceptance can make a long-running attempt observable.
    # The upper bound is generousand exists only to catch a typo; a value longer
    # than Docker's stop grace period is exactly how "interrupted, not gracefully
    # finished" is reproduced on purpose.
    demo_slow_step_seconds: float = Field(default=1.5, ge=0.0, le=600)

    # Phase 03 budget defaults. A company account is created with a real, visible
    # allowance instead of an invented unlimited one or zero: zero would block
    # every paid call, unlimited would be a fiction. Operators can change it.
    company_default_budget_tokens: int = Field(default=1_000_000, ge=0, le=10**12)
    # Conservative hold for one connectivity probe; settled against reported usage.
    probe_reservation_tokens: int = Field(default=512, ge=1, le=1_000_000)

    @field_validator("public_base_url")
    @classmethod
    def _validate_public_base_url(cls, value: str) -> str:
        if not normalize_origin(value):
            raise ValueError("public_base_url must be an absolute http(s) URL")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        level = value.upper()
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Origins accepted for browser-originated writes.

        The public base URL is the deployment origin; localhost and 127.0.0.1
        variants are both accepted because operators commonly use either.
        """
        origins: set[str] = set()
        candidates = [self.public_base_url, *self.extra_allowed_origins]
        for candidate in candidates:
            origin = normalize_origin(candidate)
            if not origin:
                continue
            origins.add(origin)
            parts = urlsplit(origin)
            if parts.hostname == "localhost":
                origins.add(origin.replace("//localhost", "//127.0.0.1", 1))
            elif parts.hostname == "127.0.0.1":
                origins.add(origin.replace("//127.0.0.1", "//localhost", 1))
        return frozenset(origins)

    @property
    def worker_stale_after_seconds(self) -> float:
        return max(self.worker_heartbeat_interval_seconds * 3, 15.0)
