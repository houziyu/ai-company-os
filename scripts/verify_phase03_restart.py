"""Phase 03 stack-restart acceptance: queued and in-flight work across a restart.

Two facts that need the whole stack to stop and start again:

    restart-queued     a task queued while the workers are stopped is executed
                       after the stack comes back up (the queue is in PostgreSQL,
                       not in memory)
    restart-running    an attempt that was RUNNING when the stack stopped is
                       recovered as INTERRUPTED and re-queued, never silently
                       treated as finished

The script stops only the isolated Phase 03 project. It never touches the
development stack, and it never removes volumes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT = "acos-phase03-test"


def observe(case: str, **facts: Any) -> None:
    print("PHASE03R " + json.dumps({"case": case, **facts}, ensure_ascii=False), flush=True)


def compose(
    *args: str,
    check: bool = True,
    slow_seconds: str = "20",
) -> subprocess.CompletedProcess[str]:
    """Run one compose command against the isolated Phase 03 project.

    ``slow_seconds`` is a parameter because the restart test needs a step longer
    than Docker's 20s stop grace period: only then does the container get
    SIGKILLed instead of finishing its work during a graceful stop.
    """
    env = dict(os.environ)
    env.update(
        {
            "POSTGRES_USER": "acos_p3",
            "POSTGRES_PASSWORD": "isolated-phase03-test-only",
            "POSTGRES_DB": "acos_p3",
            "DATABASE_URL": (
                "postgresql+psycopg://acos_p3:isolated-phase03-test-only@postgres:5432/acos_p3"
            ),
            "PUBLIC_BASE_URL": "http://127.0.0.1:18085",
            "WEB_PORT": "18085",
            "WEB_BIND_ADDRESS": "127.0.0.1",
            "DEEPSEEK_API_KEY": "",
            "SECRET_ENCRYPTION_KEY": "cGhhc2UwMy10ZXN0LW9ubHktMzItYnl0ZS1rZXkhISE=",
            "WORKER_LEASE_SECONDS": "12",
            "DEMO_SLOW_STEP_SECONDS": slow_seconds,
        }
    )
    return subprocess.run(  # noqa: S603 - fixed argv
        [
            "docker",
            "compose",
            "--project-directory",
            str(REPO_ROOT),
            "--env-file",
            str(REPO_ROOT / ".env.example"),
            "-p",
            PROJECT,
            "-f",
            str(REPO_ROOT / "docker-compose.yml"),
            "-f",
            str(REPO_ROOT / "deploy" / "compose.phase03-test.yml"),
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


class Client:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=30.0)
        origin = base
        login = self.http.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "correct-horse-battery"},
            headers={"Origin": origin},
        )
        login.raise_for_status()
        self.headers = {"X-CSRF-Token": login.json()["csrfToken"], "Origin": origin}

    def tasks(self) -> list[dict[str, Any]]:
        response = self.http.get("/api/v1/tasks?limit=200", headers=self.headers)
        response.raise_for_status()
        return response.json()["tasks"]

    def create(self, project_id: int, title: str, behavior: str) -> dict[str, Any]:
        response = self.http.post(
            f"/api/v1/projects/{project_id}/tasks",
            json={
                "title": title,
                "objective": "重启恢复验收。",
                "budgetTokens": 2000,
                "demoBehavior": behavior,
                "maxAutoRetries": 2,
                "idempotencyKey": uuid.uuid4().hex,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()["task"]

    def detail(self, task_id: int) -> dict[str, Any]:
        response = self.http.get(f"/api/v1/tasks/{task_id}", headers=self.headers)
        response.raise_for_status()
        return response.json()

    def project_id(self) -> int:
        response = self.http.get("/api/v1/projects", headers=self.headers)
        response.raise_for_status()
        return int(response.json()["projects"][0]["id"])


def wait_until(predicate, *, timeout: float, interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except (httpx.HTTPError, AssertionError):
            pass
        time.sleep(interval)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18085")
    parser.add_argument(
        "--out", default="docs/acceptance/evidence/phase-03/restart"
    )
    args = parser.parse_args()
    out_dir = (REPO_ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(case: str, ok: bool, **facts: Any) -> None:
        observe(case, ok=ok, **facts)
        results.append({"case": case, "ok": ok, **facts})
        if not ok:
            failures.append(case)

    # ---- 1. queue a task while the workers are stopped -------------------
    compose("stop", "worker", "worker2")
    client = Client(base)
    project_id = client.project_id()
    queued = client.create(project_id, "停机期间排队的任务", "SUCCESS")
    queued_after = client.detail(queued["id"])
    check(
        "restart-queued-before",
        queued_after["status"] == "QUEUED" and queued_after["executions"] == [],
        task_id=queued["id"],
        status=queued_after["status"],
        executions=len(queued_after["executions"]),
        workers="stopped",
    )

    # ---- 2. and an in-flight attempt when the whole stack stops -----------
    # Recreate the workers with a 45s step. Docker's stop grace period is 20s,
    # so `compose stop` sends SIGTERM and then SIGKILLs the process while it is
    # still working: the attempt is genuinely interrupted, not finished
    # gracefully during shutdown.
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--scale",
        "worker=1",
        "--scale",
        "worker2=1",
        "worker",
        "worker2",
        slow_seconds="45",
    )
    assert wait_until(
        lambda: all(
            "healthy" in compose("ps", service, slow_seconds="45").stdout
            for service in ("worker", "worker2")
        ),
        timeout=180,
    ), "worker did not return"
    inflight = client.create(project_id, "重启期间中断的任务", "SLOW")

    def running() -> bool:
        return any(
            row["status"] == "RUNNING" for row in client.detail(inflight["id"])["executions"]
        )

    assert wait_until(running, timeout=120), "no running attempt before restart"
    before_restart = client.detail(inflight["id"])
    running_row = next(
        row for row in before_restart["executions"] if row["status"] == "RUNNING"
    )
    compose("stop", "worker", "worker2", slow_seconds="45")
    stopped_at = time.time()

    # The container is gone while its lease is still nominally valid. Nothing in
    # the database is rewritten by stopping: that is the point of a lease.
    check(
        "restart-inflight-stopped",
        running_row["leaseExpiresAt"] is not None,
        task_id=inflight["id"],
        execution_id=running_row["id"],
        attempt=running_row["attempt"],
        worker=running_row["workerId"],
        lease_expires_at=running_row["leaseExpiresAt"],
        stopped_at_epoch=round(stopped_at, 1),
    )

    # ---- 3. bring the stack back and let recovery run --------------------
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--scale",
        "worker=1",
        "--scale",
        "worker2=1",
        "worker",
        "worker2",
        slow_seconds="2",
    )
    assert wait_until(
        lambda: all(
            "healthy" in compose("ps", service, slow_seconds="2").stdout
            for service in ("worker", "worker2")
        ),
        timeout=180,
    ), "workers did not become healthy after restart"

    assert wait_until(
        lambda: client.detail(queued["id"])["status"] == "SUCCEEDED", timeout=180
    ), "queued task did not run after restart"
    queued_after = client.detail(queued["id"])
    check(
        "restart-queued",
        queued_after["status"] == "SUCCEEDED"
        and len(queued_after["executions"]) >= 1
        and queued_after["executions"][0]["attempt"] == 1,
        task_id=queued["id"],
        status=queued_after["status"],
        executions=len(queued_after["executions"]),
        executed_by=queued_after["executions"][0]["workerId"],
    )

    assert wait_until(
        lambda: client.detail(inflight["id"])["status"] in {"SUCCEEDED", "INTERRUPTED"},
        timeout=240,
    ), "in-flight task did not settle after restart"
    recovered = client.detail(inflight["id"])
    attempts = sorted(recovered["executions"], key=lambda row: row["attempt"])
    interrupted = [row for row in attempts if row["status"] == "INTERRUPTED"]
    check(
        "restart-running",
        bool(interrupted)
        and interrupted[0]["interruptedUnknownSideEffects"] is True
        and attempts[-1]["attempt"] > interrupted[0]["attempt"]
        and recovered["status"] == "SUCCEEDED",
        task_id=inflight["id"],
        statuses=[row["status"] for row in attempts],
        attempts=[row["attempt"] for row in attempts],
        recovery_note=interrupted[0]["recoveryNote"] if interrupted else None,
        final_status=recovered["status"],
    )

    (out_dir / "compose-ps-after-restart.txt").write_text(
        compose("ps").stdout, encoding="utf-8"
    )
    client.http.close()

    summary = {
        "checks": results,
        "failures": failures,
        "passed": not failures,
        "out_dir": str(out_dir),
    }
    (out_dir / "restart-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
