"""Liveness and readiness evaluation.

Readiness fails when any of the following is untrue, so the container never
reports healthy while the database is unusable or migrations are pending:
* the database answers a query,
* the recorded Alembic revision equals the packaged script head,
* (optionally) the worker has a fresh heartbeat.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "fail", "skip"]


@dataclass(slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(slots=True)
class ReadinessReport:
    ready: bool
    checks: list[CheckResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "checks": [check.as_dict() for check in self.checks],
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
        }


def alembic_script_dir(backend_root: Path) -> Path:
    return backend_root / "alembic"


def expected_head_revision(backend_root: Path) -> str:
    config = Config()
    config.set_main_option("script_location", str(alembic_script_dir(backend_root)))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one alembic head, found {len(heads)}: {heads}")
    return heads[0]


class HealthService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        backend_root: Path,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._backend_root = backend_root
        self._started_at = datetime.now(tz=UTC)

    # ------------------------------------------------------------- database
    def _check_database(self) -> CheckResult:
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT 1"))
            return CheckResult("database", "ok")
        except SQLAlchemyError as exc:
            logger.warning("readiness: database unreachable", extra={"error": type(exc).__name__})
            return CheckResult("database", "fail", f"数据库连接失败（{type(exc).__name__}）。")

    def _check_migrations(self) -> CheckResult:
        try:
            head = expected_head_revision(self._backend_root)
        except Exception as exc:  # pragma: no cover - packaging error
            return CheckResult("migrations", "fail", f"无法读取迁移脚本：{exc}")

        try:
            with self._session_factory() as session:
                table = session.execute(
                    text("SELECT to_regclass('public.alembic_version')")
                ).scalar_one_or_none()
                if table is None:
                    return CheckResult("migrations", "fail", "数据库尚未执行迁移。")
                current = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            return CheckResult(
                "migrations",
                "fail",
                f"无法读取 alembic_version（{type(exc).__name__}）。",
            )

        if current is None:
            return CheckResult("migrations", "fail", "数据库尚未执行迁移。")
        if current != head:
            return CheckResult(
                "migrations",
                "fail",
                f"数据库版本 {current} 与代码版本 {head} 不一致，请先执行迁移。",
            )
        return CheckResult("migrations", "ok", f"revision={current}")

    # --------------------------------------------------------------- worker
    def _check_worker(self, required: bool) -> CheckResult:
        try:
            with self._session_factory() as session:
                row = session.execute(
                    text(
                        "SELECT last_heartbeat_at FROM worker_heartbeats"
                        " ORDER BY last_heartbeat_at DESC LIMIT 1"
                    )
                ).one_or_none()
        except SQLAlchemyError as exc:
            return CheckResult(
                "worker", "fail" if required else "skip", f"无法读取心跳（{type(exc).__name__}）。"
            )

        if row is None:
            return CheckResult(
                "worker", "fail" if required else "skip", "尚未收到任何 Worker 心跳。"
            )

        last_heartbeat = row[0]
        if last_heartbeat.tzinfo is None:
            last_heartbeat = last_heartbeat.replace(tzinfo=UTC)
        age = (datetime.now(tz=UTC) - last_heartbeat).total_seconds()
        stale_after = self._settings.worker_stale_after_seconds
        if age > stale_after:
            return CheckResult(
                "worker",
                "fail" if required else "skip",
                f"最后一次心跳在 {int(age)} 秒前，超过阈值 {int(stale_after)} 秒。",
            )
        return CheckResult("worker", "ok", f"last_heartbeat_age_seconds={int(age)}")

    # ---------------------------------------------------------------- public
    def readiness(self, *, require_worker: bool) -> ReadinessReport:
        """Readiness for this process.

        The API is ready when the database is reachable and migrated. ``worker``
        is reported for information; it gates readiness only when
        ``require_worker`` is set (used by the worker's own probe).
        """
        checks = [self._check_database()]
        if checks[0].status == "ok":
            # Only evaluate migrations when the database is reachable; otherwise
            # the failure message would be misleading.
            checks.append(self._check_migrations())
            checks.append(self._check_worker(required=require_worker))
        else:
            checks.append(CheckResult("migrations", "skip", "数据库不可用。"))
            checks.append(CheckResult("worker", "skip", "数据库不可用。"))

        gating = [check for check in checks if check.name != "worker" or require_worker]
        ready = all(check.status == "ok" for check in gating)
        return ReadinessReport(ready=ready, checks=checks)

    # --------------------------------------------------------- status page
    def service_status(self) -> dict[str, object]:
        """Non-secret facts for the settings page.

        Never includes connection strings, secrets or stack traces.
        """
        database_status = "unknown"
        revision: str | None = None
        expected: str | None = None
        worker_status = "unknown"
        last_heartbeat: datetime | None = None
        worker_version: str | None = None

        try:
            expected = expected_head_revision(self._backend_root)
        except Exception:  # pragma: no cover - packaging issue
            expected = None

        try:
            with self._session_factory() as session:
                database_status = "ok"
                revision = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                row = session.execute(
                    text(
                        "SELECT last_heartbeat_at, version FROM worker_heartbeats"
                        " ORDER BY last_heartbeat_at DESC LIMIT 1"
                    )
                ).one_or_none()
        except SQLAlchemyError:
            database_status = "fail"
        else:
            if row is None:
                worker_status = "missing"
            else:
                last_heartbeat = row[0]
                worker_version = row[1]
                if last_heartbeat.tzinfo is None:
                    last_heartbeat = last_heartbeat.replace(tzinfo=UTC)
                age = (datetime.now(tz=UTC) - last_heartbeat).total_seconds()
                worker_status = (
                    "ok" if age <= self._settings.worker_stale_after_seconds else "stale"
                )

        return {
            "appVersion": self._settings.app_version,
            "environment": self._settings.app_env,
            "databaseStatus": database_status,
            "databaseRevision": revision,
            "expectedRevision": expected,
            "workerLastHeartbeatAt": (last_heartbeat.isoformat() if last_heartbeat else None),
            "workerStatus": worker_status,
            "workerVersion": worker_version,
            "sessionTtlHours": self._settings.session_ttl_hours,
            "startedAt": self._started_at.isoformat(),
        }
