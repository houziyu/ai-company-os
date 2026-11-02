"""Phase 02 acceptance tests: company, organization and model centre.

These tests run against a real migrated PostgreSQL database (see conftest). Probe
tests inject an ``httpx.MockTransport`` through ``create_app(probe_transport=...)``
so the production adapter path is exercised without network access. The mock is
explicitly a protocol simulator: no test claims a real provider was verified.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import build_engine
from app.main import BACKEND_ROOT, create_app
from tests.conftest import setup_owner

OWNER = {"Origin": "http://testserver"}


# --------------------------------------------------------------------- helpers
def owner_headers(client: TestClient) -> dict[str, str]:
    return setup_owner(client)


def create_company(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "精一科技",
    goal: str = "为中小企业交付可验证的自动化研发能力。",
    model_id: int | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {"name": name, "goal": goal}
    if model_id is not None:
        payload["modelId"] = model_id
    if idempotency_key is not None:
        payload["idempotencyKey"] = idempotency_key
    return client.post("/api/v1/company", json=payload, headers=headers)


def create_provider(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "DeepSeek 供应商",
    base_url: str = "https://api.deepseek.com",
    secret_ref: str | None = "DEEPSEEK_API_KEY",  # noqa: S107 - env var name
    kind: str = "DEEPSEEK",
    status: str = "ENABLED",
    timeout_seconds: float = 5.0,
) -> httpx.Response:
    return client.post(
        "/api/v1/providers",
        json={
            "name": name,
            "kind": kind,
            "baseUrl": base_url,
            "secretRef": secret_ref,
            "status": status,
            "timeoutSeconds": timeout_seconds,
        },
        headers=headers,
    )


def create_model(
    client: TestClient,
    headers: dict[str, str],
    provider_id: int,
    *,
    logical_name: str = "deepseek-v4.1-flash",
    label: str = "DeepSeek V4.1 Flash",
) -> httpx.Response:
    return client.post(
        "/api/v1/models",
        json={
            "providerId": provider_id,
            "logicalName": logical_name,
            "label": label,
        },
        headers=headers,
    )


def openai_ok_body(content: str = '{"ok": true}', *, usage: bool = True) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "chatcmpl-test",
        "model": "deepseek-v4.1-flash-2026-01",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28}
    return body


# ------------------------------------------------------- template + creation
def test_company_template_exposes_five_positions(client):
    headers = owner_headers(client)
    response = client.get("/api/v1/company/template", headers=headers)
    assert response.status_code == 200
    body = response.json()
    codes = [item["code"] for item in body["positions"]]
    assert codes == ["CEO", "PRODUCT_MANAGER", "TECH_LEAD", "QA", "DEVOPS"]
    reports = {item["code"]: item["reportsToCode"] for item in body["positions"]}
    assert reports["CEO"] is None
    assert reports["PRODUCT_MANAGER"] == "CEO"
    assert reports["QA"] == "TECH_LEAD"
    for item in body["positions"]:
        assert item["duty"].strip()


def test_company_creation_makes_exactly_five_positions_and_five_agents(client, engine):
    headers = owner_headers(client)
    response = create_company(client, headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "精一科技"
    assert body["positionCount"] == 5
    assert body["agentCount"] == 5
    assert body["version"] == 1

    org = client.get("/api/v1/organization", headers=headers)
    assert org.status_code == 200
    data = org.json()
    assert len(data["positions"]) == 5
    assert len(data["agents"]) == 5
    # Five *independent* instances: distinct ids, codes and names.
    assert len({a["id"] for a in data["agents"]}) == 5
    assert len({a["employeeCode"] for a in data["agents"]}) == 5
    # Management tree matches the template.
    by_code = {a["positionCode"]: a for a in data["agents"]}
    assert by_code["CEO"]["managerId"] is None
    assert by_code["PRODUCT_MANAGER"]["managerId"] == by_code["CEO"]["id"]
    assert by_code["QA"]["managerId"] == by_code["TECH_LEAD"]["id"]

    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM companies),"
                " (SELECT count(*) FROM positions),"
                " (SELECT count(*) FROM agents),"
                " (SELECT count(*) FROM position_versions),"
                " (SELECT count(*) FROM departments)"
            )
        ).one()
    assert tuple(counts) == (1, 5, 5, 5, 1)


def test_company_creation_is_idempotent_for_same_key(client, engine):
    headers = owner_headers(client)
    first = create_company(client, headers, idempotency_key="wizard-run-1")
    assert first.status_code == 201
    second = create_company(client, headers, idempotency_key="wizard-run-1")
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM companies")) == 1
        assert connection.scalar(text("SELECT count(*) FROM agents")) == 5


def test_company_creation_rejects_different_payload_after_exists(client):
    headers = owner_headers(client)
    assert create_company(client, headers, idempotency_key="k1").status_code == 201
    conflicting = create_company(client, headers, name="另一家公司", idempotency_key="k1")
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "COMPANY_ALREADY_EXISTS"
    # Same key, same body, but the earlier request had no key -> still refused
    # rather than silently creating a second company.
    duplicate = create_company(client, headers, idempotency_key="k2")
    assert duplicate.status_code == 409


def test_concurrent_company_creation_creates_exactly_one(client, app, settings, engine):
    """Four simultaneous wizard submits must produce one company and five agents."""
    headers = owner_headers(client)
    barrier = Barrier(4)

    def attempt(index: int) -> tuple[int, str]:
        engine_for_worker = build_engine(settings)
        worker_app = create_app(settings, engine=engine_for_worker)
        try:
            with TestClient(worker_app) as other:
                other.cookies.update(client.cookies)
                barrier.wait(timeout=20)
                response = other.post(
                    "/api/v1/company",
                    json={
                        "name": "并发科技",
                        "goal": "验证并发初始化只创建一家公司。",
                        "idempotencyKey": f"concurrent-{index}",
                    },
                    headers=headers,
                )
                return response.status_code, response.json().get("code", "")
        finally:
            engine_for_worker.dispose()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))

    statuses = sorted(status for status, _ in results)
    assert statuses.count(201) == 1, results
    assert statuses.count(409) == 3, results
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM companies")) == 1
        assert connection.scalar(text("SELECT count(*) FROM agents")) == 5
        assert connection.scalar(text("SELECT count(*) FROM positions")) == 5
        # Only one initialization audit event is recorded.
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action='COMPANY_INITIALIZED'")
            )
            == 1
        )


def test_company_requires_authentication(client):
    assert client.post("/api/v1/company", json={"name": "x", "goal": "y"}).status_code == 401
    assert client.get("/api/v1/organization").status_code == 401
    assert client.get("/api/v1/models/centre").status_code == 401
    assert client.get("/api/v1/audit/events").status_code == 401


def test_company_write_requires_csrf(client):
    setup_owner(client)
    # Authenticated but no CSRF header: the origin guard is satisfied, CSRF is not.
    response = client.post(
        "/api/v1/company",
        json={"name": "无 CSRF", "goal": "应被拒绝。"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


def test_company_missing_before_wizard(client):
    headers = owner_headers(client)
    assert client.get("/api/v1/company", headers=headers).status_code == 404
    org = client.get("/api/v1/organization", headers=headers)
    assert org.status_code == 404
    assert org.json()["code"] == "COMPANY_NOT_FOUND"


# ------------------------------------------------------------- organization
def test_position_duty_edit_persists_and_versions(client, engine):
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    qa = next(p for p in org["positions"] if p["code"] == "QA")

    response = client.patch(
        f"/api/v1/positions/{qa['id']}",
        json={"duty": "验证每次交付都有可复现的测试证据。", "expectedVersion": qa["rowVersion"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["versionCreated"] is True
    assert response.json()["position"]["version"] == 2

    reloaded = client.get("/api/v1/organization", headers=headers).json()
    qa_after = next(p for p in reloaded["positions"] if p["code"] == "QA")
    assert qa_after["duty"] == "验证每次交付都有可复现的测试证据。"
    assert qa_after["version"] == 2

    with engine.connect() as connection:
        versions = connection.execute(
            text(
                "SELECT version, duty FROM position_versions"
                " WHERE position_id = :pid ORDER BY version"
            ),
            {"pid": qa["id"]},
        ).all()
    assert [row[0] for row in versions] == [1, 2]
    # The old duty text is preserved verbatim.
    assert "独立验证交付是否满足验收标准" in versions[0][1]


def test_position_stale_version_is_rejected(client):
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    ceo = next(p for p in org["positions"] if p["code"] == "CEO")
    # A rename alone does not append a duty version, but it does bump the
    # optimistic-locking counter; a stale client must be refused.
    first = client.patch(
        f"/api/v1/positions/{ceo['id']}",
        json={"title": "首席执行官", "expectedVersion": ceo["rowVersion"]},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["position"]["version"] == ceo["version"]
    assert first.json()["position"]["rowVersion"] == ceo["rowVersion"] + 1
    stale = client.patch(
        f"/api/v1/positions/{ceo['id']}",
        json={"title": "CEO（旧版本）", "expectedVersion": ceo["rowVersion"]},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "CONCURRENT_UPDATE"


def test_reporting_cycle_is_rejected_by_server(client):
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    by_code = {a["positionCode"]: a for a in org["agents"]}
    ceo = by_code["CEO"]
    tech = by_code["TECH_LEAD"]
    qa = by_code["QA"]

    # CEO currently reports to nobody; making the CEO report to QA (which
    # transitively reports to the CEO) must be refused.
    response = client.patch(
        f"/api/v1/agents/{ceo['id']}",
        json={"changeManager": True, "managerId": qa["id"]},
        headers=headers,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "REPORTING_CYCLE"
    assert body["fieldErrors"][0]["field"] == "managerId"

    # Direct two-node cycle in the other direction.
    response = client.patch(
        f"/api/v1/agents/{tech['id']}",
        json={"changeManager": True, "managerId": qa["id"]},
        headers=headers,
    )
    assert response.status_code == 409

    # Self-management is its own error.
    self_report = client.patch(
        f"/api/v1/agents/{tech['id']}",
        json={"changeManager": True, "managerId": tech["id"]},
        headers=headers,
    )
    assert self_report.status_code == 409
    assert self_report.json()["code"] == "REPORTING_SELF"

    # Nothing was written: the tree is unchanged.
    after = client.get("/api/v1/organization", headers=headers).json()
    after_by_code = {a["positionCode"]: a for a in after["agents"]}
    assert after_by_code["CEO"]["managerId"] is None
    assert after_by_code["TECH_LEAD"]["managerId"] == after_by_code["CEO"]["id"]


def test_legitimate_reporting_change_persists(client):
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    by_code = {a["positionCode"]: a for a in org["agents"]}

    # Move QA under the CEO: no cycle, so it is accepted.
    response = client.patch(
        f"/api/v1/agents/{by_code['QA']['id']}",
        json={"changeManager": True, "managerId": by_code["CEO"]["id"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["managerId"] == by_code["CEO"]["id"]
    assert response.json()["managerName"] == by_code["CEO"]["displayName"]

    reloaded = client.get("/api/v1/organization", headers=headers).json()
    qa_after = next(a for a in reloaded["agents"] if a["positionCode"] == "QA")
    assert qa_after["managerId"] == by_code["CEO"]["id"]

    # Clearing the manager is also allowed.
    cleared = client.patch(
        f"/api/v1/agents/{qa_after['id']}",
        json={"changeManager": True, "managerId": None},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["managerId"] is None


def test_management_relation_does_not_grant_resource_permissions(client):
    """A manager cannot act on behalf of a report: there is only the owner.

    The test documents and enforces the invariant by proving the API has no path
    where a management edge changes what the session may do. Employee identities
    are not principals: every write still needs the owner's session and CSRF.
    """
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    by_code = {a["positionCode"]: a for a in org["agents"]}
    ceo = by_code["CEO"]

    # The CEO is a manager of three employees. Without a session its id grants
    # nothing: these are the same 401s as for any anonymous caller.
    anonymous = TestClient(client.app)
    assert (
        anonymous.patch(
            f"/api/v1/agents/{ceo['id']}",
            json={"displayName": "冒名"},
            headers={"Origin": "http://testserver"},
        ).status_code
        == 401
    )
    assert anonymous.get("/api/v1/organization").status_code == 401

    # A management relation also does not create an Owner role or a user row.
    from sqlalchemy import text as sql_text

    with client.app.state.engine.connect() as connection:
        users = connection.execute(sql_text("SELECT username, role FROM users")).all()
        managers = connection.execute(
            sql_text("SELECT manager_id FROM agents WHERE manager_id IS NOT NULL")
        ).all()
    assert [(row[0], row[1]) for row in users] == [("owner", "OWNER")]
    # Four reporting edges come from the template; none of them is a principal.
    assert len(managers) == 4


def test_agent_identity_and_history_survive_status_and_model_changes(client, engine):
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    qa = next(a for a in org["agents"] if a["positionCode"] == "QA")

    # Duty override, then pause, then archive: the record and history stay.
    duty = client.patch(
        f"/api/v1/agents/{qa['id']}",
        json={"dutyOverride": "额外负责发布前的回归清单。", "reason": "职责扩展"},
        headers=headers,
    )
    assert duty.status_code == 200, duty.text
    assert duty.json()["effectiveDuty"] == "额外负责发布前的回归清单。"

    paused = client.patch(
        f"/api/v1/agents/{qa['id']}",
        json={"status": "PAUSED", "expectedVersion": duty.json()["version"]},
        headers=headers,
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"

    archived = client.patch(
        f"/api/v1/agents/{qa['id']}",
        json={"status": "ARCHIVED", "expectedVersion": paused.json()["version"]},
        headers=headers,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    assert archived.json()["archivedAt"] is not None

    still_there = client.get("/api/v1/organization", headers=headers).json()
    qa_after = next(a for a in still_there["agents"] if a["id"] == qa["id"])
    assert qa_after["status"] == "ARCHIVED"
    # Identity is intact: same employee code, same position, duty override kept.
    assert qa_after["employeeCode"] == qa["employeeCode"]
    assert qa_after["positionCode"] == "QA"
    assert qa_after["effectiveDuty"] == "额外负责发布前的回归清单。"

    history = client.get(f"/api/v1/agents/{qa['id']}/history", headers=headers)
    assert history.status_code == 200
    kinds = [entry["kind"] for entry in history.json()["entries"]]
    assert "DUTY_OVERRIDE" in kinds

    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action='AGENT_STATUS_CHANGED'")
            )
            == 2
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM agent_duty_override_history WHERE agent_id = :a"),
                {"a": qa["id"]},
            )
            == 1
        )
        # No deletion anywhere: the agent row still exists.
        assert (
            connection.scalar(text("SELECT count(*) FROM agents WHERE id = :a"), {"a": qa["id"]})
            == 1
        )


def test_duty_override_clearing_keeps_history(client):
    headers = owner_headers(client)
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    devops = next(a for a in org["agents"] if a["positionCode"] == "DEVOPS")

    first = client.patch(
        f"/api/v1/agents/{devops['id']}",
        json={"dutyOverride": "仅负责预发环境。"},
        headers=headers,
    )
    assert first.status_code == 200
    cleared = client.patch(
        f"/api/v1/agents/{devops['id']}",
        json={"clearDutyOverride": True},
        headers=headers,
    )
    assert cleared.status_code == 200
    # Falls back to the position version text.
    assert cleared.json()["dutyOverride"] is None
    assert "维护构建、部署" in cleared.json()["effectiveDuty"]

    history = client.get(f"/api/v1/agents/{devops['id']}/history", headers=headers).json()
    summaries = [entry["summary"] for entry in history["entries"]]
    assert "设置职责覆盖" in summaries
    assert "清除职责覆盖，回到岗位版本" in summaries


# ------------------------------------------------------------- model centre
def test_provider_and_model_crud_with_secret_status(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    headers = owner_headers(client)

    created = create_provider(client, headers)
    assert created.status_code == 201, created.text
    body = created.json()
    # The reference name is returned; the value never is.
    assert body["secretRef"] == "DEEPSEEK_API_KEY"
    assert body["secretConfigured"] is False
    assert body["status"] == "ENABLED"

    # Now the server environment has the variable: status flips without a write.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value-not-a-real-credential")
    centre = client.get("/api/v1/models/centre", headers=headers).json()
    assert centre["providers"][0]["secretConfigured"] is True

    model = create_model(client, headers, body["id"])
    assert model.status_code == 201, model.text
    assert model.json()["supportsJsonOutput"] is None  # never assumed

    disabled = client.patch(
        f"/api/v1/models/{model.json()['id']}",
        # expectedVersion is required since the R7 fix: a form that cannot state
        # which version it edited cannot be proven fresh.
        json={"status": "DISABLED", "expectedVersion": model.json()["version"]},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"


def test_provider_validation_rejects_bad_config(client):
    headers = owner_headers(client)
    bad_url = create_provider(client, headers, name="坏地址", base_url="ftp://example.com")
    assert bad_url.status_code == 422
    assert {item["field"] for item in bad_url.json()["fieldErrors"]} == {"baseUrl"}

    # Credentials must not be smuggled through a base URL.
    with_credentials = create_provider(
        client, headers, name="带凭据", base_url="https://user:sk-inline@example.com/v1"
    )
    assert with_credentials.status_code == 422

    # A literal secret is not accepted where an environment variable name belongs.
    literal = create_provider(client, headers, name="字面密钥", secret_ref="sk-abcdef1234567890")
    assert literal.status_code == 422
    assert {item["field"] for item in literal.json()["fieldErrors"]} == {"secretRef"}

    whitespace_model = create_model(client, headers, 1, logical_name="bad model")
    assert whitespace_model.status_code == 404  # provider 1 does not exist yet


def test_model_assignment_records_history_and_closes_previous(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")
    headers = owner_headers(client)
    provider = create_provider(client, headers).json()
    primary = create_model(client, headers, provider["id"]).json()
    secondary = create_model(
        client, headers, provider["id"], logical_name="deepseek-chat", label="DeepSeek Chat"
    ).json()
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    ceo = next(a for a in org["agents"] if a["positionCode"] == "CEO")

    first = client.post(
        f"/api/v1/agents/{ceo['id']}/model-assignment",
        json={"modelId": primary["id"]},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["model"]["label"] == "DeepSeek V4.1 Flash"

    second = client.post(
        f"/api/v1/agents/{ceo['id']}/model-assignment",
        json={"modelId": secondary["id"], "note": "换用更便宜的模型"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["model"]["label"] == "DeepSeek Chat"

    history = client.get(f"/api/v1/agents/{ceo['id']}/history", headers=headers).json()
    assignments = [e for e in history["entries"] if e["kind"] == "MODEL_ASSIGNMENT"]
    assert len(assignments) == 2
    assert any("（当前）" in e["summary"] for e in assignments)
    assert any("（已结束）" in e["summary"] for e in assignments)

    # Exactly one open binding, enforced by a partial unique index.
    with client.app.state.engine.connect() as connection:
        open_rows = connection.scalar(
            text(
                "SELECT count(*) FROM agent_model_assignments"
                " WHERE agent_id = :a AND ended_at IS NULL"
            ),
            {"a": ceo["id"]},
        )
        total_rows = connection.scalar(
            text("SELECT count(*) FROM agent_model_assignments WHERE agent_id = :a"),
            {"a": ceo["id"]},
        )
    assert (open_rows, total_rows) == (1, 2)


def test_assignment_refused_for_disabled_model(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")
    headers = owner_headers(client)
    provider = create_provider(client, headers).json()
    model = create_model(client, headers, provider["id"]).json()
    client.patch(
        f"/api/v1/models/{model['id']}",
        json={"status": "DISABLED", "expectedVersion": model["version"]},
        headers=headers,
    )
    create_company(client, headers)
    org = client.get("/api/v1/organization", headers=headers).json()
    ceo = next(a for a in org["agents"] if a["positionCode"] == "CEO")

    response = client.post(
        f"/api/v1/agents/{ceo['id']}/model-assignment",
        json={"modelId": model["id"]},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_BINDING_UNAVAILABLE"


# -------------------------------------------------------------------- probes
@pytest.fixture()
def probe_app(settings):
    """App with a scripted transport. Each test sets the handler it needs."""

    def build(handler):
        engine = build_engine(settings)
        transport = httpx.MockTransport(handler)
        app = create_app(
            settings,
            engine=engine,
            backend_root=BACKEND_ROOT,
            probe_transport=transport,
        )
        return app, engine

    return build


def test_probe_success_verifies_json_and_records_usage(probe_app, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=openai_ok_body())

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider(client, headers).json()
            model = create_model(client, headers, provider["id"]).json()

            response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["outcome"] == "SUCCESS"
            assert body["jsonOutputVerified"] is True
            assert body["callCount"] == 1
            assert body["usageKnown"] is True
            assert body["promptTokens"] == 21
            assert body["completionTokens"] == 7
            assert body["resolvedModelName"] == "deepseek-v4.1-flash-2026-01"
            assert "可能消耗少量 Token" in body["tokenWarning"]

            # Exactly one HTTP call, to the normalized endpoint, with the key in
            # the Authorization header and not in the body.
            assert len(calls) == 1
            assert str(calls[0].url) == "https://api.deepseek.com/v1/chat/completions"
            assert calls[0].headers["authorization"] == "Bearer test-key-value"
            assert b"test-key-value" not in calls[0].content

            centre = client.get("/api/v1/models/centre", headers=headers).json()
            json_model = next(m for m in centre["models"] if m["id"] == model["id"])
            assert json_model["supportsJsonOutput"] is True
            assert centre["usage"]["probeCalls"] == 1
            assert centre["usage"]["measuredCalls"] == 1
            assert centre["usage"]["promptTokens"] == 21
            assert centre["recentProbes"][0]["outcome"] == "SUCCESS"
    finally:
        engine.dispose()


def test_probe_wrong_key_reports_auth_failure_without_leaking_body(probe_app, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "wrong-key-value")
    provider_body = {
        "error": {
            "message": "Incorrect API key provided: wrong-key-value. Check your key.",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=provider_body)

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider(client, headers).json()
            model = create_model(client, headers, provider["id"]).json()
            response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["outcome"] == "FAILURE"
            assert body["errorCode"] == "AUTH_FAILED"
            assert body["callCount"] == 1
            # The provider's own message (which echoes the key) never surfaces.
            assert "wrong-key-value" not in response.text
            assert "Incorrect API key" not in response.text

            centre = client.get("/api/v1/models/centre", headers=headers)
            assert "wrong-key-value" not in centre.text
            assert "Incorrect API key" not in centre.text

            audit = client.get("/api/v1/audit/events", headers=headers)
            assert audit.status_code == 200
            assert "wrong-key-value" not in audit.text
            assert "Incorrect API key" not in audit.text

            # Failure is recorded against the model and does not claim JSON support.
            json_model = next(m for m in centre.json()["models"] if m["id"] == model["id"])
            assert json_model["supportsJsonOutput"] is False
            assert json_model["lastProbeOutcome"] == "FAILURE"
    finally:
        engine.dispose()


def test_probe_timeout_is_a_distinct_error(probe_app, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider(client, headers).json()
            model = create_model(client, headers, provider["id"]).json()
            response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["outcome"] == "FAILURE"
            assert body["errorCode"] == "TIMEOUT"
            assert body["callCount"] == 1
            assert body["jsonOutputVerified"] is None or body["jsonOutputVerified"] is False
    finally:
        engine.dispose()


def test_probe_invalid_json_output_is_reported_and_usage_kept(probe_app, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")

    def handler(request: httpx.Request) -> httpx.Response:
        # Valid OpenAI envelope, but the model's content is not JSON.
        return httpx.Response(
            200,
            json=openai_ok_body("Sure! Here is your answer: not json at all"),
        )

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider(client, headers).json()
            model = create_model(client, headers, provider["id"]).json()
            response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["outcome"] == "FAILURE"
            assert body["errorCode"] == "INVALID_JSON_OUTPUT"
            assert body["jsonOutputVerified"] is False
            # Tokens were really spent, so usage must be recorded, not zeroed.
            assert body["callCount"] == 1
            assert body["usageKnown"] is True
            assert body["promptTokens"] == 21

            centre = client.get("/api/v1/models/centre", headers=headers).json()
            assert centre["usage"]["probeCalls"] == 1
            assert centre["usage"]["measuredCalls"] == 1

            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM provider_usage_records")) == 1
                row = connection.execute(
                    text("SELECT prompt_tokens, usage_known FROM provider_usage_records")
                ).one()
            assert tuple(row) == (21, True)
    finally:
        engine.dispose()


def test_probe_without_secret_makes_no_call(probe_app, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=openai_ok_body())

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider(client, headers).json()
            model = create_model(client, headers, provider["id"]).json()
            response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["outcome"] == "FAILURE"
            assert body["errorCode"] == "MISSING_SECRET"
            assert body["callCount"] == 0
            # No request was attempted, so no usage row is invented.
            assert calls == []
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM provider_usage_records")) == 0
    finally:
        engine.dispose()


def test_probe_does_not_save_configuration(probe_app, monkeypatch):
    """Testing a connection must not create or modify configuration."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=openai_ok_body())

    app, engine = probe_app(handler)
    try:
        with TestClient(app) as client:
            headers = owner_headers(client)
            provider = create_provider(client, headers).json()
            model = create_model(client, headers, provider["id"]).json()
            before = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]

            client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)

            after = client.get("/api/v1/models/centre", headers=headers).json()["models"][0]
            # Only probe metadata changed; the configured identity did not.
            assert after["logicalName"] == before["logicalName"]
            assert after["label"] == before["label"]
            assert after["status"] == before["status"]
            assert after["maxOutputTokens"] == before["maxOutputTokens"]
            assert after["providerId"] == before["providerId"]
            # One call, and no automatic retry loop.
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM model_probes")) == 1
                assert connection.scalar(text("SELECT sum(call_count) FROM model_probes")) == 1
    finally:
        engine.dispose()


def test_probe_on_disabled_provider_is_refused_locally(client):
    headers = owner_headers(client)
    provider = create_provider(
        client, headers, name="停用供应商", secret_ref="DEEPSEEK_API_KEY", status="DISABLED"
    ).json()
    model = create_model(client, headers, provider["id"]).json()
    response = client.post(f"/api/v1/models/{model['id']}/probe", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "FAILURE"
    assert body["errorCode"] == "PROVIDER_DISABLED"
    assert body["callCount"] == 0


# --------------------------------------------------------- secret containment
def test_no_secret_value_in_responses_database_or_logs(client, engine, monkeypatch):
    """The configured credential must not appear anywhere observable."""
    secret_value = "sk-phase02-canary-0123456789abcdef"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret_value)
    headers = owner_headers(client)

    provider = create_provider(client, headers).json()
    model = create_model(client, headers, provider["id"]).json()
    create_company(client, headers, model_id=model["id"])

    responses = [
        client.get("/api/v1/models/centre", headers=headers),
        client.get("/api/v1/organization", headers=headers),
        client.get("/api/v1/company", headers=headers),
        client.get("/api/v1/audit/events", headers=headers),
        client.get("/api/v1/system/status", headers=headers),
    ]
    for response in responses:
        assert secret_value not in response.text, response.request.url

    with engine.connect() as connection:
        tables = (
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
            )
            .scalars()
            .all()
        )
        for table in tables:
            # Table names come from information_schema, not from user input.
            rows = connection.execute(text(f'SELECT * FROM "{table}"')).all()  # noqa: S608
            for row in rows:
                for value in row:
                    assert secret_value not in str(value), table

    # The stored provider configuration contains the reference name only.
    with engine.connect() as connection:
        secret_refs = connection.execute(text("SELECT secret_ref FROM providers")).scalars().all()
    assert secret_refs == ["DEEPSEEK_API_KEY"]


def test_audit_records_organization_and_model_changes(client):
    headers = owner_headers(client)
    create_company(client, headers)
    provider = create_provider(client, headers).json()
    create_model(client, headers, provider["id"])

    audit = client.get("/api/v1/audit/events?limit=200", headers=headers)
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()["events"]}
    assert {
        "COMPANY_INITIALIZED",
        "PROVIDER_CREATED",
        "PROVIDER_MODEL_CREATED",
    } <= actions

    org = client.get("/api/v1/organization", headers=headers).json()
    qa = next(p for p in org["positions"] if p["code"] == "QA")
    client.patch(
        f"/api/v1/positions/{qa['id']}",
        json={"duty": "新的职责文本。"},
        headers=headers,
    )
    agent = next(a for a in org["agents"] if a["positionCode"] == "TECH_LEAD")
    client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"displayName": "改名后的 DevOps"},
        headers=headers,
    )

    after = {
        event["action"]
        for event in client.get("/api/v1/audit/events?limit=200", headers=headers).json()["events"]
    }
    assert {"POSITION_UPDATED", "AGENT_UPDATED"} <= after


def test_audit_log_has_no_write_endpoint(client):
    headers = owner_headers(client)
    events = client.get("/api/v1/audit/events", headers=headers).json()
    first_id = events["events"][0]["id"] if events["events"] else 1
    assert client.delete(f"/api/v1/audit/events/{first_id}", headers=headers).status_code in (
        404,
        405,
    )
    assert client.patch(
        f"/api/v1/audit/events/{first_id}", json={}, headers=headers
    ).status_code in (404, 405)


# ------------------------------------------------------------- setup status
def test_setup_status_reports_company_initialization(client):
    headers = owner_headers(client)
    before = client.get("/api/v1/auth/setup-status").json()
    assert before["setupComplete"] is True
    assert before["companyInitialized"] is False
    assert before["companyName"] is None

    create_company(client, headers, name="状态测试公司")

    after = client.get("/api/v1/auth/setup-status").json()
    assert after["companyInitialized"] is True
    assert after["companyName"] == "状态测试公司"


def test_company_update_bumps_version_and_audits(client):
    headers = owner_headers(client)
    created = create_company(client, headers).json()
    updated = client.patch(
        "/api/v1/company",
        json={
            "name": "精一科技（更名）",
            "goal": created["goal"],
            "expectedVersion": created["version"],
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "精一科技（更名）"
    assert updated.json()["version"] == created["version"] + 1

    stale = client.patch(
        "/api/v1/company",
        json={
            "name": "过期写入",
            "goal": created["goal"],
            "expectedVersion": created["version"],
        },
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "CONCURRENT_UPDATE"


def test_company_initialization_with_model_binds_all_five(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-value")
    headers = owner_headers(client)
    provider = create_provider(client, headers).json()
    model = create_model(client, headers, provider["id"]).json()

    response = create_company(client, headers, model_id=model["id"])
    assert response.status_code == 201, response.text

    org = client.get("/api/v1/organization", headers=headers).json()
    assert all(agent["model"] is not None for agent in org["agents"])
    assert {agent["model"]["modelId"] for agent in org["agents"]} == {model["id"]}

    with client.app.state.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM agent_model_assignments WHERE ended_at IS NULL")
            )
            == 5
        )
