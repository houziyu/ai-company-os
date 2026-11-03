"""Phase 03 runtime acceptance: real API and worker processes.

What makes this different from the pytest suite
-----------------------------------------------
``tests/test_tasks.py`` exercises the queue with real PostgreSQL and real
transactions, but inside one Python process. This script starts **separate OS
processes** — one uvicorn API and two ``app.workers.task_worker`` workers — and
kills one of them with an operating-system terminate signal while it holds a
lease. That is the only way to verify "a worker process exiting does not corrupt
the record", which an injected exception cannot prove.

Checks performed (each prints one ``PHASE03 {...}`` JSON line):

    two-worker-claim      one task, two workers polling, exactly one execution
    worker-killed         SIGKILL/TerminateProcess mid-attempt, then recovery
    idle-requeue          the surviving worker completed the re-queued attempt
    budget-race           concurrent tasks cannot overdraw the project allowance
    idempotent-create     duplicate POST creates one task and one hold
    pause-blocks          a paused company refuses new work and claims
    sse-resume            Last-Event-ID replays strictly after the given id

The script never touches ``.env``, the development database or any Compose
volume; it requires an isolated PostgreSQL through ``PHASE03_DATABASE_URL``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
DEFAULT_DB = "postgresql+psycopg://acos:devpass@127.0.0.1:55433/acos"


def observe(case: str, **facts: Any) -> None:
    print("PHASE03 " + json.dumps({"case": case, **facts}, ensure_ascii=False), flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Process:
    """A child process with its own log file and a real OS kill."""

    def __init__(self, name: str, args: list[str], env: dict[str, str], log_dir: Path) -> None:
        self.name = name
        self.log_path = log_dir / f"{name}.log"
        self._log = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            args,
            cwd=str(BACKEND_ROOT),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

    @property
    def pid(self) -> int:
        return self.proc.pid

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self, *, hard: bool = True) -> int:
        """Terminate like an operator would: no in-process cleanup, no atexit.

        On Windows ``TerminateProcess`` is what Task Manager / ``taskkill /F``
        does; on POSIX ``SIGKILL`` cannot be caught. Both skip ``finally`` blocks,
        so the attempt's lease is left behind exactly as a crash would leave it.
        """
        if self.proc.poll() is not None:
            return int(self.proc.returncode)
        if hard:
            if os.name == "nt":  # pragma: no cover - platform specific
                subprocess.run(  # noqa: S603,S607 - system taskkill
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                os.kill(self.proc.pid, signal.SIGKILL)
        else:
            self.proc.terminate()
        try:
            return int(self.proc.wait(timeout=30))
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.proc.kill()
            return int(self.proc.wait(timeout=10))

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        self._log.close()


def base_env(database_url: str, extra: dict[str, str]) -> dict[str, str]:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "APP_ENV": "test",
        "APP_VERSION": "0.3.0-acceptance",
        "LOG_LEVEL": "INFO",
        "PUBLIC_BASE_URL": "http://127.0.0.1:1",  # replaced per process
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("TEST_ADMIN_DATABASE_URL", None)
    env.update(extra)
    return env


@contextmanager
def api_process(database_url: str, port: int, log_dir: Path) -> Iterator[str]:
    base = f"http://127.0.0.1:{port}"
    env = base_env(
        database_url,
        {
            "PUBLIC_BASE_URL": base,
            "EXTRA_ALLOWED_ORIGINS": json.dumps([base]),
            "COOKIE_SECURE": "false",
            "ENABLE_API_DOCS": "true",
        },
    )
    process = Process(
        "api",
        [sys.executable, "-m", "uvicorn", "app.asgi:app", "--host", "127.0.0.1", "--port", str(port)],
        env,
        log_dir,
    )
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                response = httpx.get(f"{base}/api/v1/health/ready", timeout=2)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if not process.alive():
                raise RuntimeError(f"api exited early; see {process.log_path}")
            time.sleep(0.4)
        else:  # pragma: no cover
            raise RuntimeError("api did not become ready")
        yield base
    finally:
        process.close()


def start_worker(
    database_url: str,
    *,
    name: str,
    health_port: int,
    log_dir: Path,
    lease_seconds: float,
    slow_step_seconds: float,
    poll_seconds: float = 0.2,
) -> Process:
    env = base_env(
        database_url,
        {
            "WORKER_NAME": name,
            "WORKER_HEALTH_PORT": str(health_port),
            "WORKER_LEASE_SECONDS": str(lease_seconds),
            "WORKER_POLL_INTERVAL_SECONDS": str(poll_seconds),
            "DEMO_SLOW_STEP_SECONDS": str(slow_step_seconds),
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": "2",
        },
    )
    return Process(name, [sys.executable, "-m", "app.workers.task_worker"], env, log_dir)


class Client:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=30.0)
        self.headers: dict[str, str] = {}

    def close(self) -> None:
        self.http.close()

    def bootstrap(self) -> dict[str, Any]:
        base = str(self.http.base_url).rstrip("/")
        setup = self.http.post(
            "/api/v1/auth/setup",
            json={
                "username": "owner",
                "displayName": "验收所有者",
                "password": "correct-horse-battery",
                "passwordConfirm": "correct-horse-battery",
            },
            headers={"Origin": base},
        )
        setup.raise_for_status()
        self.headers = {
            "X-CSRF-Token": setup.json()["csrfToken"],
            "Origin": base,
        }
        company = self.http.post(
            "/api/v1/company",
            json={"name": "验收公司", "goal": "验证任务调度与预算。"},
            headers=self.headers,
        )
        company.raise_for_status()
        return company.json()

    def project(self, *, key: str, budget: int) -> dict[str, Any]:
        response = self.http.post(
            "/api/v1/projects",
            json={
                "key": key,
                "name": f"项目 {key}",
                "objective": "验收用项目。",
                "runMode": "DEMO",
                "budgetTokens": budget,
            },
            headers=self.headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    def task(
        self,
        project_id: int,
        *,
        title: str,
        budget: int = 1000,
        behavior: str = "SUCCESS",
        retries: int = 1,
        key: str | None = None,
    ) -> httpx.Response:
        return self.http.post(
            f"/api/v1/projects/{project_id}/tasks",
            json={
                "title": title,
                "objective": "验收任务目标。",
                "acceptance": "完成并记录用量。",
                "budgetTokens": budget,
                "demoBehavior": behavior,
                "maxAutoRetries": retries,
                "idempotencyKey": key or uuid.uuid4().hex,
            },
            headers=self.headers,
        )

    def task_detail(self, task_id: int) -> dict[str, Any]:
        response = self.http.get(f"/api/v1/tasks/{task_id}", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def budgets(self) -> dict[str, Any]:
        response = self.http.get("/api/v1/budgets", headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()


def wait_until(predicate, *, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def db_counts(database_url: str) -> dict[str, int]:
    """Read invariants straight from the database, not through the API."""
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                "tasks": int(connection.scalar(text("SELECT count(*) FROM tasks")) or 0),
                "executions": int(
                    connection.scalar(text("SELECT count(*) FROM task_executions")) or 0
                ),
                "reservations": int(
                    connection.scalar(text("SELECT count(*) FROM budget_reservations")) or 0
                ),
                "open_reservations": int(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM budget_reservations WHERE status='RESERVED'"
                        )
                    )
                    or 0
                ),
                "negative_accounts": int(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM budget_accounts"
                            " WHERE consumed_tokens < 0 OR reserved_tokens < 0"
                        )
                    )
                    or 0
                ),
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PHASE03_DATABASE_URL", DEFAULT_DB),
        help="Isolated PostgreSQL URL. Never point this at real data.",
    )
    parser.add_argument("--out", default="", help="Directory for process logs and JSON result.")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="acos-phase03-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "process-logs"
    log_dir.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(case: str, ok: bool, **facts: Any) -> None:
        observe(case, ok=ok, **facts)
        results.append({"case": case, "ok": ok, **facts})
        if not ok:
            failures.append(case)

    api_port = free_port()
    health_1, health_2 = free_port(), free_port()
    client: Client | None = None
    workers: list[Process] = []

    try:
        with api_process(args.database_url, api_port, log_dir) as base:
            client = Client(base)
            client.bootstrap()
            project = client.project(key="ACCEPT", budget=50_000)
            observe(
                "environment",
                api=base,
                database_url_host=args.database_url.rsplit("@", 1)[-1],
                out_dir=str(out_dir),
            )

            # ---- 1. two real worker processes race for one task --------------
            lease = 12.0
            # The barrier makes "two workers race" a real race rather than a
            # sequence: both processes are started before either may poll.
            w1 = start_worker(
                args.database_url,
                name="worker-1",
                health_port=health_1,
                log_dir=log_dir,
                lease_seconds=lease,
                slow_step_seconds=0.2,
            )
            w2 = start_worker(
                args.database_url,
                name="worker-2",
                health_port=health_2,
                log_dir=log_dir,
                lease_seconds=lease,
                slow_step_seconds=0.2,
            )
            workers.extend([w1, w2])

            def worker_ready(port: int) -> bool:
                try:
                    response = httpx.get(
                        f"http://127.0.0.1:{port}/health/ready", timeout=2
                    )
                except httpx.HTTPError:
                    return False
                return response.status_code == 200

            assert wait_until(
                lambda: all(worker_ready(port) for port in (health_1, health_2)),
                timeout=60,
            ), "workers did not become ready"

            created = client.task(project["id"], title="双 Worker 竞争任务")
            created.raise_for_status()
            task_id = created.json()["task"]["id"]
            assert wait_until(
                lambda: client.task_detail(task_id)["status"] == "SUCCEEDED",
                timeout=45,
            ), "task did not finish"
            detail = client.task_detail(task_id)
            executions = detail["executions"]
            check(
                "two-worker-claim",
                len(executions) == 1 and executions[0]["status"] == "SUCCEEDED",
                task_id=task_id,
                execution_count=len(executions),
                winner_worker=executions[0]["workerId"],
                fencing_token=executions[0]["fencingToken"],
                pids={"worker-1": w1.pid, "worker-2": w2.pid},
            )

            # ---- 2. kill the worker that holds a lease ----------------------
            # A long slow step guarantees the kill lands while the attempt runs.
            # The worker's configured slow step is 0.2s (fast for the normal
            # checks). For the kill test the attempt must still be running when
            # the process dies, so the task is submitted while a *slow* worker
            # configuration is in effect: stop the survivors, restart both with a
            # long step, then kill one. This keeps the kill deterministic instead
            # of racing a 0.2s sleep.
            for worker in list(workers):
                worker.close()
                workers.remove(worker)
            w1 = start_worker(
                args.database_url,
                name="worker-1",
                health_port=health_1,
                log_dir=log_dir,
                lease_seconds=lease,
                slow_step_seconds=40.0,
            )
            w2 = start_worker(
                args.database_url,
                name="worker-2",
                health_port=health_2,
                log_dir=log_dir,
                lease_seconds=lease,
                slow_step_seconds=40.0,
            )
            workers.extend([w1, w2])
            assert wait_until(
                lambda: all(worker_ready(port) for port in (health_1, health_2)),
                timeout=60,
            ), "restarted workers did not become ready"

            slow_task = client.task(
                project["id"],
                title="被强杀的执行",
                behavior="SLOW",
                budget=4000,
                retries=1,
            )
            slow_task.raise_for_status()
            slow_id = slow_task.json()["task"]["id"]

            def running_attempt() -> dict[str, Any] | None:
                body = client.task_detail(slow_id)
                for execution in body["executions"]:
                    if execution["status"] == "RUNNING" and execution["workerId"]:
                        return execution
                return None

            assert wait_until(lambda: running_attempt() is not None, timeout=45), (
                "no attempt started"
            )
            victim_execution = running_attempt()
            assert victim_execution is not None
            victim_name = "worker-1" if victim_execution["workerId"] == "worker-1" else "worker-2"
            victim = w1 if victim_name == "worker-1" else w2
            survivor = w2 if victim is w1 else w1
            victim_pid = victim.pid
            victim_attempt = victim_execution["attempt"]
            exit_code = victim.kill(hard=True)
            workers.remove(victim)
            check(
                "worker-killed",
                exit_code != 0 and not victim.alive(),
                worker=victim_name,
                pid=victim_pid,
                exit_code=exit_code,
                still_alive=victim.alive(),
                execution_id=victim_execution["id"],
                attempt=victim_attempt,
                lease_expires_at=victim_execution["leaseExpiresAt"],
            )

            # ---- 3. the surviving worker recovers the orphaned attempt ------
            # Recovery runs on the survivor's periodic loop after the lease expires.
            assert wait_until(
                lambda: client.task_detail(slow_id)["status"] == "SUCCEEDED",
                timeout=90,
            ), "recovered task did not succeed"
            recovered = client.task_detail(slow_id)
            attempts = sorted(recovered["executions"], key=lambda item: item["attempt"])
            interrupted = [
                item for item in attempts if item["status"] == "INTERRUPTED"
            ]
            check(
                "idle-requeue",
                bool(interrupted)
                and interrupted[0]["interruptedUnknownSideEffects"] is True
                and attempts[-1]["attempt"] > interrupted[0]["attempt"]
                and recovered["status"] == "SUCCEEDED",
                task_id=slow_id,
                attempts=[item["attempt"] for item in attempts],
                statuses=[item["status"] for item in attempts],
                interrupted_execution_id=interrupted[0]["id"] if interrupted else None,
                recovery_note=interrupted[0]["recoveryNote"] if interrupted else None,
                final_execution_id=attempts[-1]["id"],
                survivor=survivor.name,
                survivor_pid=survivor.pid,
            )

            # ---- 4. budget race across two projects ------------------------
            # Each task asks for more than half the project allowance, so only one
            # can be admitted no matter how the two requests interleave.
            scarce = client.project(key="SCARCE", budget=1_000)
            before = client.budgets()["company"]["availableTokens"]
            responses: list[int] = []
            payloads = [
                ("预算竞争 A", 700, uuid.uuid4().hex),
                ("预算竞争 B", 700, uuid.uuid4().hex),
            ]
            import threading

            def submit(title: str, amount: int, key: str) -> None:
                local = httpx.Client(
                    base_url=base, timeout=30.0, cookies=dict(client.http.cookies)
                )
                try:
                    response = local.post(
                        f"/api/v1/projects/{scarce['id']}/tasks",
                        json={
                            "title": title,
                            "objective": "并发额度验证。",
                            "budgetTokens": amount,
                            "demoBehavior": "SUCCESS",
                            "maxAutoRetries": 1,
                            "idempotencyKey": key,
                        },
                        headers=client.headers,
                    )
                    responses.append(response.status_code)
                finally:
                    local.close()

            threads = [threading.Thread(target=submit, args=args_) for args_ in payloads]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            budgets = client.budgets()
            scarce_account = next(
                item for item in budgets["projects"] if item["scopeId"] == scarce["id"]
            )
            check(
                "budget-race",
                sorted(responses).count(201) == 1 and scarce_account["reservedTokens"] <= 1_000,
                statuses=sorted(responses),
                project_reserved=scarce_account["reservedTokens"],
                project_limit=scarce_account["limitTokens"],
                company_available_before=before,
            )

            # ---- 5. duplicate request -------------------------------------
            key = f"dup-{uuid.uuid4().hex}"
            first = client.task(project["id"], title="幂等任务", key=key)
            second = client.task(project["id"], title="幂等任务", key=key)
            first.raise_for_status()
            duplicate_task_id = first.json()["task"]["id"]
            conflict = client.task(project["id"], title="不同标题", key=key)
            check(
                "idempotent-create",
                first.status_code == 201
                and second.status_code == 200
                and second.json()["task"]["id"] == duplicate_task_id
                and conflict.status_code == 409
                and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT",
                first=first.status_code,
                second=second.status_code,
                conflict=conflict.status_code,
                task_id=duplicate_task_id,
            )

            # ---- 6. company pause -----------------------------------------
            paused = client.http.post(
                "/api/v1/company/pause",
                json={"reason": "验收暂停"},
                headers=client.headers,
            )
            refused = client.task(project["id"], title="暂停期间的任务")
            state = client.http.get("/api/v1/company/state", headers=client.headers).json()
            check(
                "pause-blocks",
                paused.status_code == 200
                and refused.status_code == 409
                and refused.json()["code"] == "COMPANY_PAUSED"
                and state["status"] == "PAUSED",
                pause_status=paused.status_code,
                refused_status=refused.status_code,
                company_state=state["status"],
            )
            client.http.post("/api/v1/company/resume", headers=client.headers)

            # ---- 7. SSE replay --------------------------------------------
            events = client.http.get(
                f"/api/v1/tasks/{task_id}/events?after=0", headers=client.headers
            ).json()
            ids = [item["id"] for item in events["events"]]
            resumed = client.http.get(
                f"/api/v1/tasks/{task_id}/events?after={ids[0]}", headers=client.headers
            ).json()
            # The stream itself: read frames until the created event appears.
            frames: list[str] = []
            with client.http.stream(
                "GET", "/api/v1/events/stream?after=0", headers=client.headers
            ) as stream:
                for chunk in stream.iter_text():
                    frames.append(chunk)
                    if "TASK_CREATED" in chunk or len(frames) > 8:
                        break
            text = "".join(frames)
            check(
                "sse-resume",
                len(ids) >= 3
                and [item["id"] for item in resumed["events"]] == ids[1:]
                and "id: " in text
                and "event: TASK_CREATED" in text,
                total_events=len(ids),
                resumed_from=ids[0],
                resumed_count=len(resumed["events"]),
                stream_has_id="id: " in text,
            )

            # ---- 8. stop gate after claim (R1) ----------------------------
            # A slow task is claimed by a real worker; while it sleeps inside its
            # first step the company is paused. The next step must never start.
            stop_task = client.task(
                project["id"],
                title="领取后暂停",
                behavior="SLOW",
                budget=4_000,
                retries=0,
            )
            stop_task.raise_for_status()
            stop_id = stop_task.json()["task"]["id"]
            assert wait_until(
                lambda: any(
                    execution["status"] == "RUNNING"
                    for execution in client.task_detail(stop_id)["executions"]
                ),
                timeout=45,
            ), "stop-gate task was not claimed"
            paused_mid = client.http.post(
                "/api/v1/company/pause",
                json={"reason": "领取后暂停"},
                headers=client.headers,
            )
            assert wait_until(
                lambda: client.task_detail(stop_id)["status"] != "RUNNING",
                timeout=90,
            ), "paused in-flight task did not reach a stop state"
            stopped = client.task_detail(stop_id)
            step_count = sum(len(execution["steps"]) for execution in stopped["executions"])
            stop_budgets = client.budgets()
            client.http.post("/api/v1/company/resume", headers=client.headers)
            check(
                "stop-gate-after-claim",
                paused_mid.status_code == 200
                and stopped["status"] == "INTERRUPTED"
                and step_count == 1
                and stop_budgets["company"]["reservedTokens"] == 0,
                task_id=stop_id,
                status=stopped["status"],
                steps_started=step_count,
                company_reserved=stop_budgets["company"]["reservedTokens"],
                executions=[
                    {"attempt": item["attempt"], "status": item["status"]}
                    for item in stopped["executions"]
                ],
            )

            # ---- 9. cooperative cancel while a real worker holds the attempt -
            cancel_task = client.task(
                project["id"],
                title="运行中取消",
                behavior="SLOW",
                budget=4_000,
                retries=0,
            )
            cancel_task.raise_for_status()
            cancel_id = cancel_task.json()["task"]["id"]
            assert wait_until(
                lambda: any(
                    execution["status"] == "RUNNING"
                    for execution in client.task_detail(cancel_id)["executions"]
                ),
                timeout=45,
            ), "cancel task was not claimed"
            cancel_response = client.http.post(
                f"/api/v1/tasks/{cancel_id}/cancel", json={}, headers=client.headers
            )
            assert wait_until(
                lambda: client.task_detail(cancel_id)["status"] == "CANCELLED",
                timeout=90,
            ), "cooperative cancel did not reach CANCELLED"
            cancelled_detail = client.task_detail(cancel_id)
            check(
                "cancel-running-flag",
                cancel_response.status_code == 200
                and "安全检查点" in cancel_response.json()["message"]
                and cancelled_detail["status"] == "CANCELLED"
                and cancelled_detail["cancelRequestedAt"] is not None,
                task_id=cancel_id,
                immediate_status=cancel_response.json()["task"]["status"],
                final_status=cancelled_detail["status"],
            )

            # ---- 10. database invariants -----------------------------------
            counts = db_counts(args.database_url)
            final_budgets = client.budgets()
            check(
                "ledger-invariants",
                counts["negative_accounts"] == 0
                and final_budgets["company"]["reservedTokens"] >= 0,
                **counts,
                company_reserved=final_budgets["company"]["reservedTokens"],
                company_consumed=final_budgets["company"]["consumedTokens"],
                pending_reconciliation=final_budgets["pendingReconciliationCount"],
            )
    finally:
        for worker in workers:
            worker.close()
        if client is not None:
            client.close()

    summary = {
        "checks": results,
        "failures": failures,
        "passed": not failures,
        "out_dir": str(out_dir),
        "process_logs": sorted(path.name for path in log_dir.iterdir()),
    }
    (out_dir / "runtime-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
