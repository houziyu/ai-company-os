"""Verify SSE disconnect/replay through the repository's real nginx config.

Why this exists
---------------
Phase 03's acceptance listed "SSE behind nginx" as unexecuted. The fix round also
changed the replay cursor (review finding R8), so the end-to-end path is proven
behind the proxy production actually uses: ``deploy/nginx/default.conf`` served by
the unprivileged nginx image, with ``/api`` proxied to a real uvicorn process.

What is checked
---------------
1. events arrive through nginx and their ``id:`` cursors are remembered;
2. nginx is stopped (the proxy disappears, as in a restart), a **new** event is
   committed while the client is disconnected, and nginx is started again;
3. the client resumes with ``after=<last id>`` and receives the missed event —
   nothing that committed during the outage is skipped;
4. the resumed batch contains no duplicates.

The only change to the shipped nginx config is the upstream host:port, because the
production file points at the Compose service name. Every other directive —
timeouts, headers, buffering — is the production text.

Only the isolated database in ``--database-url`` is used. ``.env``, the
development stack and Compose volumes are never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
NGINX_DIR = ROOT / "deploy" / "nginx"
NGINX_IMAGE = "nginxinc/nginx-unprivileged:1.27-alpine"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, *, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


class ApiProcess:
    def __init__(self, database_url: str, port: int, proxy_port: int, log_path: Path) -> None:
        env = {
            **os.environ,
            "DATABASE_URL": database_url,
            "APP_ENV": "test",
            "APP_VERSION": "0.3.1-sse-nginx",
            "LOG_LEVEL": "INFO",
            # The browser talks only to the proxy, so its origin is the accepted
            # one; the direct API origin is accepted too for the outage probe.
            "PUBLIC_BASE_URL": f"http://127.0.0.1:{proxy_port}",
            "EXTRA_ALLOWED_ORIGINS": json.dumps([f"http://127.0.0.1:{port}"]),
            "COOKIE_SECURE": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        env.pop("TEST_ADMIN_DATABASE_URL", None)
        self._log = log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.asgi:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        self._log.close()


class NginxContainer:
    """The repository's nginx config in a container, upstream on the host.

    The shipped config resolves the upstream through Docker's embedded DNS
    (``127.0.0.11``), which only exists on a user-defined network. The container is
    attached to a dedicated bridge network so that resolver is real, and the host
    API is reached through the network gateway address.
    """

    def __init__(self, name: str, port: int, mount_dir: Path, api_port: int) -> None:
        self.name = name
        self.port = port
        self.mount_dir = mount_dir
        self.api_port = api_port
        self.network = f"{name}-net"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603,S607 - fixed argv
            ["docker", *args], capture_output=True, text=True, check=False
        )

    def stop(self) -> None:
        self._run("rm", "-f", self.name)
        self._run("network", "rm", self.network)

    def start(self) -> None:
        self.stop()
        self._run("network", "create", self.network)
        # ``host-gateway`` maps the Docker host into the container's /etc/hosts.
        result = self._run(
            "run",
            "-d",
            "--name",
            self.name,
            "--network",
            self.network,
            "--add-host",
            "host.docker.internal:host-gateway",
            "-p",
            f"127.0.0.1:{self.port}:8080",
            "-v",
            f"{self.mount_dir / 'default.conf'}:/etc/nginx/conf.d/default.conf:ro",
            "-v",
            f"{self.mount_dir / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
            "-v",
            f"{self.mount_dir / 'security-headers.conf'}:/etc/nginx/snippets/security-headers.conf:ro",
            NGINX_IMAGE,
        )
        if result.returncode != 0:
            raise RuntimeError(f"nginx failed to start: {result.stderr.strip()}")

    def logs(self) -> str:
        result = self._run("logs", self.name)
        return (result.stdout or "") + (result.stderr or "")


def render_mount_dir(api_port: int, target: Path) -> None:
    """Copy the production nginx config, substituting only the upstream."""
    target.mkdir(parents=True, exist_ok=True)
    source = (NGINX_DIR / "default.conf").read_text(encoding="utf-8")
    needle = 'set $acos_api_upstream "http://api:8000";'
    if needle not in source:  # pragma: no cover - guards config drift
        raise RuntimeError(f"upstream line not found in {NGINX_DIR / 'default.conf'}")
    rendered = source.replace(
        needle, f'set $acos_api_upstream "http://host.docker.internal:{api_port}";'
    )
    (target / "default.conf").write_text(rendered, encoding="utf-8")
    for name in ("nginx.conf", "security-headers.conf"):
        (target / name).write_bytes((NGINX_DIR / name).read_bytes())


@contextmanager
def read_sse(
    client: httpx.Client,
    base: str,
    *,
    after: int,
    quiet_seconds: float = 3.0,
    max_seconds: float = 25.0,
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """Read frames until events stop arriving, yielding (last cursor, events).

    The stream sends ``: keep-alive`` comment frames every second, so "no bytes"
    is not the stop condition — "no *event*" is. The read also has a hard cap so a
    misbehaving stream cannot hang the check.

    The read is deliberately *client-terminated*: it stops after a quiet period,
    which is how a real browser outage looks from the server. httpx reports the
    resulting half-closed chunked response as ``RemoteProtocolError`` when the
    context exits, so that specific error is swallowed — the frames already parsed
    are the evidence. Any other transport error is re-raised.
    """
    events: list[dict[str, Any]] = []
    last_id = after
    buffer = ""
    current: dict[str, Any] = {}
    started = time.time()
    last_event_at = time.time()
    try:
        with client.stream(
            "GET", f"{base}/api/v1/events/stream", params={"after": after}, timeout=60
        ) as response:
            assert response.status_code == 200, response.status_code
            for chunk in response.iter_bytes():
                if chunk:
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.rstrip("\r")
                        if line.startswith("id: "):
                            current["id"] = int(line[4:])
                            last_id = current["id"]
                        elif line.startswith("event: "):
                            current["type"] = line[7:]
                        elif line.startswith("data: "):
                            current["data"] = json.loads(line[6:])
                            events.append(current)
                            current = {}
                            # A completed event is what resets the quiet timer;
                            # keep-alive comments do not.
                            last_event_at = time.time()
                if time.time() - last_event_at > quiet_seconds:
                    break
                if time.time() - started > max_seconds:  # pragma: no cover - guard
                    break
    except httpx.RemoteProtocolError:
        # Expected: the client closed a streaming response on purpose.
        pass
    yield last_id, events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url", default=os.environ.get("PHASE03_DATABASE_URL", "")
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "acceptance" / "evidence" / "phase-03-fixes" / "nginx-sse"),
    )
    args = parser.parse_args()
    if not args.database_url:
        print("--database-url (or PHASE03_DATABASE_URL) is required", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    api_port, nginx_port = free_port(), free_port()
    name = f"acos-sse-nginx-{uuid.uuid4().hex[:6]}"
    render_mount_dir(api_port, out / "nginx-mount")
    nginx = NginxContainer(name, nginx_port, out / "nginx-mount", api_port)
    api = ApiProcess(args.database_url, api_port, nginx_port, out / "api.log")
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    client = httpx.Client(timeout=60.0)

    def check(case: str, ok: bool, **facts: Any) -> None:
        checks.append({"case": case, "ok": ok, **facts})
        if not ok:
            failures.append(case)
        print("SSE " + json.dumps({"case": case, "ok": ok, **facts}, ensure_ascii=False), flush=True)

    try:
        assert wait_http(f"http://127.0.0.1:{api_port}/api/v1/health/ready", timeout=90), (
            "api did not start"
        )
        nginx.start()
        base = f"http://127.0.0.1:{nginx_port}"
        assert wait_http(f"{base}/api/v1/health/ready", timeout=90), "nginx did not become ready"

        setup = client.post(
            f"{base}/api/v1/auth/setup",
            json={
                "username": "owner",
                "displayName": "SSE 所有者",
                "password": "correct-horse-battery",
                "passwordConfirm": "correct-horse-battery",
            },
            headers={"Origin": base},
        )
        assert setup.status_code == 201, setup.text
        headers = {"X-CSRF-Token": setup.json()["csrfToken"], "Origin": base}
        client.headers.update(headers)
        assert (
            client.post(
                f"{base}/api/v1/company",
                json={"name": "SSE 公司", "goal": "验证 nginx 后的断线补读。"},
            ).status_code
            == 201
        )
        project = client.post(
            f"{base}/api/v1/projects",
            json={
                "key": "SSE",
                "name": "SSE 项目",
                "objective": "验证事件流。",
                "runMode": "DEMO",
                "budgetTokens": 100_000,
            },
        ).json()
        first = client.post(
            f"{base}/api/v1/projects/{project['id']}/tasks",
            json={
                "title": "断线前的任务",
                "objective": "产生事件。",
                "budgetTokens": 1000,
                "demoBehavior": "SUCCESS",
                "maxAutoRetries": 0,
                "idempotencyKey": uuid.uuid4().hex,
            },
        )
        assert first.status_code == 201, first.text

        with read_sse(client, base, after=0, quiet_seconds=4) as (cursor, events):
            received = [event["id"] for event in events]
        check("through-nginx", len(received) >= 1, first_cursors=received)

        # ---- outage: proxy gone, event committed while nobody is reading ----
        # The commit below goes *directly* to the API, which is what a worker
        # does; a worker never depends on the web proxy. It uses a separate
        # client so the proxy-backed client's state is not affected by the outage.
        direct = httpx.Client(timeout=60.0, cookies=dict(client.cookies))
        try:
            nginx.stop()
            # Prove the proxy really is gone before committing anything.
            try:
                proxy_was_down = client.get(
                    f"{base}/api/v1/health/ready", timeout=5
                ).status_code >= 500
            except httpx.HTTPError:
                proxy_was_down = True
            missed = direct.post(
                f"http://127.0.0.1:{api_port}/api/v1/projects/{project['id']}/tasks",
                json={
                    "title": "断线期间的任务",
                    "objective": "断线期间产生事件。",
                    "budgetTokens": 1000,
                    "demoBehavior": "SUCCESS",
                    "maxAutoRetries": 0,
                    "idempotencyKey": uuid.uuid4().hex,
                },
                headers={**headers, "Origin": f"http://127.0.0.1:{api_port}"},
            )
            assert missed.status_code == 201, missed.text
            missed_task_id = missed.json()["task"]["id"]
        finally:
            direct.close()

        nginx.start()
        assert wait_http(f"{base}/api/v1/health/ready", timeout=90), "nginx did not come back"

        with read_sse(client, base, after=cursor, quiet_seconds=4) as (cursor2, resumed):
            resumed_ids = [event["id"] for event in resumed]
        resumed_tasks = [event["data"].get("taskId") for event in resumed]
        check(
            "resume-behind-nginx",
            missed_task_id in resumed_tasks
            and all(item > cursor for item in resumed_ids),
            proxy_was_down=proxy_was_down,
            resumed_from=cursor,
            resumed_cursors=resumed_ids,
            resumed_task_ids=resumed_tasks,
            missed_task_id=missed_task_id,
            last_cursor=cursor2,
        )
        check(
            "no-duplicates",
            len(resumed_ids) == len(set(resumed_ids)),
            resumed_cursors=resumed_ids,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        check("unexpected-error", False, error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            (out / "nginx.log").write_text(nginx.logs(), encoding="utf-8")
        except Exception:  # pragma: no cover - container may not exist
            pass
        nginx.stop()
        client.close()
        api.close()

    (out / "results.json").write_text(
        json.dumps(
            {"checks": checks, "failures": failures, "passed": not failures},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"passed": not failures, "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
