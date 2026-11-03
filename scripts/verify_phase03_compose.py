"""Phase 03 Compose-level acceptance: two worker *containers* and a real kill.

The Python runtime script already proves the process-level behaviour. This script
adds the container facts that only Compose can show:

    compose-scale        two worker containers from one image, both healthy
    container-claim      one task, two containers polling, one execution
    container-killed     ``docker kill`` the container holding the lease
    container-recovery   the surviving container re-queues and completes it
    restart-recovery     a stopped stack resumes queued work after ``up``

It talks to the isolated Phase 03 stack on 127.0.0.1:18085 and shells out to
``docker`` only for lifecycle actions (scale/kill/stop), never to read business
state — that comes from the API and the database.

Usage (from the repository root, with the stack already up):

    python scripts/verify_phase03_compose.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT = "acos-phase03-test"
WORKER_SERVICES = ("worker", "worker2")


def observe(case: str, **facts: Any) -> None:
    print("PHASE03C " + json.dumps({"case": case, **facts}, ensure_ascii=False), flush=True)


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["POSTGRES_USER"] = "acos_p3"
    env["POSTGRES_PASSWORD"] = "isolated-phase03-test-only"
    env["POSTGRES_DB"] = "acos_p3"
    env["DATABASE_URL"] = (
        "postgresql+psycopg://acos_p3:isolated-phase03-test-only@postgres:5432/acos_p3"
    )
    env["PUBLIC_BASE_URL"] = "http://127.0.0.1:18085"
    env["WEB_PORT"] = "18085"
    env["WEB_BIND_ADDRESS"] = "127.0.0.1"
    env["DEEPSEEK_API_KEY"] = ""
    env["SECRET_ENCRYPTION_KEY"] = "cGhhc2UwMy10ZXN0LW9ubHktMzItYnl0ZS1rZXkhISE="
    env["WORKER_LEASE_SECONDS"] = "12"
    env["DEMO_SLOW_STEP_SECONDS"] = "20"
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


def container_id(service: str) -> str:
    result = compose("ps", "-q", service)
    value = result.stdout.strip().splitlines()
    if not value:
        raise RuntimeError(f"no container for service {service}")
    return value[0]


def wait_until(predicate, *, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except (httpx.HTTPError, AssertionError, KeyError):
            pass
        time.sleep(interval)
    return False


class Client:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=30.0)
        self.headers: dict[str, str] = {}

    def bootstrap(self) -> None:
        status = self.http.get("/api/v1/auth/setup-status").json()
        if not status["setupComplete"]:
            setup = self.http.post(
                "/api/v1/auth/setup",
                json={
                    "username": "owner",
                    "displayName": "验收所有者",
                    "password": "correct-horse-battery",
                    "passwordConfirm": "correct-horse-battery",
                },
                headers={"Origin": "http://127.0.0.1:18085"},
            )
            setup.raise_for_status()
            self.headers = {
                "X-CSRF-Token": setup.json()["csrfToken"],
                "Origin": "http://127.0.0.1:18085",
            }
        else:
            login = self.http.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "correct-horse-battery"},
                headers={"Origin": "http://127.0.0.1:18085"},
            )
            login.raise_for_status()
            self.headers = {
                "X-CSRF-Token": login.json()["csrfToken"],
                "Origin": "http://127.0.0.1:18085",
            }
        if self.http.get("/api/v1/company", headers=self.headers).status_code == 404:
            created = self.http.post(
                "/api/v1/company",
                json={"name": "验收公司", "goal": "容器级任务调度验收。"},
                headers=self.headers,
            )
            created.raise_for_status()

    def project(self) -> dict[str, Any]:
        existing = self.http.get("/api/v1/projects", headers=self.headers).json()["projects"]
        if existing:
            return existing[0]
        response = self.http.post(
            "/api/v1/projects",
            json={
                "key": "COMPOSE",
                "name": "容器验收项目",
                "objective": "验证两个 Worker 容器与容器强杀恢复。",
                "runMode": "DEMO",
                "budgetTokens": 1_000_000,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def task(self, project_id: int, *, title: str, behavior: str) -> dict[str, Any]:
        response = self.http.post(
            f"/api/v1/projects/{project_id}/tasks",
            json={
                "title": title,
                "objective": "容器级验收。",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18085")
    parser.add_argument(
        "--out",
        default="docs/acceptance/evidence/phase-03/compose",
        help="Directory for the JSON result and docker diagnostics.",
    )
    args = parser.parse_args()
    out_dir = (REPO_ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    base = args.base_url.rstrip("/")

    def check(case: str, ok: bool, **facts: Any) -> None:
        observe(case, ok=ok, **facts)
        results.append({"case": case, "ok": ok, **facts})
        if not ok:
            failures.append(case)

    client = Client(base)
    try:
        client.bootstrap()
        project = client.project()

        # ---- scale to two worker containers -----------------------------
        compose("up", "-d", "--scale", "worker=1", "--scale", "worker2=1", "--no-recreate")
        ids = {service: container_id(service) for service in WORKER_SERVICES}
        wait_until(
            lambda: all(
                "healthy" in compose("ps", service).stdout
                for service in WORKER_SERVICES
            ),
            timeout=90,
        )
        states = {
            service: compose("ps", service).stdout.strip() for service in WORKER_SERVICES
        }
        check(
            "compose-scale",
            len({value for value in ids.values()}) == 2
            and all("healthy" in value for value in states.values()),
            containers=ids,
            states=states,
        )

        # ---- one task, two containers ----------------------------------
        racing = client.task(project["id"], title="容器竞争任务", behavior="SUCCESS")
        assert wait_until(
            lambda: client.detail(racing["id"])["status"] == "SUCCEEDED", timeout=120
        ), "container race task did not finish"
        detail = client.detail(racing["id"])
        executions = detail["executions"]
        check(
            "container-claim",
            len(executions) == 1 and executions[0]["workerId"] in {"worker-1", "worker-2"},
            task_id=racing["id"],
            execution_count=len(executions),
            winner=executions[0]["workerId"],
            fencing_token=executions[0]["fencingToken"],
        )

        # ---- kill the container holding a lease ------------------------
        # The stack is configured with a 20s slow step, so the attempt is still
        # running when the container is killed with SIGKILL (docker kill).
        killing = client.task(project["id"], title="容器强杀任务", behavior="SLOW")
        assert wait_until(
            lambda: any(
                row["status"] == "RUNNING" and row["workerId"]
                for row in client.detail(killing["id"])["executions"]
            ),
            timeout=120,
        ), "no running attempt for the kill test"
        running = next(
            row
            for row in client.detail(killing["id"])["executions"]
            if row["status"] == "RUNNING"
        )
        victim_service = "worker" if running["workerId"] == "worker-1" else "worker2"
        victim_id = ids[victim_service]
        kill = compose("kill", victim_service, check=False)
        # docker kill sends SIGKILL: no graceful shutdown, no in-process cleanup.
        check(
            "container-killed",
            kill.returncode == 0,
            service=victim_service,
            container=victim_id,
            worker_id=running["workerId"],
            execution_id=running["id"],
            attempt=running["attempt"],
            kill_exit=kill.returncode,
            kill_stderr=kill.stderr.strip()[:300],
        )

        # ---- the survivor recovers ------------------------------------
        assert wait_until(
            lambda: client.detail(killing["id"])["status"] in {"SUCCEEDED", "INTERRUPTED"},
            timeout=180,
        ), "recovered container task did not settle"
        recovered = client.detail(killing["id"])
        attempts = sorted(recovered["executions"], key=lambda row: row["attempt"])
        interrupted = [row for row in attempts if row["status"] == "INTERRUPTED"]
        check(
            "container-recovery",
            bool(interrupted)
            and interrupted[0]["interruptedUnknownSideEffects"] is True
            and recovered["status"] == "SUCCEEDED"
            and attempts[-1]["attempt"] > interrupted[0]["attempt"],
            task_id=killing["id"],
            statuses=[row["status"] for row in attempts],
            attempts=[row["attempt"] for row in attempts],
            recovery_note=interrupted[0]["recoveryNote"] if interrupted else None,
            final_status=recovered["status"],
        )

        (out_dir / "compose-ps.txt").write_text(
            compose("ps").stdout, encoding="utf-8"
        )
        (out_dir / "worker-logs.txt").write_text(
            compose("logs", "--no-color", "--tail", "200", "worker", "worker2").stdout
            + "\n--- stderr ---\n"
            + compose("logs", "--no-color", "--tail", "200", "worker", "worker2").stderr,
            encoding="utf-8",
        )
    finally:
        client.http.close()

    summary = {
        "checks": results,
        "failures": failures,
        "passed": not failures,
        "out_dir": str(out_dir),
    }
    (out_dir / "compose-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
