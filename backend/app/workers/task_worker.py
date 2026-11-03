"""Phase 03 worker: persistent task consumer.

Responsibilities
----------------
* Keep the Phase 01 heartbeat (readiness still depends on it).
* Claim queued tasks from PostgreSQL with ``SKIP LOCKED`` and a fenced lease.
* Run the labelled demo processor, heart-beating its lease while it works.
* Recover attempts whose lease expired (including those left by a worker process
  that was killed), then decide per the rules in ``ExecutionService`` whether to
  re-queue or wait for a human.
* Answer its own health endpoint, now also reporting the queue it serves.

Scope guard: no code execution, no arbitrary tool calls, no executor container.
Phase 04 owns isolation; this worker only runs the deterministic demo processor.
"""

from __future__ import annotations

import contextlib
import json
import logging
import signal
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import build_engine, build_session_factory
from app.logging import configure_logging
from app.repositories.auth_repository import WorkerHeartbeatRepository
from app.services.execution_service import (
    ClaimedExecution,
    ExecutionService,
    ProcessorOutcome,
)
from app.workers.demo_processor import DemoProcessor

logger = logging.getLogger(__name__)


class WorkerState:
    def __init__(self) -> None:
        self.started_at = datetime.now(tz=UTC)
        self.last_heartbeat_at: datetime | None = None
        self.last_heartbeat_error: str | None = None
        self.last_claim_at: datetime | None = None
        self.current_task_id: int | None = None
        self.current_execution_id: int | None = None
        self.processed_count = 0
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
                    "processed": self.state.processed_count,
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
            "worker": self.settings.worker_name,
            "last_heartbeat_at": last.isoformat() if last else None,
            "current_task_id": self.state.current_task_id,
            "current_execution_id": self.state.current_execution_id,
            "processed": self.state.processed_count,
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
                note=f"phase-03 task consumer ({settings.worker_name})",
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


class WorkerRuntime:
    """One worker process: heartbeat, recovery and the claim/execute loop."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        processor: DemoProcessor | None = None,
        lease_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._executions = ExecutionService()
        self._processor = processor or DemoProcessor(
            session_factory, slow_step_seconds=settings.demo_slow_step_seconds
        )
        self._lease_seconds = lease_seconds or settings.worker_lease_seconds
        self._poll_interval = poll_interval_seconds or settings.worker_poll_interval_seconds
        self.state = WorkerState()

    # ------------------------------------------------------------------ recovery
    def recover(self) -> list[int]:
        """Recover attempts whose lease expired before this worker started."""
        try:
            with self._session_factory() as session:
                recovered = self._executions.recover_expired_leases(
                    session,
                    idempotent_processor=self._processor.is_idempotent(),
                )
                session.commit()
            if recovered:
                logger.info("recovered interrupted tasks", extra={"count": len(recovered)})
            return recovered
        except Exception as exc:
            # Recovery is best-effort per scan: a failure (database error, or a
            # business condition the service did not absorb) must never abort the
            # worker loop or stop it consuming other tasks. The next scan retries;
            # the transaction has already been rolled back by the context manager.
            logger.warning("recovery failed", extra={"error": type(exc).__name__}, exc_info=True)
            return []

    # -------------------------------------------------------------------- claim
    def claim_one(self) -> tuple[int, int, int, int, str, int] | None:
        try:
            with self._session_factory() as session:
                claim = self._executions.claim_next(
                    session,
                    worker_id=self._settings.worker_name,
                    lease_seconds=self._lease_seconds,
                )
                session.commit()
        except SQLAlchemyError as exc:
            logger.warning("claim failed", extra={"error": type(exc).__name__})
            return None
        if claim is None:
            return None
        self.state.last_claim_at = datetime.now(tz=UTC)
        self.state.current_task_id = claim.task_id
        self.state.current_execution_id = claim.execution_id
        return (
            claim.execution_id,
            claim.task_id,
            claim.project_id,
            claim.attempt,
            claim.demo_behavior,
            claim.fencing_token,
        )

    def process_claim(self, claim_tuple: tuple[int, int, int, int, str, int]) -> None:
        execution_id, task_id, project_id, attempt, behavior, fencing_token = claim_tuple
        claim = ClaimedExecution(
            execution_id=execution_id,
            task_id=task_id,
            project_id=project_id,
            attempt=attempt,
            fencing_token=fencing_token,
            demo_behavior=behavior,
            task_budget_tokens=0,
            title="",
        )
        stop_heartbeat = threading.Event()

        def _heartbeat_loop() -> None:
            while not stop_heartbeat.wait(self._lease_seconds / 3):
                try:
                    with self._session_factory() as session:
                        held = self._executions.heartbeat(
                            session,
                            execution_id=execution_id,
                            fencing_token=fencing_token,
                            lease_seconds=self._lease_seconds,
                        )
                        session.commit()
                    if not held:
                        logger.warning(
                            "lease lost while working", extra={"execution_id": execution_id}
                        )
                        return
                except SQLAlchemyError:
                    # A transient database error does not end the attempt; the
                    # next beat or the final write will decide with the token.
                    continue

        beat = threading.Thread(target=_heartbeat_loop, name=f"lease-{execution_id}", daemon=True)
        beat.start()
        try:
            outcome = self._processor.process(claim, lease_seconds=self._lease_seconds)
        except Exception as exc:
            logger.exception("processor crashed", extra={"execution_id": execution_id})
            outcome = ProcessorOutcome(
                succeeded=False,
                summary=None,
                prompt_tokens=None,
                completion_tokens=None,
                usage_known=False,
                error_code="PROCESSOR_ERROR",
                error_message=f"处理器异常（{type(exc).__name__}）。",
                retryable=False,
            )
        finally:
            stop_heartbeat.set()
            beat.join(timeout=2)

        try:
            with self._session_factory() as session:
                self._executions.complete(
                    session,
                    execution_id=execution_id,
                    fencing_token=fencing_token,
                    outcome=outcome,
                    lease_seconds=self._lease_seconds,
                )
                session.commit()
            self.state.processed_count += 1
        except Exception as exc:
            logger.warning(
                "result rejected",
                extra={"execution_id": execution_id, "error": type(exc).__name__},
            )
        finally:
            self.state.current_task_id = None
            self.state.current_execution_id = None

    # --------------------------------------------------------------------- loop
    def run_once(self) -> bool:
        """One claim/execute cycle. Returns True when a task was processed."""
        claim = self.claim_one()
        if claim is None:
            return False
        self.process_claim(claim)
        return True

    def run(self) -> int:
        self.state.started_at = datetime.now(tz=UTC)
        record_heartbeat(self._session_factory, self._settings, self.state)
        self.recover()
        logger.info(
            "worker starting (task consumer)",
            extra={
                "worker": self._settings.worker_name,
                "lease_seconds": self._lease_seconds,
                "poll_seconds": self._poll_interval,
                "processor": self._processor.label,
            },
        )
        last_recovery = time.monotonic()
        while not self.state.stopping.is_set():
            record_heartbeat(self._session_factory, self._settings, self.state)
            # Periodic recovery catches attempts orphaned while this worker idles.
            if time.monotonic() - last_recovery >= max(self._lease_seconds, 5.0):
                self.recover()
                last_recovery = time.monotonic()
            if not self.run_once():
                self.state.stopping.wait(self._poll_interval)
        logger.info("worker stopped", extra={"processed": self.state.processed_count})
        return 0


def run() -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    runtime = WorkerRuntime(settings, session_factory)
    server = start_health_server(settings, session_factory, runtime.state)

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("worker received signal", extra={"signal": signum})
        runtime.state.stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError):  # pragma: no cover - not main thread
            signal.signal(sig, _handle_signal)

    try:
        runtime.run()
    finally:
        server.shutdown()
        server.server_close()
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
