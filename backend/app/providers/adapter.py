"""Unified model adapter.

Every model call in the product goes through this interface, so provider-specific
response shapes stay out of business code (docs/architecture.md). Phase 02 uses
it for connectivity probes only; Phase 03+ reuses it for task execution.

Guarantees this module provides:

* **One call per probe.** :func:`probe` issues exactly one HTTP request. No
  automatic retry loop: a retry would silently double the token spend the user
  was warned about.
* **Total deadline.** The configured timeout bounds the whole operation —
  connecting, reading and parsing — not just one socket read. A provider that
  drips bytes slowly cannot extend the wait past the budget, and the response
  body is size-capped so an unbounded stream cannot be used to exhaust memory.
* **Structured errors.** Provider bodies, headers and raw exception strings never
  escape this module. Callers get a :class:`AdapterError` with a stable
  :class:`~app.domain.enums.ProbeErrorCode` and a short Chinese explanation that
  is safe to display and to audit.
* **Untrusted metadata is validated, never blindly stored.** Anything the
  provider returns that the product would persist or display (the model name,
  token counts) is type-checked, range-checked against the destination column and
  screened against the live credential. A value that fails is dropped, not
  written; the call itself is still recorded.
* **Usage is parsed independently of the message envelope.** A malformed
  ``choices`` array must not discard token counts the provider did report.
* **JSON output verification.** The probe asks for a JSON object and then
  actually parses it; ``supports_json_output`` is only set to true when the
  parse succeeded. Protocol compatibility is not treated as proven capability.
* **No secrets in logs or payloads.** The credential is read from the server
  environment at call time and only placed in the Authorization header.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.domain.enums import ProbeErrorCode, ProviderKind

# httpx transports (real or httpx.MockTransport) share this base type; naming it
# keeps the public constructor signature readable.
type ProbeTransport = httpx.AsyncBaseTransport

# Load trusted CA certificates once at service startup, not for each paid call.
_TLS_CONTEXT = ssl.create_default_context()

# Bounds the probe prompt so a provider cannot be charged for a long generation.
PROBE_MAX_TOKENS = 64
PROBE_TIMEOUT_CEILING_SECONDS = 120.0
PROBE_TIMEOUT_FLOOR_SECONDS = 1.0

# A probe response is a tiny JSON object. Capping the body means a provider
# cannot stream indefinitely, and it bounds memory before parsing.
MAX_RESPONSE_BYTES = 256 * 1024

# Destination column limits. Values outside them must never reach the database:
# a too-long model name would otherwise surface as a bogus 503 after the call
# already happened (review finding R5).
MODEL_NAME_MAX_LENGTH = 160
# PostgreSQL INTEGER upper bound; token counts are stored in an INTEGER column.
TOKEN_COUNT_MAX = 2_147_483_647

_PROBE_SYSTEM_PROMPT = (
    "You are a connectivity probe for a deployment health check. "
    "Reply with a single JSON object and nothing else."
)
_PROBE_USER_PROMPT = 'Return exactly {"ok": true} as valid JSON.'

# Short, user-facing explanations per error category. Deliberately generic: the
# provider's own message is never reused, because it can echo request data.
_ERROR_MESSAGES: Mapping[ProbeErrorCode, str] = {
    ProbeErrorCode.MISSING_SECRET: "服务端未配置该供应商的密钥引用。",
    ProbeErrorCode.INVALID_CONFIG: "供应商配置无效（地址或模型名称不合法）。",
    ProbeErrorCode.AUTH_FAILED: "供应商拒绝了密钥（认证失败）。",
    ProbeErrorCode.RATE_LIMITED: "供应商限流，请稍后再测试。",
    ProbeErrorCode.TIMEOUT: "请求在超时时间内没有完成。",
    ProbeErrorCode.NETWORK_ERROR: "无法连接供应商地址。",
    ProbeErrorCode.HTTP_ERROR: "供应商返回了错误状态。",
    ProbeErrorCode.INVALID_RESPONSE: "供应商响应格式不符合预期。",
    ProbeErrorCode.INVALID_JSON_OUTPUT: "模型输出不是有效 JSON。",
    ProbeErrorCode.PROVIDER_DISABLED: "供应商已停用，未发起请求。",
    ProbeErrorCode.MODEL_DISABLED: "模型已停用，未发起请求。",
}


@dataclass(slots=True)
class AdapterError(Exception):
    """Structured, publishable adapter failure.

    ``code`` drives the UI badge and the audit detail; ``detail`` is a fixed
    Chinese sentence. Neither includes the provider's raw text.
    """

    code: ProbeErrorCode
    detail: str | None = None
    http_status: int | None = None

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail or _ERROR_MESSAGES[self.code]}"

    @property
    def message(self) -> str:
        return self.detail or _ERROR_MESSAGES[self.code]


@dataclass(slots=True)
class ProbeRequest:
    """Everything the adapter needs; the secret is resolved here, not passed in."""

    base_url: str
    model_name: str
    kind: ProviderKind
    secret_ref: str | None
    timeout_seconds: float
    max_output_tokens: int | None = None
    # Injected for tests: an httpx transport (e.g. MockTransport).
    transport: ProbeTransport | None = None
    environ: Mapping[str, str] | None = field(default=None)


@dataclass(slots=True)
class ProbeResult:
    """Outcome of one successful probe."""

    latency_ms: int
    json_output_verified: bool
    resolved_model_name: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    # True only when *both* dimensions were reported. A partial report is not a
    # complete measurement (review finding R4).
    usage_known: bool
    raw_usage_found: bool
    # Screening outcome, kept so the caller can audit "we called and dropped a
    # suspicious value" without storing the value itself.
    metadata_dropped: bool = False


def chat_completions_url(base_url: str) -> str:
    """Normalize a configured base URL into the chat-completions endpoint.

    Operators reasonably paste either ``https://host``, ``https://host/v1`` or the
    full ``.../v1/chat/completions``. All three must work, because a probe that
    fails on URL shape reports a connection problem the user cannot diagnose.
    """
    trimmed = base_url.strip().rstrip("/")
    if not trimmed:
        raise AdapterError(ProbeErrorCode.INVALID_CONFIG)
    if trimmed.endswith("/chat/completions"):
        return trimmed
    if trimmed.endswith("/v1"):
        return f"{trimmed}/chat/completions"
    return f"{trimmed}/v1/chat/completions"


def resolve_timeout(configured: float) -> float:
    return max(PROBE_TIMEOUT_FLOOR_SECONDS, min(float(configured), PROBE_TIMEOUT_CEILING_SECONDS))


def read_secret(secret_ref: str | None, environ: Mapping[str, str] | None = None) -> str | None:
    """Return the credential value for ``secret_ref`` from the server environment.

    Only an environment lookup happens here. There is no fallback to a default
    key and no way to pass a literal secret through the API, which is what makes
    "界面只显示是否配置" enforceable rather than a UI convention.
    """
    if not secret_ref:
        return None
    source = os.environ if environ is None else environ
    value = source.get(secret_ref, "").strip()
    return value or None


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a model reply that should contain one JSON object.

    Models wrap JSON in fences or add prose even when asked not to. The strict
    interpretation is used for the capability claim: only text that parses
    directly, or after stripping a fenced block, counts as verified. Anything
    else returns ``None`` so the caller records INVALID_JSON_OUTPUT rather than
    reporting success on a guess.
    """
    candidate = text.strip()
    if not candidate:
        return None
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        parsed = json.loads(candidate)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_usage_int(value: object) -> int | None:
    """Return a storable token count, or ``None`` when it is not usable.

    Rejects booleans, negatives, floats and anything above the destination
    column's range: a provider sending 2^31 must produce a normal "usage unknown"
    outcome, not a database error after a call that already cost money
    (review finding R5).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= TOKEN_COUNT_MAX:
        return value
    return None


def _map_status(status_code: int) -> ProbeErrorCode:
    if status_code in (401, 403):
        return ProbeErrorCode.AUTH_FAILED
    if status_code == 429:
        return ProbeErrorCode.RATE_LIMITED
    return ProbeErrorCode.HTTP_ERROR


def sanitize_provider_text(value: object, *, secret: str | None, max_length: int) -> str | None:
    """Return provider-supplied text only if it is safe to persist and display.

    Providers are untrusted. A response field can echo the request's credential
    (review finding R1: a provider that copies ``Authorization`` into ``model``
    would otherwise write the key into the database and return it to the
    browser). This function therefore:

    * rejects non-strings,
    * rejects blank values,
    * rejects anything containing the live credential (or a long run of its
      characters, so a partially-mangled echo is caught too),
    * rejects values longer than the destination column,
    * strips control characters that would corrupt logs or JSON output.

    A rejected value returns ``None``; the caller keeps the call record and marks
    the metadata as dropped rather than inventing a replacement.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > max_length:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        return None
    try:
        candidate.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if secret:
        if secret in candidate:
            return None
        # A truncated echo of the credential is still a leak. Compare a window
        # long enough not to collide with ordinary model names.
        window = 12
        if len(secret) >= window:
            for start in range(0, len(secret) - window + 1):
                if secret[start : start + window] in candidate:
                    return None
    return candidate


def extract_usage(body: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Parse token counts independently of the message envelope.

    A malformed ``choices`` array says nothing about whether the provider
    reported usage. Parsing here means a bad envelope still keeps the tokens that
    were really spent (review finding R5, third case).
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return (
        _coerce_usage_int(usage.get("prompt_tokens")),
        _coerce_usage_int(usage.get("completion_tokens")),
    )


def _extract_probe_content(body: Mapping[str, Any]) -> str | None:
    """Return the assistant content, or ``None`` when the envelope is malformed.

    Kept separate from the usage and model-name parsing so a bad envelope cannot
    discard otherwise usable facts about a call that already happened.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


async def _read_capped_body(response: httpx.Response, *, deadline: float) -> bytes:
    """Read a streamed response body with a size cap and a total-time check.

    The enclosing asyncio timeout cancels even a blocked read or incomplete
    response headers. Chunk checks additionally guard CPU work between awaits.

    ``MAX_RESPONSE_BYTES`` bounds memory, so an endless stream cannot be used to
    exhaust the process either.
    """
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_RESPONSE_BYTES:
                raise AdapterError(ProbeErrorCode.INVALID_RESPONSE)
        except ValueError:
            # A malformed Content-Length is untrusted metadata; fall through to
            # the streaming cap, which enforces the real limit.
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        if time.perf_counter() > deadline:
            raise AdapterError(ProbeErrorCode.TIMEOUT)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise AdapterError(ProbeErrorCode.INVALID_RESPONSE)
        chunks.append(chunk)
    return b"".join(chunks)


def probe(request: ProbeRequest) -> ProbeResult:
    """Synchronous service entry; HTTP I/O runs in a cancellable async scope.

    API routes and workers call this from synchronous code. Async callers should
    await ``probe_async`` directly. No detached worker or retry survives timeout.
    Test transports must implement AsyncBaseTransport (MockTransport does).
    """
    return asyncio.run(probe_async(request))


async def probe_async(request: ProbeRequest) -> ProbeResult:
    deadline = time.perf_counter() + resolve_timeout(request.timeout_seconds)
    try:
        async with asyncio.timeout(resolve_timeout(request.timeout_seconds)):
            result = await _probe(request, deadline=deadline)
            # Parsing is synchronous but capped at 256 KiB; never return success
            # after the budget, even if it expired during parsing.
            if time.perf_counter() >= deadline:
                raise AdapterError(ProbeErrorCode.TIMEOUT)
            return result
    except TimeoutError as exc:
        raise AdapterError(ProbeErrorCode.TIMEOUT) from exc


async def _probe(request: ProbeRequest, *, deadline: float) -> ProbeResult:
    """Perform a single OpenAI-compatible chat completion and verify the JSON.

    Raises :class:`AdapterError` on every failure path. Exactly one HTTP request
    is attempted; there is no retry, redirect following or fallback model.
    """
    base_url = request.base_url.strip()
    if not base_url:
        raise AdapterError(ProbeErrorCode.INVALID_CONFIG)
    if not request.model_name.strip():
        raise AdapterError(ProbeErrorCode.INVALID_CONFIG)

    secret = read_secret(request.secret_ref, request.environ)
    if request.secret_ref and secret is None:
        raise AdapterError(ProbeErrorCode.MISSING_SECRET)
    if not request.secret_ref:
        # A provider without a secret reference cannot be probed: sending an
        # empty bearer token would fail at the provider and look like a wrong key.
        raise AdapterError(ProbeErrorCode.MISSING_SECRET)

    url = chat_completions_url(base_url)
    timeout = resolve_timeout(request.timeout_seconds)
    max_tokens = min(
        request.max_output_tokens or PROBE_MAX_TOKENS,
        PROBE_MAX_TOKENS,
    )

    payload: dict[str, Any] = {
        "model": request.model_name,
        "messages": [
            {"role": "system", "content": _PROBE_SYSTEM_PROMPT},
            {"role": "user", "content": _PROBE_USER_PROMPT},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    started = time.perf_counter()
    status_code = 0
    try:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            verify=_TLS_CONTEXT,
            follow_redirects=False,
            transport=request.transport,
        )
        async with client:
            if time.perf_counter() >= deadline:
                raise AdapterError(ProbeErrorCode.TIMEOUT)
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                status_code = response.status_code
                body_bytes = await _read_capped_body(response, deadline=deadline)
    except httpx.TimeoutException as exc:
        raise AdapterError(ProbeErrorCode.TIMEOUT) from exc
    except AdapterError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise AdapterError(ProbeErrorCode.NETWORK_ERROR) from exc
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)

    if status_code >= 400:
        raise AdapterError(
            _map_status(status_code),
            detail=f"{_ERROR_MESSAGES[_map_status(status_code)]}（状态码 {status_code}）",
            http_status=status_code,
        )

    try:
        body = json.loads(body_bytes)
    except (ValueError, RecursionError) as exc:
        raise AdapterError(ProbeErrorCode.INVALID_RESPONSE) from exc

    if not isinstance(body, dict):
        raise AdapterError(ProbeErrorCode.INVALID_RESPONSE)

    # Usage first, envelope second: a broken choices array must not discard token
    # counts the provider actually reported (review finding R5, third case).
    prompt_tokens, completion_tokens = extract_usage(body)

    # The provider-reported model name is untrusted metadata. It is screened
    # before *any* envelope check, so a reply that both echoes the credential and
    # fails validation cannot leak through an error path (review finding R1).
    raw_resolved = body.get("model")
    resolved_name = sanitize_provider_text(
        raw_resolved, secret=secret, max_length=MODEL_NAME_MAX_LENGTH
    )
    metadata_dropped = raw_resolved is not None and resolved_name is None

    content = _extract_probe_content(body)
    if content is None:
        raise AdapterErrorWithUsage(
            ProbeErrorCode.INVALID_RESPONSE,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            resolved_model_name=resolved_name,
            metadata_dropped=metadata_dropped,
        )

    verified = parse_json_object(content) is not None

    if not verified:
        # The call succeeded but the capability claim did not. Report usage
        # anyway: the tokens were really spent.
        raise AdapterErrorWithUsage(
            ProbeErrorCode.INVALID_JSON_OUTPUT,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            resolved_model_name=resolved_name,
            metadata_dropped=metadata_dropped,
        )

    return ProbeResult(
        latency_ms=latency_ms,
        json_output_verified=True,
        resolved_model_name=resolved_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        # Both dimensions must be reported for the measurement to count as
        # complete; a partial report is unknown, not zero (review finding R4).
        usage_known=prompt_tokens is not None and completion_tokens is not None,
        raw_usage_found=isinstance(body.get("usage"), dict),
        metadata_dropped=metadata_dropped,
    )


class AdapterErrorWithUsage(AdapterError):
    """An adapter failure that still incurred billable tokens.

    Subclasses :class:`AdapterError` so every existing ``except AdapterError``
    handler keeps working, while callers that care about accounting can read the
    usage facts. An invalid-JSON reply, or a malformed envelope that still carried
    a valid ``usage`` object, consumed real tokens; hiding that would under-report
    spend.
    """

    __slots__ = (
        "completion_tokens",
        "latency_ms",
        "metadata_dropped",
        "prompt_tokens",
        "resolved_model_name",
    )

    def __init__(
        self,
        code: ProbeErrorCode,
        *,
        detail: str | None = None,
        http_status: int | None = None,
        latency_ms: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        resolved_model_name: str | None,
        metadata_dropped: bool = False,
    ) -> None:
        super().__init__(code=code, detail=detail, http_status=http_status)
        self.latency_ms = latency_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.resolved_model_name = resolved_model_name
        self.metadata_dropped = metadata_dropped
