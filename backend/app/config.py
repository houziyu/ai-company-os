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

    lockout_threshold: int = Field(default=10, ge=3, le=100)
    lockout_minutes: int = Field(default=5, ge=1, le=120)
    password_min_length: int = Field(default=12, ge=12, le=128)

    worker_name: str = "worker-1"
    worker_health_port: int = Field(default=9100, ge=1, le=65535)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, gt=0.5, le=300)

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
