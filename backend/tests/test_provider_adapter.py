"""Adapter unit tests and template/migration consistency.

The adapter is the boundary between business code and providers, so its
behaviour is tested directly rather than only through the API: URL normalization,
timeout clamping, JSON-output verification strictness, secret resolution and
error mapping each fail in a different way.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.company_template import TEMPLATE_KEY, TEMPLATE_POSITIONS, TEMPLATE_VERSION
from app.domain.enums import ProbeErrorCode, ProviderKind
from app.providers.adapter import (
    AdapterError,
    AdapterErrorWithUsage,
    ProbeRequest,
    chat_completions_url,
    parse_json_object,
    probe,
    read_secret,
    resolve_timeout,
)
from app.repositories.organization_repository import CompanyRepository


# ----------------------------------------------------------------- URL shape
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        (
            "https://api.deepseek.com/v1/chat/completions",
            "https://api.deepseek.com/v1/chat/completions",
        ),
        (
            "http://127.0.0.1:11434/v1/",
            "http://127.0.0.1:11434/v1/chat/completions",
        ),
    ],
)
def test_base_url_normalization(configured, expected):
    assert chat_completions_url(configured) == expected


def test_blank_base_url_is_invalid_config():
    with pytest.raises(AdapterError) as caught:
        chat_completions_url("   ")
    assert caught.value.code is ProbeErrorCode.INVALID_CONFIG


# ------------------------------------------------------------------ timeout
@pytest.mark.parametrize(
    ("configured", "expected"), [(0.1, 1.0), (1, 1.0), (30, 30.0), (5000, 120.0)]
)
def test_timeout_is_clamped_into_a_safe_range(configured, expected):
    assert resolve_timeout(configured) == expected


# ----------------------------------------------------------- secret resolve
def test_secret_is_read_from_the_environment_only():
    assert read_secret(None, {}) is None
    assert read_secret("DEEPSEEK_API_KEY", {}) is None
    assert read_secret("DEEPSEEK_API_KEY", {"DEEPSEEK_API_KEY": "  "}) is None
    assert read_secret("DEEPSEEK_API_KEY", {"DEEPSEEK_API_KEY": " value "}) == "value"


# ------------------------------------------------------------ JSON checking
@pytest.mark.parametrize(
    ("text", "expected_ok"),
    [
        ('{"ok": true}', True),
        ('  {"ok": true}  ', True),
        ('```json\n{"ok": true}\n```', True),
        ('```\n{"ok": true}\n```', True),
        ("not json", False),
        ("", False),
        ("[1, 2, 3]", False),  # an array is not the required object
        ('{"ok": true} trailing prose', False),
        ("{broken", False),
    ],
)
def test_parse_json_object_is_strict(text, expected_ok):
    result = parse_json_object(text)
    assert (result is not None) is expected_ok


# ---------------------------------------------------------------- probe flow
def _request(handler, **overrides) -> ProbeRequest:
    defaults = {
        "base_url": "https://api.example.test",
        "model_name": "test-model",
        "kind": ProviderKind.OPENAI_COMPATIBLE,
        "secret_ref": "TEST_PROVIDER_KEY",
        "timeout_seconds": 5.0,
        "transport": httpx.MockTransport(handler),
        "environ": {"TEST_PROVIDER_KEY": "local-test-credential"},
    }
    defaults.update(overrides)
    return ProbeRequest(**defaults)


def _ok_body(content: str = '{"ok": true}', *, usage: dict | None = None) -> dict:
    body = {
        "model": "test-model-2026-01",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def test_probe_sends_one_request_with_bearer_and_json_output_request():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_body())

    result = probe(_request(handler))
    assert result.json_output_verified is True
    assert result.resolved_model_name == "test-model-2026-01"
    assert len(seen) == 1

    request = seen[0]
    assert str(request.url) == "https://api.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer local-test-credential"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    assert payload["max_tokens"] <= 64
    # The credential is never in the body.
    assert "local-test-credential" not in request.content.decode()


def test_probe_missing_secret_raises_before_any_call():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json=_ok_body())

    request = _request(handler, environ={})
    with pytest.raises(AdapterError) as caught:
        probe(request)
    assert caught.value.code is ProbeErrorCode.MISSING_SECRET
    assert calls == []


def test_probe_without_secret_ref_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=_ok_body())

    with pytest.raises(AdapterError) as caught:
        probe(_request(handler, secret_ref=None))
    assert caught.value.code is ProbeErrorCode.MISSING_SECRET


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, ProbeErrorCode.AUTH_FAILED),
        (403, ProbeErrorCode.AUTH_FAILED),
        (429, ProbeErrorCode.RATE_LIMITED),
        (400, ProbeErrorCode.HTTP_ERROR),
        (500, ProbeErrorCode.HTTP_ERROR),
        (503, ProbeErrorCode.HTTP_ERROR),
    ],
)
def test_http_status_maps_to_structured_code(status_code, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "provider says no"})

    with pytest.raises(AdapterError) as caught:
        probe(_request(handler))
    assert caught.value.code is expected
    # Provider prose never survives into the exception message.
    assert "provider says no" not in str(caught.value)


def test_probe_timeout_maps_to_timeout_code():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(AdapterError) as caught:
        probe(_request(handler))
    assert caught.value.code is ProbeErrorCode.TIMEOUT


def test_probe_connection_error_maps_to_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot connect", request=request)

    with pytest.raises(AdapterError) as caught:
        probe(_request(handler))
    assert caught.value.code is ProbeErrorCode.NETWORK_ERROR


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": 123}}]},
        {"model": "x"},
        [],
    ],
)
def test_malformed_envelope_is_invalid_response(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(AdapterError) as caught:
        probe(_request(handler))
    assert caught.value.code is ProbeErrorCode.INVALID_RESPONSE


def test_non_json_http_body_is_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(AdapterError) as caught:
        probe(_request(handler))
    assert caught.value.code is ProbeErrorCode.INVALID_RESPONSE


def test_invalid_model_json_carries_usage_for_accounting():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ok_body(
                "I cannot do JSON today",
                usage={"prompt_tokens": 12, "completion_tokens": 4},
            ),
        )

    with pytest.raises(AdapterErrorWithUsage) as caught:
        probe(_request(handler))
    assert caught.value.code is ProbeErrorCode.INVALID_JSON_OUTPUT
    assert caught.value.prompt_tokens == 12
    assert caught.value.completion_tokens == 4
    assert caught.value.latency_ms >= 0


def test_missing_usage_is_unknown_not_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    result = probe(_request(handler))
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    assert result.usage_known is False


def test_zero_usage_is_reported_as_measured_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_ok_body(usage={"prompt_tokens": 0, "completion_tokens": 0})
        )

    result = probe(_request(handler))
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)
    assert result.usage_known is True


def test_negative_usage_values_are_rejected_not_stored():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_ok_body(usage={"prompt_tokens": -5, "completion_tokens": -1})
        )

    result = probe(_request(handler))
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    assert result.usage_known is False


# ------------------------------------------------- template / migration sync
def test_template_module_matches_migrated_seed_rows(client):
    """The Python template and the seeded table must describe the same company.

    The migration carries a literal copy so it stays a stable historical record.
    This test is what keeps the copy honest.
    """
    body = client.get("/api/v1/company/template", headers=_owner(client)).json()
    seeded = body["positions"]

    assert body["templateKey"] == TEMPLATE_KEY
    assert body["templateVersion"] == TEMPLATE_VERSION
    assert len(seeded) == len(TEMPLATE_POSITIONS) == 5

    by_code = {row["code"]: row for row in seeded}
    for expected in TEMPLATE_POSITIONS:
        actual = by_code[expected.code]
        assert actual["title"] == expected.title
        assert actual["defaultAgentName"] == expected.default_agent_name
        assert actual["duty"] == expected.duty
        assert actual["reportsToCode"] == expected.reports_to_code
        assert actual["sortOrder"] == expected.sort_order


def test_company_repository_reads_five_template_rows(client):
    _owner(client)
    factory = client.app.state.session_factory
    with factory() as session:
        rows = CompanyRepository().template_positions(session, TEMPLATE_KEY, TEMPLATE_VERSION)
        assert [row.code for row in rows] == ["CEO", "PRODUCT_MANAGER", "TECH_LEAD", "QA", "DEVOPS"]


def _owner(client) -> dict[str, str]:
    from tests.conftest import setup_owner

    return setup_owner(client)
