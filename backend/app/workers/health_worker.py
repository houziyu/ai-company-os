"""Phase 01 worker.

Scope guard: this worker only maintains a heartbeat and answers its own health
probe. It intentionally has no task consumption, model call or code execution
path — those arrive in later phases. It never imports the API router and never
exposes business endpoints.
"""

from __future__ import annotations

import contextlib
import json
import logging
import signal
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import build_engine, build_session_factory
from app.logging import configure_logging
from app.repositories.auth_repository import WorkerHeartbeatRepository

logger = logging.getLogger(__name__)


class WorkerState:
    def __init__(self) -> None:
        self.started_at = datetime.now(tz=UTC)
        self.last_heartbeat_at: datetime | None = None
        self.last_heartbeat_error: str | None = None
        self.stopping = threading.Event()


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "acos-worker"

    state: WorkerState
    session_factory: sessionmaker[Session]
    settings: Settings

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("worker http: " + fmt, *args)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/health/live":
            self._respond(
                200,
                {
                    "status": "ok",
                    "service": "worker",
                    "started_at": self.state.started_at.isoformat(),
                },
            )
            return
        if path == "/health/ready":
            self._respond(*self._readiness())
            return
        self._respond(404, {"status": "error", "message": "not found"})

    def _readiness(self) -> tuple[int, dict[str, object]]:
        problems: list[str] = []
        if self.state.last_heartbeat_error:
            problems.append(self.state.last_heartbeat_error)
        try:
            with self.session_factory() as session:
                session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            problems.append(f"database: {type(exc).__name__}")

        last = self.state.last_heartbeat_at
        if last is None:
            problems.append("尚未写入心跳")
        else:
            age = (datetime.now(tz=UTC) - last).total_seconds()
            if age > self.settings.worker_stale_after_seconds:
                problems.append(f"最后一次心跳在 {int(age)} 秒前")

        payload: dict[str, object] = {
            "status": "not_ready" if problems else "ok",
            "last_heartbeat_at": last.isoformat() if last else None,
        }
        if problems:
            payload["problems"] = problems
            return 503, payload
        return 200, payload

    def _respond(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def record_heartbeat(
    session_factory: sessionmaker[Session], settings: Settings, state: WorkerState
) -> None:
    repo = WorkerHeartbeatRepository()
    try:
        with session_factory() as session:
            repo.upsert(
                session,
                worker_name=settings.worker_name,
                version=settings.app_version,
                note="phase-01 health loop only",
            )
            session.commit()
        state.last_heartbeat_at = datetime.now(tz=UTC)
        state.last_heartbeat_error = None
    except SQLAlchemyError as exc:
        state.last_heartbeat_error = f"心跳写入失败（{type(exc).__name__}）"
        logger.warning("worker heartbeat failed", extra={"error": type(exc).__name__})


def start_health_server(
    settings: Settings, session_factory: sessionmaker[Session], state: WorkerState
) -> ThreadingHTTPServer:
    handler = type(
        "HealthHandler",
        (_HealthHandler,),
        {
            "state": state,
            "session_factory": session_factory,
            "settings": settings,
        },
    )
    # Binds all interfaces inside the container; nothing is published to the host.
    server = ThreadingHTTPServer(("0.0.0.0", settings.worker_health_port), handler)  # noqa: S104
    threading.Thread(target=server.serve_forever, name="worker-health", daemon=True).start()
    return server


def run() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    state = WorkerState()
    server = start_health_server(settings, session_factory, state)

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("worker received signal", extra={"signal": signum})
        state.stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError):  # pragma: no cover - not main thread
            signal.signal(sig, _handle_signal)

    logger.info(
        "worker starting (health loop only)",
        extra={"worker": settings.worker_name, "version": settings.app_version},
    )

    try:
        while not state.stopping.is_set():
            record_heartbeat(session_factory, settings, state)
            state.stopping.wait(settings.worker_heartbeat_interval_seconds)
    finally:
        server.shutdown()
        server.server_close()
        engine.dispose()
        logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
