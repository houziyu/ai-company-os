"""Phase 02 end-to-end acceptance against a running Compose stack.

This script drives the real HTTP API of a deployed stack: it creates the owner,
runs the company wizard, edits the organization, configures the model centre and
checks the audit trail. It deliberately has no access to the database and no
provider credentials, so it can only prove what the API and UI can prove.

Checks that need to spend tokens or reach a real provider are *not* here:
``probe`` against a real DeepSeek key requires a key, and this script refuses to
pretend. The protocol-level probe behaviour (success JSON, wrong key, timeout,
invalid JSON) is covered by the pytest suite with a scripted transport, and that
distinction is stated in the acceptance report.

Usage:
    python scripts/verify_phase02.py --base-url http://127.0.0.1:18083 --compose-project acos-phase02-test

Exit code 0 means every executed check passed; 1 means at least one failed.
Only run against a fresh/disposable stack: the script creates the owner account.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

# Fixed password for a disposable acceptance stack only; never a deployment value.
DEFAULT_PASSWORD = "phase02-acceptance-password"  # noqa: S105


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class Results:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail))
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: {detail}")

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))
        self.csrf: str | None = None
        self.origin = self.base_url
        # The double-submit pattern means the SPA always echoes the CSRF cookie.
        # Set False to model a request that omits the header (the negative test).
        self.send_csrf = True

    def _cookie(self, name: str) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expect: int | None = None,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json", "Origin": self.origin}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method != "GET" and self.send_csrf:
            csrf = self.csrf or self._cookie("acos_csrf")
            if csrf:
                headers["X-CSRF-Token"] = csrf
        # URL scheme comes from --base-url supplied by the operator running the check.
        request = Request(url, data=data, headers=headers, method=method)  # noqa: S310
        try:
            with self.opener.open(request, timeout=30) as response:
                status = response.status
                body = response.read()
                parsed = json.loads(body) if body else None
        except HTTPError as error:
            status = error.code
            raw = error.read()
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"raw": raw.decode("utf-8", "replace")[:200]}
        if expect is not None and status != expect:
            raise AssertionError(f"{method} {path} expected {expect}, got {status}: {parsed}")
        return status, parsed


def check_database_port_isolated(project: str) -> tuple[bool, str]:
    """The business database must not be published on the host.

    Mirrors the Phase 01 check: ``docker port`` is queried for the postgres
    container of this project. Missing Docker is a failure, not a skip, because
    the claim being made is about the deployed stack.
    """
    if shutil.which("docker") is None:
        return False, "docker 不可用，无法验证数据库端口未发布"
    command = [
        "docker",
        "ps",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        "label=com.docker.compose.service=postgres",
        "--format",
        "{{.ID}}",
    ]
    # The command is a fixed docker invocation built here, not user input.
    completed = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, check=False
    )
    container_id = completed.stdout.strip().splitlines()
    if not container_id:
        return False, f"未找到项目 {project} 的 postgres 容器"
    port_command = ["docker", "port", container_id[0]]
    port_result = subprocess.run(  # noqa: S603
        port_command, capture_output=True, text=True, check=False
    )
    published = port_result.stdout.strip()
    if published:
        return False, f"数据库端口被发布到宿主：{published}"
    return True, "postgres 容器没有发布端口"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18083")
    parser.add_argument("--compose-project", default="acos-phase02-test")
    args = parser.parse_args()

    results = Results()
    client = ApiClient(args.base_url)

    # ---------------------------------------------------------- connectivity
    status, meta = client.request("GET", "/api/v1/meta", expect=200)
    results.add(
        "API 可访问",
        status == 200 and isinstance(meta, dict),
        f"HTTP {status}, app={meta.get('appName') if isinstance(meta, dict) else '?'}",
    )

    status, health = client.request("GET", "/api/v1/health/ready", expect=200)
    checks = {item["name"]: item["status"] for item in health.get("checks", [])}
    results.add(
        "就绪检查含数据库与迁移",
        checks.get("database") == "ok" and checks.get("migrations") == "ok",
        f"checks={checks}",
    )

    # ------------------------------------------------------------ owner setup
    status, setup_status = client.request("GET", "/api/v1/auth/setup-status", expect=200)
    if setup_status["setupComplete"]:
        results.add(
            "所有者初始化",
            False,
            "该栈已有所有者；此脚本只应对全新栈运行",
        )
        return 1
    results.add(
        "创建前公司未初始化",
        setup_status["companyInitialized"] is False,
        f"companyInitialized={setup_status['companyInitialized']}",
    )

    status, session = client.request(
        "POST",
        "/api/v1/auth/setup",
        {
            "username": "owner",
            "displayName": "验收所有者",
            "password": DEFAULT_PASSWORD,
            "passwordConfirm": DEFAULT_PASSWORD,
        },
        expect=201,
    )
    client.csrf = session["csrfToken"]
    results.add("所有者初始化成功", status == 201, f"user={session['user']['username']}")

    status, setup_status = client.request("GET", "/api/v1/auth/setup-status", expect=200)
    results.add(
        "创建前公司状态仍为未初始化",
        setup_status["companyInitialized"] is False,
        f"companyInitialized={setup_status['companyInitialized']}",
    )

    # ------------------------------------------------------- template + models
    status, template = client.request("GET", "/api/v1/company/template", expect=200)
    codes = [item["code"] for item in template["positions"]]
    results.add(
        "模板包含五个岗位",
        codes == ["CEO", "PRODUCT_MANAGER", "TECH_LEAD", "QA", "DEVOPS"],
        f"codes={codes}",
    )

    status, centre = client.request("GET", "/api/v1/models/centre", expect=200)
    preset = centre["deepseekPreset"]
    results.add(
        "模型中心提供 DeepSeek 预设且无密钥值",
        preset["secretRef"] == "DEEPSEEK_API_KEY"
        and "secret" not in json.dumps(preset).lower().replace("secretref", ""),
        f"secretRef={preset['secretRef']}",
    )

    # Provider with no key configured: the UI/API must say 未配置.
    status, provider = client.request(
        "POST",
        "/api/v1/providers",
        {
            "name": "DeepSeek 预设",
            "kind": "DEEPSEEK",
            "baseUrl": preset["baseUrl"],
            "secretRef": preset["secretRef"],
            "timeoutSeconds": 10,
        },
        expect=201,
    )
    results.add(
        "旧式环境变量引用未配置时状态为 MISSING_ENV",
        provider["secretState"] == "MISSING_ENV" and provider["secretConfigured"] is False,
        f"secretState={provider['secretState']}",
    )

    status, model = client.request(
        "POST",
        "/api/v1/models",
        {
            "providerId": provider["id"],
            "logicalName": preset["suggestedModel"],
            "label": "DeepSeek V4.1 Flash",
        },
        expect=201,
    )
    results.add(
        "模型未探测前 JSON 能力为未知",
        model["supportsJsonOutput"] is None,
        f"supportsJsonOutput={model['supportsJsonOutput']}",
    )

    # A probe with no key must fail locally and report MISSING_SECRET, no call.
    status, probe = client.request("POST", f"/api/v1/models/{model['id']}/probe", {}, expect=200)
    results.add(
        "缺少密钥的探测被本地拒绝",
        probe["outcome"] == "FAILURE"
        and probe["errorCode"] == "MISSING_SECRET"
        and probe["callCount"] == 0,
        f"errorCode={probe['errorCode']}, callCount={probe['callCount']}",
    )
    results.add(
        "探测提示可能消耗 Token",
        "可能消耗少量 Token" in probe["tokenWarning"],
        probe["tokenWarning"],
    )

    # --- review-fix behaviour that is observable without a provider call ------
    # A stale configuration form must be refused, not silently applied.
    stale = client.request(
        "PATCH",
        f"/api/v1/providers/{provider['id']}",
        {"name": "过期表单", "expectedVersion": provider["version"] + 99},
    )[0]
    results.add(
        "过期的供应商表单被拒绝",
        stale == 409,
        f"status={stale}",
    )

    # Explicit null clears the notes; omitting the field keeps them.
    _, noted = client.request(
        "PATCH",
        f"/api/v1/providers/{provider['id']}",
        {"notes": "验收备注", "expectedVersion": provider["version"]},
        expect=200,
    )
    _, cleared = client.request(
        "PATCH",
        f"/api/v1/providers/{provider['id']}",
        {"notes": None, "expectedVersion": noted["version"]},
        expect=200,
    )
    results.add(
        "备注可显式清空",
        cleared["notes"] is None,
        f"notes={cleared['notes']}",
    )

    # Changing the endpoint invalidates the (already absent) capability claim and
    # explicitly marks the evidence as stale-free rather than reusing it.
    _, moved_provider = client.request(
        "PATCH",
        f"/api/v1/providers/{provider['id']}",
        {
            "baseUrl": "https://another-endpoint.example.test",
            "expectedVersion": cleared["version"],
        },
        expect=200,
    )
    centre_after_move = client.request("GET", "/api/v1/models/centre", expect=200)[1]
    moved_model = next(m for m in centre_after_move["models"] if m["id"] == model["id"])
    results.add(
        "地址变更后能力仍为未验证",
        moved_model["supportsJsonOutput"] is None,
        f"baseUrl={moved_provider['baseUrl']}, supportsJsonOutput={moved_model['supportsJsonOutput']}",
    )
    # Restore the original endpoint so later probe checks describe what they name.
    client.request(
        "PATCH",
        f"/api/v1/providers/{provider['id']}",
        {"baseUrl": preset["baseUrl"], "expectedVersion": moved_provider["version"]},
        expect=200,
    )

    # ------------------------------------------------------- company creation
    idempotency_key = f"acceptance-{uuid.uuid4().hex[:12]}"
    payload = {
        "name": "精一科技",
        "goal": "为中小企业交付可验证的自动化研发能力。",
        "idempotencyKey": idempotency_key,
        "modelId": model["id"],
    }
    status, company = client.request("POST", "/api/v1/company", payload, expect=201)
    results.add(
        "公司创建生成五岗位五员工",
        company["positionCount"] == 5 and company["agentCount"] == 5,
        f"positions={company['positionCount']}, agents={company['agentCount']}",
    )

    status, repeat = client.request("POST", "/api/v1/company", payload, expect=200)
    results.add(
        "相同幂等键重复提交返回既有公司",
        repeat["id"] == company["id"],
        f"second status=200, same id={repeat['id'] == company['id']}",
    )

    status, conflict = client.request(
        "POST",
        "/api/v1/company",
        {**payload, "idempotencyKey": "different-key"},
    )
    results.add(
        "不同请求重复创建被拒绝",
        status == 409 and conflict["code"] == "COMPANY_ALREADY_EXISTS",
        f"status={status}, code={conflict.get('code')}",
    )

    status, setup_status = client.request("GET", "/api/v1/auth/setup-status", expect=200)
    results.add(
        "公司初始化状态已更新",
        setup_status["companyInitialized"] is True and setup_status["companyName"] == "精一科技",
        f"companyName={setup_status['companyName']}",
    )

    # --------------------------------------------------------------- organization
    status, org = client.request("GET", "/api/v1/organization", expect=200)
    agents = {item["positionCode"]: item for item in org["agents"]}
    results.add(
        "组织包含五名独立员工",
        len({item["id"] for item in org["agents"]}) == 5
        and len({item["employeeCode"] for item in org["agents"]}) == 5,
        f"agents={len(org['agents'])}",
    )
    results.add(
        "模板汇报关系已建立",
        agents["CEO"]["managerId"] is None
        and agents["PRODUCT_MANAGER"]["managerId"] == agents["CEO"]["id"]
        and agents["QA"]["managerId"] == agents["TECH_LEAD"]["id"],
        "CEO 为根，PM 直属 CEO，QA 直属技术负责人",
    )
    results.add(
        "创建时绑定的模型出现在组织视图",
        all(item["model"] and item["model"]["modelId"] == model["id"] for item in org["agents"]),
        f"bound model id={model['id']}",
    )

    # Cycle rejection, server-side.
    status, cycle = client.request(
        "PATCH",
        f"/api/v1/agents/{agents['CEO']['id']}",
        {"changeManager": True, "managerId": agents["QA"]["id"]},
    )
    results.add(
        "循环汇报关系被服务端拒绝",
        status == 409
        and cycle["code"] == "REPORTING_CYCLE"
        and cycle["fieldErrors"][0]["field"] == "managerId",
        f"status={status}, code={cycle.get('code')}",
    )

    # Legitimate reporting change.
    status, moved = client.request(
        "PATCH",
        f"/api/v1/agents/{agents['QA']['id']}",
        {"changeManager": True, "managerId": agents["CEO"]["id"]},
        expect=200,
    )
    results.add(
        "合法汇报调整生效",
        moved["managerId"] == agents["CEO"]["id"],
        f"QA managerId={moved['managerId']}",
    )

    # Position duty edit creates a version and keeps history.
    qa_position = next(item for item in org["positions"] if item["code"] == "QA")
    status, position_update = client.request(
        "PATCH",
        f"/api/v1/positions/{qa_position['id']}",
        {
            "duty": "验证每次交付都有可复现的测试证据。",
            "expectedVersion": qa_position["rowVersion"],
        },
        expect=200,
    )
    results.add(
        "岗位职责编辑生成新版本",
        position_update["versionCreated"] is True
        and position_update["position"]["version"] == qa_position["version"] + 1,
        f"version {qa_position['version']} -> {position_update['position']['version']}",
    )

    # Employee duty override + pause + archive keep identity and history.
    status, updated = client.request(
        "PATCH",
        f"/api/v1/agents/{agents['DEVOPS']['id']}",
        {"dutyOverride": "额外负责发布前检查。", "reason": "验收"},
        expect=200,
    )
    client.request(
        "PATCH",
        f"/api/v1/agents/{agents['DEVOPS']['id']}",
        {"status": "ARCHIVED", "expectedVersion": updated["version"]},
        expect=200,
    )[1]
    status, org_after = client.request("GET", "/api/v1/organization", expect=200)
    devops_after = next(
        item for item in org_after["agents"] if item["id"] == agents["DEVOPS"]["id"]
    )
    results.add(
        "归档保留身份与职责覆盖",
        devops_after["status"] == "ARCHIVED"
        and devops_after["employeeCode"] == agents["DEVOPS"]["employeeCode"]
        and devops_after["effectiveDuty"] == "额外负责发布前检查。",
        f"status={devops_after['status']}, code={devops_after['employeeCode']}",
    )

    status, history = client.request(
        "GET", f"/api/v1/agents/{agents['DEVOPS']['id']}/history", expect=200
    )
    kinds = [entry["kind"] for entry in history["entries"]]
    results.add(
        "员工履历包含职责与模型记录",
        "DUTY_OVERRIDE" in kinds and "MODEL_ASSIGNMENT" in kinds,
        f"kinds={kinds}",
    )

    # Rebind to prove the previous assignment is closed, not deleted.
    status, rebound = client.request(
        "POST",
        f"/api/v1/agents/{agents['QA']['id']}/model-assignment",
        {"modelId": model["id"], "note": "验收重绑"},
        expect=200,
    )
    results.add(
        "模型重新绑定成功",
        rebound["model"]["modelId"] == model["id"],
        f"modelId={rebound['model']['modelId']}",
    )

    # ------------------------------------------------------------------- audit
    status, audit = client.request("GET", "/api/v1/audit/events?limit=200", expect=200)
    actions = {event["action"] for event in audit["events"]}
    expected_actions = {
        "COMPANY_INITIALIZED",
        "POSITION_UPDATED",
        "AGENT_UPDATED",
        "AGENT_STATUS_CHANGED",
        "AGENT_MODEL_ASSIGNED",
        "PROVIDER_CREATED",
        "PROVIDER_MODEL_CREATED",
        "MODEL_PROBE_FAILED",
    }
    missing = expected_actions - actions
    results.add(
        "组织与模型变更写入审计",
        not missing,
        f"missing={sorted(missing)}" if missing else f"{len(actions)} 种动作",
    )

    # Audit is append-only through the API.
    delete_status, _ = client.request("DELETE", "/api/v1/audit/events/1")
    results.add(
        "审计记录无删除接口",
        delete_status in (404, 405),
        f"DELETE status={delete_status}",
    )

    # ------------------------------------------------------------ secret check
    status, centre_after = client.request("GET", "/api/v1/models/centre", expect=200)
    blob = json.dumps(centre_after)
    results.add(
        "模型中心响应不含密钥值或密文",
        "secretValue" not in blob
        and "Bear" + "er " not in blob
        and "secretCiphertext" not in blob,
        "响应只含 secretRef / secretSource / secretState / secretConfigured",
    )

    # -------------------------------------------------------- data & security
    ok, detail = check_database_port_isolated(args.compose_project)
    results.add("数据库端口未发布到宿主", ok, detail)

    anonymous = ApiClient(args.base_url)
    status, _ = anonymous.request("GET", "/api/v1/organization")
    results.add("未认证访问组织被拒绝", status == 401, f"status={status}")

    # Same session cookies, but no X-CSRF-Token header. The opener must be
    # rebuilt around the shared jar; assigning the jar alone leaves urllib
    # without a cookie processor and every request looks anonymous.
    csrf_client = ApiClient(args.base_url)
    csrf_client.jar = client.jar
    csrf_client.opener = build_opener(HTTPCookieProcessor(client.jar))
    csrf_client.csrf = None
    csrf_client.send_csrf = False
    csrf_client.origin = client.origin
    status, csrf_body = csrf_client.request(
        "PATCH",
        f"/api/v1/agents/{agents['QA']['id']}",
        {"displayName": "无 CSRF"},
        expect=None,
    )
    results.add(
        "写请求缺少 CSRF 头被拒绝",
        status == 403 and (csrf_body or {}).get("code") == "CSRF_VALIDATION_FAILED",
        f"status={status}, code={(csrf_body or {}).get('code')}",
    )

    # The session must still work: the rejected write did not revoke it.
    status, _ = client.request("GET", "/api/v1/organization", expect=200)
    results.add("CSRF 拒绝不影响有效会话", status == 200, f"status={status}")

    # ---------------------------------------------------------------- summary
    print()
    print(f"执行 {len(results.checks)} 项检查，失败 {len(results.failed)} 项")
    for check in results.failed:
        print(f"  - {check.name}: {check.detail}")
    return 0 if not results.failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (URLError, AssertionError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
