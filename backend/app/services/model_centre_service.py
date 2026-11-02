"""Model centre: providers, models, assignments and connectivity probes.

Separation the phase plan requires ("保存配置与联网探测分开"):

* :meth:`ModelCentreService.create_provider` / :meth:`update_provider` /
  :meth:`create_model` / :meth:`update_model` only persist configuration. They
  never call a provider.
* :meth:`ModelCentreService.probe_model` performs exactly one provider call,
  records a probe row plus a usage row, and writes its own audit event. It does
  **not** save any configuration; a probe uses the already-persisted provider
  settings, so "test" can never silently apply an unconfirmed edit.

Secret handling: only ``secret_ref`` (an environment variable *name*) is stored.
``secret_configured`` in the response is computed by looking at the process
environment at read time. There is no endpoint that returns, logs or accepts a
credential value, which is verified by tests that scan responses, the database,
logs and the built frontend bundle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import (
    AuditAction,
    AuditActorType,
    ProbeErrorCode,
    ProbeOutcome,
    ProviderKind,
    ProviderStatus,
    UsageSource,
)
from app.domain.errors import (
    ConcurrentUpdateError,
    FieldError,
    ModelNotFoundError,
    ProviderNotFoundError,
    ValidationFailedError,
)
from app.models.organization import Model, ModelProbe, Provider
from app.providers.adapter import (
    AdapterError,
    AdapterErrorWithUsage,
    ProbeRequest,
    ProbeResult,
    probe,
    read_secret,
)
from app.repositories.auth_repository import AuditRepository
from app.repositories.organization_repository import (
    ModelRepository,
    ProbeRepository,
    ProviderRepository,
    UsageRepository,
)

logger = logging.getLogger(__name__)

MAX_PROVIDER_NAME = 80
MAX_BASE_URL = 300
MAX_MODEL_NAME = 160
MAX_NOTES = 500
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0

ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# Environment variable *name* used by the preset; never a credential value.
DEEPSEEK_SECRET_REF = "DEEPSEEK_API_KEY"  # noqa: S105
DEEPSEEK_MODEL = "deepseek-v4.1-flash"


@dataclass(slots=True)
class ProviderInput:
    """Validated provider fields. Typed so persistence cannot receive junk."""

    name: str
    kind: str
    base_url: str
    secret_ref: str | None
    status: str
    notes: str | None
    timeout_seconds: float


@dataclass(slots=True)
class ProviderView:
    provider: Provider
    models: Sequence[Model]
    secret_configured: bool


@dataclass(slots=True)
class ProbeOutcomeRecord:
    probe: ModelProbe
    message: str
    usage_known: bool


def _clean(value: str) -> str:
    return value.strip()


def config_fingerprint(
    *, kind: str, base_url: str, secret_ref: str | None, timeout_seconds: float
) -> str:
    """Hash exactly the fields that determine where and how a call is made.

    Probe evidence is only meaningful for the configuration that produced it. The
    fingerprint deliberately excludes the display name, notes and status: renaming
    a provider does not change what a probe measured, but changing the endpoint,
    the credential reference, the protocol kind or the timeout does (review
    findings R3 and R7). ``secret_ref`` — a variable *name* — is hashed rather
    than the secret value, so rotating the value of the same variable is a
    configuration change the operator chose to make without exposing anything.
    """
    payload = {
        "kind": kind,
        "baseUrl": base_url.rstrip("/"),
        "secretRef": secret_ref or "",
        "timeoutSeconds": round(float(timeout_seconds), 3),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valid_base_url(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    # Reject credentials in the URL: a base URL is configuration, not a secret
    # channel, and a pasted key would otherwise leak into the database and UI.
    return parts.username is None and parts.password is None


class ModelCentreService:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._providers = ProviderRepository()
        self._models = ModelRepository()
        self._probes = ProbeRepository()
        self._usage = UsageRepository()
        self._audit = AuditRepository()
        # Tests inject an httpx.MockTransport here. Production passes nothing,
        # so the real network path is the only one that exists in a deployment.
        self._transport = transport
        # A separate transaction boundary is required for the pre-call intent
        # commit: the request transaction must not be the only place the fact of
        # a paid call exists (review finding R2).
        self._session_factory = session_factory

    # ------------------------------------------------------------- queries
    def secret_configured(self, secret_ref: str | None) -> bool:
        return read_secret(secret_ref) is not None

    def list_providers(self, session: Session) -> list[ProviderView]:
        providers = self._providers.list_all(session)
        return [
            ProviderView(
                provider=provider,
                models=self._models.list_for_provider(session, provider.id),
                secret_configured=self.secret_configured(provider.secret_ref),
            )
            for provider in providers
        ]

    def get_provider_view(self, session: Session, provider_id: int) -> ProviderView:
        provider = self._providers.get(session, provider_id)
        if provider is None:
            raise ProviderNotFoundError()
        return ProviderView(
            provider=provider,
            models=self._models.list_for_provider(session, provider.id),
            secret_configured=self.secret_configured(provider.secret_ref),
        )

    def list_models(self, session: Session) -> Sequence[Model]:
        return self._models.list_all(session)

    def recent_probes(self, session: Session, model_id: int | None = None) -> Sequence[ModelProbe]:
        if model_id is None:
            return self._probes.recent(session, limit=20)
        model = self._models.get(session, model_id)
        if model is None:
            raise ModelNotFoundError()
        return self._probes.recent_for_model(session, model_id, limit=10)

    def usage_summary(self, session: Session) -> dict[str, int | None]:
        summary = self._usage.summarize(session)
        summary["pending_probes"] = self._probes.count_pending(session)
        return summary

    def deepseek_preset(self) -> dict[str, object]:
        """Preset values for the DeepSeek provider form.

        These are defaults the operator may edit, not a verified capability
        claim. The preset exists so the common path needs no typing; it never
        pre-fills a key.
        """
        return {
            "kind": ProviderKind.DEEPSEEK.value,
            "baseUrl": DEEPSEEK_BASE_URL,
            "secretRef": DEEPSEEK_SECRET_REF,
            "suggestedModel": DEEPSEEK_MODEL,
            "notes": "DeepSeek 的 OpenAI 兼容接口；模型名可按供应商文档调整。",
        }

    # ----------------------------------------------------------- providers
    def create_provider(
        self,
        session: Session,
        *,
        name: str,
        kind: str,
        base_url: str,
        secret_ref: str | None,
        status: str,
        notes: str | None,
        timeout_seconds: float | None,
        actor_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Provider:
        cleaned = self._validate_provider(
            name=name,
            kind=kind,
            base_url=base_url,
            secret_ref=secret_ref,
            status=status,
            notes=notes,
            timeout_seconds=timeout_seconds,
        )
        if self._providers.get_by_name(session, cleaned.name) is not None:
            raise ValidationFailedError([FieldError("name", "该供应商名称已存在。")])

        provider = self._providers.create(
            session,
            name=cleaned.name,
            kind=cleaned.kind,
            base_url=cleaned.base_url,
            secret_ref=cleaned.secret_ref,
            status=cleaned.status,
            notes=cleaned.notes,
            timeout_seconds=cleaned.timeout_seconds,
            config_fingerprint=config_fingerprint(
                kind=cleaned.kind,
                base_url=cleaned.base_url,
                secret_ref=cleaned.secret_ref,
                timeout_seconds=cleaned.timeout_seconds,
            ),
        )
        self._audit.add(
            session,
            action=AuditAction.PROVIDER_CREATED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="PROVIDER",
            target_id=str(provider.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=(
                f"name={provider.name};kind={provider.kind};"
                f"secret_ref={provider.secret_ref or 'none'};"
                f"secret_configured={self.secret_configured(provider.secret_ref)}"
            ),
        )
        logger.info("provider created", extra={"user_id": actor_user_id})
        return provider

    def update_provider(
        self,
        session: Session,
        *,
        provider_id: int,
        name: str | None,
        kind: str | None,
        base_url: str | None,
        secret_ref: str | None,
        change_secret_ref: bool,
        status: str | None,
        notes: str | None,
        notes_provided: bool,
        timeout_seconds: float | None,
        expected_version: int,
        actor_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Provider:
        """Update provider configuration with optimistic concurrency.

        ``expected_version`` is the value the client read. A row lock alone only
        serialises writes; it cannot tell whether the submitted form was built
        from the current row, so two operators editing the same provider would
        silently overwrite each other (review finding R7). A mismatch is a 409.

        ``notes_provided`` separates "field omitted" from "field explicitly
        cleared": the UI sends ``notes: null`` to erase the text, which must not
        be read as "keep the old value" (review finding R9).
        """
        provider = self._providers.get(session, provider_id, for_update=True)
        if provider is None:
            raise ProviderNotFoundError()
        if provider.version != expected_version:
            raise ConcurrentUpdateError("provider")

        resolved_secret_ref = provider.secret_ref if not change_secret_ref else secret_ref
        resolved_notes = notes if notes_provided else provider.notes
        cleaned = self._validate_provider(
            name=name if name is not None else provider.name,
            kind=kind if kind is not None else provider.kind,
            base_url=base_url if base_url is not None else provider.base_url,
            secret_ref=resolved_secret_ref,
            status=status if status is not None else provider.status,
            notes=resolved_notes,
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else provider.timeout_seconds
            ),
        )

        existing = self._providers.get_by_name(session, cleaned.name)
        if existing is not None and existing.id != provider.id:
            raise ValidationFailedError([FieldError("name", "该供应商名称已存在。")])

        previous_fingerprint = provider.config_fingerprint
        changed: list[str] = []
        if provider.name != cleaned.name:
            provider.name = cleaned.name
            changed.append("name")
        if provider.kind != cleaned.kind:
            provider.kind = cleaned.kind
            changed.append("kind")
        if provider.base_url != cleaned.base_url:
            provider.base_url = cleaned.base_url
            changed.append("base_url")
        if provider.secret_ref != cleaned.secret_ref:
            provider.secret_ref = cleaned.secret_ref
            changed.append("secret_ref")
        if provider.status != cleaned.status:
            provider.status = cleaned.status
            changed.append("status")
        if provider.notes != cleaned.notes:
            provider.notes = cleaned.notes
            changed.append("notes")
        if float(provider.timeout_seconds) != cleaned.timeout_seconds:
            provider.timeout_seconds = cleaned.timeout_seconds
            changed.append("timeout_seconds")
        if not changed:
            return provider

        provider.version += 1
        new_fingerprint = config_fingerprint(
            kind=provider.kind,
            base_url=provider.base_url,
            secret_ref=provider.secret_ref,
            timeout_seconds=float(provider.timeout_seconds),
        )
        provider.config_fingerprint = new_fingerprint

        invalidated = 0
        if new_fingerprint != previous_fingerprint:
            # The probe evidence describes the old configuration. Clear the
            # capability claim so the UI cannot show "探测已验证" for an endpoint
            # that has never been probed (review finding R3). History is kept:
            # only the current-state fields are reset, and the old probe rows stay.
            invalidated = self._invalidate_probe_evidence(session, provider.id)
        session.flush()

        self._audit.add(
            session,
            action=AuditAction.PROVIDER_UPDATED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="PROVIDER",
            target_id=str(provider.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=(
                f"fields={','.join(sorted(changed))};version={provider.version};"
                f"secret_ref={provider.secret_ref or 'none'};"
                f"secret_configured={self.secret_configured(provider.secret_ref)};"
                f"capability_invalidated={invalidated}"
            ),
        )
        return provider

    def _invalidate_probe_evidence(self, session: Session, provider_id: int) -> int:
        """Reset capability fields whose probe used a different configuration.

        Only the *current-state* fields are cleared. ``model_probes`` rows and
        ``provider_usage_records`` are untouched: the calls really happened and
        their evidence must survive a configuration change.
        """
        invalidated = 0
        for model in self._models.list_for_provider(session, provider_id):
            if model.supports_json_output is None and model.last_probe_outcome is None:
                continue
            model.supports_json_output = None
            model.resolved_name = None
            model.probe_config_fingerprint = None
            model.last_probe_outcome = None
            model.last_probe_detail = "配置已变更，需要重新测试连接。"
            invalidated += 1
        return invalidated

    def _validate_provider(
        self,
        *,
        name: str,
        kind: str,
        base_url: str,
        secret_ref: str | None,
        status: str,
        notes: str | None,
        timeout_seconds: float | None,
    ) -> ProviderInput:
        cleaned_name = _clean(name)
        cleaned_url = _clean(base_url).rstrip("/")
        cleaned_secret = _clean(secret_ref) if secret_ref else None
        cleaned_notes = _clean(notes) if notes else None

        field_errors: list[FieldError] = []
        if not cleaned_name:
            field_errors.append(FieldError("name", "请输入供应商名称。"))
        elif len(cleaned_name) > MAX_PROVIDER_NAME:
            field_errors.append(FieldError("name", f"名称不能超过 {MAX_PROVIDER_NAME} 个字符。"))
        if kind not in {item.value for item in ProviderKind}:
            field_errors.append(FieldError("kind", "未知的供应商类型。"))
        if not _valid_base_url(cleaned_url):
            field_errors.append(
                FieldError("baseUrl", "请输入以 http:// 或 https:// 开头、不含账号密码的地址。")
            )
        elif len(cleaned_url) > MAX_BASE_URL:
            field_errors.append(FieldError("baseUrl", f"地址不能超过 {MAX_BASE_URL} 个字符。"))
        if cleaned_secret and not ENV_NAME_PATTERN.match(cleaned_secret):
            field_errors.append(
                FieldError(
                    "secretRef",
                    "密钥引用必须是服务端环境变量名（大写字母、数字、下划线，以字母开头）。",
                )
            )
        elif cleaned_secret and len(cleaned_secret) > 128:
            field_errors.append(FieldError("secretRef", "密钥引用名称过长。"))
        if status not in {item.value for item in ProviderStatus}:
            field_errors.append(FieldError("status", "未知的供应商状态。"))
        if cleaned_secret and len(cleaned_secret) > 128:
            field_errors.append(FieldError("secretRef", "密钥引用名称过长。"))
        if cleaned_notes and len(cleaned_notes) > MAX_NOTES:
            field_errors.append(FieldError("notes", f"备注不能超过 {MAX_NOTES} 个字符。"))
        timeout = DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
        if not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
            field_errors.append(
                FieldError(
                    "timeoutSeconds",
                    f"超时需在 {MIN_TIMEOUT_SECONDS:.0f} 到 {MAX_TIMEOUT_SECONDS:.0f} 秒之间。",
                )
            )
        if field_errors:
            raise ValidationFailedError(field_errors)

        return ProviderInput(
            name=cleaned_name,
            kind=kind,
            base_url=cleaned_url,
            secret_ref=cleaned_secret,
            status=status,
            notes=cleaned_notes,
            timeout_seconds=timeout,
        )

    # -------------------------------------------------------------- models
    def create_model(
        self,
        session: Session,
        *,
        provider_id: int,
        logical_name: str,
        label: str | None,
        max_output_tokens: int | None,
        status: str,
        actor_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Model:
        provider = self._providers.get(session, provider_id)
        if provider is None:
            raise ProviderNotFoundError()

        cleaned_name = _clean(logical_name)
        cleaned_label = _clean(label) if label else cleaned_name
        field_errors: list[FieldError] = []
        if not cleaned_name:
            field_errors.append(FieldError("logicalName", "请输入模型 ID。"))
        elif len(cleaned_name) > MAX_MODEL_NAME:
            field_errors.append(
                FieldError("logicalName", f"模型 ID 不能超过 {MAX_MODEL_NAME} 个字符。")
            )
        # A model ID is an identifier, not free text: spaces and newlines would
        # be sent verbatim to the provider and produce a confusing failure.
        elif any(ch.isspace() for ch in cleaned_name):
            field_errors.append(FieldError("logicalName", "模型 ID 不能包含空白字符。"))
        if not cleaned_label or len(cleaned_label) > MAX_MODEL_NAME:
            field_errors.append(FieldError("label", "显示名称需为 1 到 160 个字符。"))
        if status not in {item.value for item in ProviderStatus}:
            field_errors.append(FieldError("status", "未知的模型状态。"))
        if max_output_tokens is not None and max_output_tokens <= 0:
            field_errors.append(FieldError("maxOutputTokens", "输出上限必须大于 0。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        if self._models.get_by_logical_name(session, provider_id, cleaned_name) is not None:
            raise ValidationFailedError([FieldError("logicalName", "该供应商下已存在同名模型。")])

        model = self._models.create(
            session,
            provider_id=provider_id,
            logical_name=cleaned_name,
            label=cleaned_label,
            status=status,
            max_output_tokens=max_output_tokens,
        )
        self._audit.add(
            session,
            action=AuditAction.PROVIDER_MODEL_CREATED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="MODEL",
            target_id=str(model.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"provider={provider.name};model={model.logical_name}",
        )
        return model

    def update_model(
        self,
        session: Session,
        *,
        model_id: int,
        label: str | None,
        max_output_tokens: int | None,
        change_max_output_tokens: bool,
        status: str | None,
        expected_version: int,
        actor_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Model:
        model = self._models.get(session, model_id, for_update=True)
        if model is None:
            raise ModelNotFoundError()
        if model.version != expected_version:
            raise ConcurrentUpdateError("model")

        field_errors: list[FieldError] = []
        cleaned_label = _clean(label) if label is not None else model.label
        if not cleaned_label or len(cleaned_label) > MAX_MODEL_NAME:
            field_errors.append(FieldError("label", "显示名称需为 1 到 160 个字符。"))
        new_status = status if status is not None else model.status
        if new_status not in {item.value for item in ProviderStatus}:
            field_errors.append(FieldError("status", "未知的模型状态。"))
        new_max = max_output_tokens if change_max_output_tokens else model.max_output_tokens
        if new_max is not None and new_max <= 0:
            field_errors.append(FieldError("maxOutputTokens", "输出上限必须大于 0。"))
        if field_errors:
            raise ValidationFailedError(field_errors)

        changed: list[str] = []
        if model.label != cleaned_label:
            model.label = cleaned_label
            changed.append("label")
        if model.status != new_status:
            model.status = new_status
            changed.append("status")
        if model.max_output_tokens != new_max:
            model.max_output_tokens = new_max
            changed.append("maxOutputTokens")
        if not changed:
            return model
        model.version += 1
        session.flush()

        self._audit.add(
            session,
            action=AuditAction.PROVIDER_MODEL_UPDATED.value,
            outcome="SUCCESS",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="MODEL",
            target_id=str(model.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=f"model={model.logical_name};fields={','.join(changed)}",
        )
        return model

    # --------------------------------------------------------------- probe
    def probe_model(
        self,
        session: Session,
        *,
        model_id: int,
        actor_user_id: int,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> ProbeOutcomeRecord:
        """Run one connectivity probe and persist the outcome in two phases.

        Phase 1 (own committed transaction): the PENDING intent row. If the
        process dies during the call, this row is the evidence that a paid request
        may have happened — "结果与用量未知", never success, never zero cost, and
        never automatically retried (review findings R2 and R5).

        Phase 2 (the request transaction): the result, the usage row, the
        capability fields and the audit event. Every value the provider supplied
        is already validated by the adapter, so a hostile response can no longer
        turn into a database error that rolls the evidence back.

        Exactly one HTTP request is attempted: no retry, no fallback model.
        """
        model = self._models.get(session, model_id)
        if model is None:
            raise ModelNotFoundError()
        provider = self._providers.get(session, model.provider_id)
        if provider is None:
            raise ProviderNotFoundError()

        fingerprint = provider.config_fingerprint or config_fingerprint(
            kind=provider.kind,
            base_url=provider.base_url,
            secret_ref=provider.secret_ref,
            timeout_seconds=float(provider.timeout_seconds),
        )

        # ---- phase 0: refusals that make no call at all --------------------
        if provider.status != ProviderStatus.ENABLED.value:
            return self._record_local_refusal(
                session,
                model=model,
                provider=provider,
                code=ProbeErrorCode.PROVIDER_DISABLED,
                actor_user_id=actor_user_id,
                request_id=request_id,
                ip_address=ip_address,
                config_fp=fingerprint,
            )
        if model.status != ProviderStatus.ENABLED.value:
            return self._record_local_refusal(
                session,
                model=model,
                provider=provider,
                code=ProbeErrorCode.MODEL_DISABLED,
                actor_user_id=actor_user_id,
                request_id=request_id,
                ip_address=ip_address,
                config_fp=fingerprint,
            )

        # ---- phase 1: commit the intent before touching the network --------
        probe_row = self._commit_probe_intent(
            session,
            model_id=model.id,
            provider_id=provider.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            config_fp=fingerprint,
        )

        request = ProbeRequest(
            base_url=provider.base_url,
            model_name=model.logical_name,
            kind=ProviderKind(provider.kind),
            secret_ref=provider.secret_ref,
            timeout_seconds=provider.timeout_seconds,
            max_output_tokens=model.max_output_tokens,
            transport=self._transport,
        )

        try:
            result = probe(request)
        except AdapterErrorWithUsage as exc:
            # The call happened and cost tokens (or at least was answered). Record
            # the failure together with whatever usage the provider did report.
            return self._record_failure(
                session,
                row=probe_row,
                model=model,
                provider=provider,
                code=exc.code,
                actor_user_id=actor_user_id,
                request_id=request_id,
                ip_address=ip_address,
                latency_ms=exc.latency_ms,
                call_count=1,
                prompt_tokens=exc.prompt_tokens,
                completion_tokens=exc.completion_tokens,
                resolved_name=exc.resolved_model_name,
                config_fp=fingerprint,
                metadata_dropped=exc.metadata_dropped,
            )
        except AdapterError as exc:
            local_only = exc.code in _LOCAL_ONLY_CODES
            return self._record_failure(
                session,
                row=probe_row,
                model=model,
                provider=provider,
                code=exc.code,
                actor_user_id=actor_user_id,
                request_id=request_id,
                ip_address=ip_address,
                latency_ms=None,
                call_count=0 if local_only else 1,
                detail=exc.detail,
                config_fp=fingerprint,
            )

        return self._record_success(
            session,
            row=probe_row,
            model=model,
            provider=provider,
            result=result,
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            config_fp=fingerprint,
        )

    def _commit_probe_intent(
        self,
        session: Session,
        *,
        model_id: int,
        provider_id: int,
        actor_user_id: int,
        request_id: str | None,
        config_fp: str,
    ) -> ModelProbe:
        """Write and COMMIT the PENDING intent in its own transaction.

        Uses a dedicated session so committing does not also commit whatever else
        the request has pending. Without a configured factory (unit tests calling
        the service directly) the intent still exists in the caller's session, and
        the adapter-level call still happens exactly once.
        """
        if self._session_factory is None:
            return self._probes.create_intent(
                session,
                model_id=model_id,
                provider_id=provider_id,
                initiated_by_user_id=actor_user_id,
                request_id=request_id,
                config_fingerprint=config_fp,
            )
        with self._session_factory() as intent_session:
            row = self._probes.create_intent(
                intent_session,
                model_id=model_id,
                provider_id=provider_id,
                initiated_by_user_id=actor_user_id,
                request_id=request_id,
                config_fingerprint=config_fp,
            )
            intent_session.commit()
            probe_id = row.id
        # Re-read into the caller's session so phase 2 updates the committed row.
        committed = self._probes.get(session, probe_id)
        if committed is None:  # pragma: no cover - the row was just committed
            raise RuntimeError("probe intent row disappeared after commit")
        return committed

    def _record_local_refusal(
        self,
        session: Session,
        *,
        model: Model,
        provider: Provider,
        code: ProbeErrorCode,
        actor_user_id: int,
        request_id: str | None,
        ip_address: str | None,
        config_fp: str,
    ) -> ProbeOutcomeRecord:
        """Record a refusal that made no call. call_count stays 0."""
        row = self._probes.create_refusal(
            session,
            model_id=model.id,
            provider_id=provider.id,
            initiated_by_user_id=actor_user_id,
            request_id=request_id,
            config_fingerprint=config_fp,
        )
        return self._finish_row(
            session,
            row=row,
            model=model,
            provider=provider,
            outcome=ProbeOutcome.FAILURE,
            error_code=code,
            message=_failure_message(code),
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            call_count=0,
            prompt_tokens=None,
            completion_tokens=None,
            resolved_name=None,
            latency_ms=None,
            config_fp=config_fp,
            apply_capability=False,
        )

    def _record_success(
        self,
        session: Session,
        *,
        row: ModelProbe,
        model: Model,
        provider: Provider,
        result: ProbeResult,
        actor_user_id: int,
        request_id: str | None,
        ip_address: str | None,
        config_fp: str,
    ) -> ProbeOutcomeRecord:
        return self._finish_row(
            session,
            row=row,
            model=model,
            provider=provider,
            outcome=ProbeOutcome.SUCCESS,
            error_code=None,
            message="JSON 输出验证通过。",
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            call_count=1,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            resolved_name=result.resolved_model_name,
            latency_ms=result.latency_ms,
            json_verified=result.json_output_verified,
            config_fp=config_fp,
            apply_capability=True,
            metadata_dropped=result.metadata_dropped,
        )

    def _record_failure(
        self,
        session: Session,
        *,
        row: ModelProbe,
        model: Model,
        provider: Provider,
        code: ProbeErrorCode,
        actor_user_id: int,
        request_id: str | None,
        ip_address: str | None,
        latency_ms: int | None = None,
        call_count: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        resolved_name: str | None = None,
        detail: str | None = None,
        config_fp: str | None = None,
        metadata_dropped: bool = False,
    ) -> ProbeOutcomeRecord:
        return self._finish_row(
            session,
            row=row,
            model=model,
            provider=provider,
            outcome=ProbeOutcome.FAILURE,
            error_code=code,
            message=detail or _failure_message(code),
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            call_count=call_count,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            resolved_name=resolved_name,
            latency_ms=latency_ms,
            json_verified=False if call_count else None,
            config_fp=config_fp,
            # A failed call does not prove capability, but it must not erase a
            # *matching* configuration's earlier success either; the capability
            # update below handles that rule in one place.
            apply_capability=True,
            metadata_dropped=metadata_dropped,
        )

    def _finish_row(
        self,
        session: Session,
        *,
        row: ModelProbe,
        model: Model,
        provider: Provider,
        outcome: ProbeOutcome,
        error_code: ProbeErrorCode | None,
        message: str,
        actor_user_id: int,
        request_id: str | None,
        ip_address: str | None,
        call_count: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        resolved_name: str | None,
        latency_ms: int | None,
        json_verified: bool | None = None,
        config_fp: str | None = None,
        apply_capability: bool = True,
        metadata_dropped: bool = False,
    ) -> ProbeOutcomeRecord:
        """Write the outcome, usage, capability and audit for one probe.

        Capability is only updated when the result belongs to the *current*
        configuration. A late result from a superseded configuration must not
        overwrite what a newer probe found (review finding R3), which is why the
        fingerprint is compared here rather than trusted from the caller.
        """
        from app.repositories.auth_repository import utcnow

        # Match update_provider's lock order: provider, then model. Both reads
        # refresh the identity map: these objects predate the external call.
        # Holding the provider lock through commit also closes the race between
        # checking its fingerprint and writing the model's current capability.
        current_provider = self._providers.get(session, provider.id, for_update=True)
        current_model = self._models.get(session, model.id, for_update=True)
        if current_provider is None or current_model is None:
            raise RuntimeError("probe configuration disappeared")
        provider, model = current_provider, current_model

        row.outcome = outcome.value
        row.error_code = error_code.value if error_code else None
        row.detail = message[:300]
        row.finished_at = utcnow()
        row.call_count = call_count
        row.latency_ms = latency_ms
        row.prompt_tokens = prompt_tokens
        row.completion_tokens = completion_tokens
        row.resolved_model_name = resolved_name
        row.json_output_verified = json_verified
        row.config_fingerprint = config_fp

        current_fp = provider.config_fingerprint
        evidence_is_current = bool(apply_capability and current_fp and config_fp == current_fp)

        if evidence_is_current:
            model.last_probe_outcome = outcome.value
            model.last_probe_at = row.finished_at
            model.last_probe_detail = message[:300] + (
                "（供应商返回的模型标识含敏感内容或不合法，已丢弃）" if metadata_dropped else ""
            )
            model.resolved_name = resolved_name
            model.probe_config_fingerprint = current_fp
            if outcome is ProbeOutcome.SUCCESS:
                model.supports_json_output = bool(json_verified)
            elif call_count:
                # The model answered (or tried to) under this configuration: it
                # did not verify JSON output.
                model.supports_json_output = False

        if call_count:
            self._usage.add(
                session,
                source=UsageSource.PROBE.value,
                model_id=model.id,
                provider_id=provider.id,
                probe_id=row.id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        session.flush()

        detail_bits = [
            f"provider={provider.name}",
            f"model={model.logical_name}",
            f"outcome={outcome.value}",
            f"calls={call_count}",
        ]
        if error_code:
            detail_bits.append(f"error={error_code.value}")
        if apply_capability:
            detail_bits.append(
                f"usage_known={prompt_tokens is not None and completion_tokens is not None}"
            )
            detail_bits.append(f"capability_current={evidence_is_current}")
        if metadata_dropped:
            detail_bits.append("metadata_dropped=True")

        self._audit.add(
            session,
            action=(
                AuditAction.MODEL_PROBE_COMPLETED.value
                if outcome is ProbeOutcome.SUCCESS
                else AuditAction.MODEL_PROBE_FAILED.value
            ),
            outcome="SUCCESS" if outcome is ProbeOutcome.SUCCESS else "FAILURE",
            actor_type=AuditActorType.OWNER.value,
            actor_user_id=actor_user_id,
            target_type="MODEL",
            target_id=str(model.id),
            request_id=request_id,
            ip_address=ip_address,
            detail=";".join(detail_bits),
        )
        return ProbeOutcomeRecord(
            probe=row,
            message=message,
            usage_known=prompt_tokens is not None and completion_tokens is not None,
        )


# Errors raised before any network call: their occurrence must not be counted as
# a billable call, otherwise the usage ledger would over-report spend.
_LOCAL_ONLY_CODES = frozenset(
    {
        ProbeErrorCode.MISSING_SECRET,
        ProbeErrorCode.INVALID_CONFIG,
        ProbeErrorCode.PROVIDER_DISABLED,
        ProbeErrorCode.MODEL_DISABLED,
    }
)


def _failure_message(code: ProbeErrorCode) -> str:
    return {
        ProbeErrorCode.MISSING_SECRET: "服务端未配置该供应商的密钥引用，未发起请求。",
        ProbeErrorCode.INVALID_CONFIG: "供应商配置无效，未发起请求。",
        ProbeErrorCode.AUTH_FAILED: "供应商拒绝了密钥（认证失败）。",
        ProbeErrorCode.RATE_LIMITED: "供应商限流，请稍后重试。",
        ProbeErrorCode.TIMEOUT: "请求超时，未获得响应。",
        ProbeErrorCode.NETWORK_ERROR: "无法连接供应商地址。",
        ProbeErrorCode.HTTP_ERROR: "供应商返回了错误状态。",
        ProbeErrorCode.INVALID_RESPONSE: "供应商响应格式不符合预期。",
        ProbeErrorCode.INVALID_JSON_OUTPUT: "模型输出不是有效 JSON，JSON 能力未通过验证。",
        ProbeErrorCode.PROVIDER_DISABLED: "供应商已停用，未发起请求。",
        ProbeErrorCode.MODEL_DISABLED: "模型已停用，未发起请求。",
    }[code]
