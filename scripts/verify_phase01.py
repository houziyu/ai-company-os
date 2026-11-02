"""Phase 01 acceptance verification against a running Compose stack.

Run this after `docker compose up -d` succeeds:

    python scripts/verify_phase01.py --base-url http://127.0.0.1:8080

It performs the real checks the phase requires and prints a PASS/FAIL table.
HTTP checks use the target URL; port publishing is inspected via local Docker.

Exit code is 0 only when every check passes.

Note: this script creates the owner account if none exists. Run it against a
fresh deployment or a throwaway stack.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any
from urllib.parse import urlsplit


@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Client:
    base_url: str
    jar: CookieJar = field(default_factory=CookieJar)

    def __post_init__(self) -> None:
        if urlsplit(self.base_url).scheme not in {"http", "https"}:
            raise ValueError("acceptance target must use http or https")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        csrf: str | None = None,
        origin: str | None = None,
        headers: dict[str, str] | None = None,
        follow: bool = True,
    ) -> tuple[int, Any, dict[str, str]]:
        url = f"{self.base_url}{path}"
        data = None
        request_headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        if csrf:
            request_headers["X-CSRF-Token"] = csrf
        if origin is not None:
            request_headers["Origin"] = origin
        if headers:
            request_headers.update(headers)

        request = urllib.request.Request(  # noqa: S310 - target scheme validated in __post_init__
            url, data=data, headers=request_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                status = response.status
                response_headers = dict(response.headers)
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
            response_headers = dict(error.headers)

        parsed: Any = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                parsed = raw.decode("utf-8", errors="replace")
        return status, parsed, response_headers

    def csrf_token(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "acos_csrf":
                return cookie.value
        return None

    def session_cookie(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "acos_session":
                return cookie.value
        return None

    def clear(self) -> None:
        for cookie in list(self.jar):
            self.jar.clear(cookie.domain, cookie.path, cookie.name)


def add(results: list[Result], name: str, passed: bool, detail: str = "") -> bool:
    results.append(Result(name, passed, detail))
    marker = "PASS" if passed else "FAIL"
    line = f"[{marker}] {name}"
    if detail:
        line += f" -- {detail}"
    print(line)
    return passed


def check_database_port(project: str) -> tuple[bool, str]:
    """Inspect the running postgres service, without reading container secrets."""
    docker = shutil.which("docker")
    if docker is None or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) is None:
        return False, "Docker unavailable or invalid Compose project name"
    try:
        result = subprocess.run(  # noqa: S603 - fixed command, validated project, no shell
            [
                docker,
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=postgres",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        ids = result.stdout.split()
        if not ids:
            return (
                False,
                "no running postgres container found for the selected Compose project",
            )
        for container_id in ids:
            if re.fullmatch(r"[0-9a-f]{12,64}", container_id) is None:
                return False, "invalid Docker container identifier"
            result = subprocess.run(  # noqa: S603 - fixed command, validated ID, no shell
                [
                    docker,
                    "inspect",
                    "--format",
                    "{{json .HostConfig.PortBindings}}",
                    container_id,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            bindings = json.loads(result.stdout)
            if bindings is not None and not isinstance(bindings, dict):
                return False, "unexpected Docker port inspection response"
            if bindings and any(bindings.values()):
                return False, "postgres has published host ports"
        return (
            True,
            f"inspected {len(ids)} running postgres container(s), no host ports",
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return (
            False,
            "Docker port inspection could not be completed (not an automatic pass)",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--compose-project",
        default="ai-company-os",
        help="local Compose project serving --base-url",
    )
    parser.add_argument("--username", default="owner")
    parser.add_argument("--display-name", default="所有者")
    parser.add_argument("--password", default="correct-horse-battery-staple")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    origin = base
    results: list[Result] = []
    client = Client(base)

    print(f"== Phase 01 verification against {base} ==\n")

    # ---------------------------------------------------------------- health
    status, body, headers = client.request("GET", "/api/v1/health/live")
    add(results, "liveness returns 200", status == 200, f"status={status}")

    status, body, headers = client.request("GET", "/api/v1/health/ready")
    ready_checks = (
        {check["name"]: check["status"] for check in (body or {}).get("checks", [])}
        if isinstance(body, dict)
        else {}
    )
    add(
        results,
        "readiness returns 200 with database+migrations ok",
        status == 200
        and isinstance(body, dict)
        and body.get("ready") is True
        and ready_checks.get("database") == "ok"
        and ready_checks.get("migrations") == "ok",
        f"status={status} checks={ready_checks}",
    )
    add(
        results,
        "worker heartbeat reported in readiness",
        ready_checks.get("worker") == "ok",
        f"worker={ready_checks.get('worker')}",
    )

    # ------------------------------------------------------------ setup flow
    status, body, _ = client.request("GET", "/api/v1/auth/setup-status")
    setup_complete = isinstance(body, dict) and body.get("setupComplete") is True
    add(
        results,
        "setup-status reports current owner state",
        status == 200,
        f"setupComplete={setup_complete}",
    )

    if not setup_complete:
        payload = {
            "username": args.username,
            "displayName": args.display_name,
            "password": args.password,
            "passwordConfirm": args.password,
        }
        status, body, _ = client.request("POST", "/api/v1/auth/setup", payload, origin=origin)
        add(
            results,
            "first-time owner setup succeeds (201)",
            status == 201,
            f"status={status} code={(body or {}).get('code') if isinstance(body, dict) else ''}",
        )
        add(
            results,
            "setup issues session and CSRF cookies",
            client.session_cookie() is not None and client.csrf_token() is not None,
        )

        status, body, _ = client.request(
            "POST",
            "/api/v1/auth/setup",
            {
                "username": "intruder",
                "displayName": "入侵者",
                "password": args.password,
                "passwordConfirm": args.password,
            },
            origin=origin,
        )
        add(
            results,
            "repeated setup is rejected (409 SETUP_ALREADY_COMPLETED)",
            status == 409
            and isinstance(body, dict)
            and body.get("code") == "SETUP_ALREADY_COMPLETED",
            f"status={status}",
        )
    else:
        add(results, "owner already provisioned (setup skipped)", True)
        status, body, _ = client.request(
            "POST",
            "/api/v1/auth/login",
            {"username": args.username, "password": args.password},
            origin=origin,
        )
        add(results, "login with existing owner", status == 200, f"status={status}")

    csrf = client.csrf_token()
    add(results, "CSRF token available to the client", bool(csrf))

    # ---------------------------------------------------------------- session
    status, body, _ = client.request("GET", "/api/v1/auth/session")
    add(
        results,
        "authenticated session readable",
        status == 200
        and isinstance(body, dict)
        and (body.get("user") or {}).get("role") == "OWNER",
        f"status={status}",
    )

    status, body, _ = client.request("GET", "/api/v1/system/status")
    status_payload = body if isinstance(body, dict) else {}
    text = json.dumps(status_payload, ensure_ascii=False).lower()
    add(
        results,
        "system status works and leaks no secrets",
        status == 200
        and "password" not in text
        and "postgresql+psycopg" not in text
        and "secret" not in text,
        f"status={status} db={status_payload.get('databaseStatus')} worker={status_payload.get('workerStatus')}",
    )

    # ------------------------------------------------------- CSRF + origin
    status, body, _ = client.request("POST", "/api/v1/auth/logout", origin=origin)
    add(
        results,
        "logout without CSRF header is rejected (403)",
        status == 403 and isinstance(body, dict) and body.get("code") == "CSRF_VALIDATION_FAILED",
        f"status={status}",
    )

    status, body, _ = client.request(
        "POST",
        "/api/v1/auth/logout",
        csrf=csrf,
        origin="https://evil.example",
    )
    add(
        results,
        "cross-site write is rejected (403)",
        status == 403 and isinstance(body, dict) and body.get("code") == "ORIGIN_NOT_ALLOWED",
        f"status={status}",
    )

    # ------------------------------------------------------- wrong password
    old_session = client.session_cookie()
    probe = Client(base)
    status, body, _ = probe.request(
        "POST",
        "/api/v1/auth/login",
        {"username": args.username, "password": "definitely-wrong-password"},
        origin=origin,
    )
    missing = Client(base)
    status_missing, body_missing, _ = missing.request(
        "POST",
        "/api/v1/auth/login",
        {"username": "no-such-user", "password": "definitely-wrong-password"},
        origin=origin,
    )
    add(
        results,
        "wrong password rejected without revealing account existence",
        status == 401
        and status_missing == 401
        and isinstance(body, dict)
        and isinstance(body_missing, dict)
        and body.get("code") == body_missing.get("code")
        and body.get("message") == body_missing.get("message"),
        f"wrong={status} unknown_user={status_missing}",
    )

    # ------------------------------------------------------------- logout
    status, _, _ = client.request("POST", "/api/v1/auth/logout", csrf=csrf, origin=origin)
    add(
        results,
        "logout with CSRF header succeeds (204)",
        status == 204,
        f"status={status}",
    )

    status, body, _ = client.request("GET", "/api/v1/auth/session")
    add(
        results,
        "session is unusable after logout (401)",
        status == 401,
        f"status={status}",
    )

    # Replay the captured cookie: the server record is revoked, so a stolen copy
    # must not work either.
    replay = Client(base)
    status, body, _ = replay.request(
        "GET",
        "/api/v1/auth/session",
        headers={"Cookie": f"acos_session={old_session}"} if old_session else None,
    )
    add(
        results,
        "revoked cookie replay rejected (401)",
        status == 401,
        f"status={status}",
    )

    # -------------------------------------------------------------- login again
    status, body, _ = client.request(
        "POST",
        "/api/v1/auth/login",
        {"username": args.username, "password": args.password},
        origin=origin,
    )
    add(
        results,
        "login with correct password succeeds",
        status == 200,
        f"status={status}",
    )

    # -------------------------------------------------------------- unauthenticated
    anon = Client(base)
    status, body, _ = anon.request("GET", "/api/v1/system/status")
    add(
        results,
        "business API rejects unauthenticated requests (401)",
        status == 401 and isinstance(body, dict) and body.get("code") == "NOT_AUTHENTICATED",
        f"status={status}",
    )

    # ------------------------------------------------------------------ SPA
    status, body, headers = client.request("GET", "/")
    html = body if isinstance(body, str) else json.dumps(body)
    add(
        results,
        "web serves the SPA shell with security headers",
        status == 200
        and '<div id="app">' in html
        and headers.get("X-Frame-Options") == "DENY"
        and "Content-Security-Policy" in headers,
        f"status={status}",
    )

    status, body, _ = client.request("GET", "/overview")
    html = body if isinstance(body, str) else json.dumps(body)
    add(
        results,
        "client-side route falls back to index.html",
        status == 200 and '<div id="app">' in html,
        f"status={status}",
    )

    port_ok, port_detail = check_database_port(args.compose_project)
    add(
        results,
        "database port is not published",
        port_ok,
        port_detail,
    )

    # ------------------------------------------------------------------ summary
    failed = [result for result in results if not result.passed]
    print(f"\n== {len(results) - len(failed)}/{len(results)} checks passed ==")
    if failed:
        print("\nFailed checks:")
        for result in failed:
            print(f"  - {result.name}: {result.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
