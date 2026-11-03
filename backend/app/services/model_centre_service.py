"""Model centre: providers, models, assignments and connectivity probes.

Separation the phase plan requires ("保存配置与联网探测分开"):

* :meth:`ModelCentreService.create_provider` / :meth:`update_provider` /
  :meth:`create_model` / :meth:`update_model` only persist configuration. They
  never call a provider.
* :meth:`ModelCentreService.probe_model` performs exactly one provider call,
  records a probe row plus a usage row, and writes its own audit event. It does
  **not** save any configuration; a probe uses the already-persisted provider
  settings, so "test" can never silently apply an unconfirmed edit.

Secret handling (ADR-0006, superseding the Phase 02 env-name-only rule):

* A credential typed by the operator is AES-GCM encrypted immediately and stored
  in ``providers.secret_ciphertext``. The data key lives in
  ``SECRET_ENCRYPTION_KEY`` on the server, never in the database.
* Rows created under the Phase 02 rule keep ``secret_source = 'ENV'`` and their
  ``secret_ref`` variable name; they still resolve from the process environment.
* No endpoint, log line, audit detail or response contains a credential, its
  length, a prefix/suffix or the ciphertext. ``secret_state`` reports one of
  ``NONE / CONFIGURED / MISSING_ENV / UNDECRYPTABLE``; decryption happens once,
  immediately before a paid call.
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
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import (
    AuditAction,
    AuditActorType,
    CompanyStatus,
    ProbeErrorCode,
    ProbeOutcome,
    ProviderKind,
    ProviderStatus,
    ReservationKind,
    SecretSource,
    SecretState,
    UsageSource,
)
from app.domain.errors import (
    BudgetInsufficientError,
    CompanyPausedError,
    ConcurrentUpdateError,
    FieldError,
    ModelNotFoundError,
    ProviderNotFoundError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.models.organization import Company, Model, ModelProbe, Provider
from app.models.tasks import BudgetReservation
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
from app.security.secrets_cipher import (
    MAX_PLAINTEXT_CHARS,
    EncryptionNotConfiguredError,
    SecretCipher,
)
from app.services.budget_service import BudgetService

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
# Environment variable *name* offered as the suggested source; never a value.
DEEPSEEK_SECRET_REF = "DEEPSEEK_API_KEY"  # noqa: S105
DEEPSEEK_MODEL = "deepseek-v4.1-flash"
# A literal key may be pasted with surrounding whitespace or a newline; those are
# trimmed. Anything else control-ish is rejected instead of being sent to a
# provider and stored, where it would surface only as a confusing auth failure.
_CREDENTIAL_MAX = MAX_PLAINTEXT_CHARS


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
    # Validated literal credential awaiting encryption, or None to leave the
    # stored credential untouched. Never stored as given.
    secret_value: str | None = None


@dataclass(slots=True)
class ProviderView:
    provider: Provider
    models: Sequence[Model]
    secret_configured: bool
    secret_state: SecretState


@dataclass(slots=True)
class ProbeOutcomeRecord:
    probe: ModelProbe
    message: str
    usage_known: bool


def _clean(value: str) -> str:
    return value.strip()


def marker_for(ciphertext: str | None, secret_ref: str | None) -> str:
    """The credential component of a configuration fingerprint.

    Shared by the persistence paths, which know the stored columns directly, and
    :func:`credential_marker`, which derives the same value from a provider row.
    """
    if ciphertext:
        digest = hashlib.sha256(ciphertext.encode("utf-8")).hexdigest()
        return f"db:{digest[:16]}"
    return secret_ref or ""


def credential_marker(provider: Provider) -> str:
    """Return the non-secret fingerprint component describing the credential.

    * ``ENV`` rows: the variable name (byte-identical to the Phase 02 behaviour,
      so stored fingerprints stay valid across the upgrade).
    * ``DB`` rows: ``db:<hash of the ciphertext>``. Ciphertext changes whenever
      the key is replaced, so probe evidence is invalidated exactly when the
      credential changes. Hashing the ciphertext — not the plaintext — keeps the
      marker from becoming a cheap offline oracle for a candidate key.
    * No credential: the empty string.
    """
    return marker_for(provider.secret_ciphertext, provider.secret_ref)


def config_fingerprint(
    *, kind: str, base_url: str, secret_ref: str | None, timeout_seconds: float
) -> str:
    """Hash exactly the fields that determine where and how a call is made.

    Probe evidence is only meaningful for the configuration that produced it. The
    fingerprint deliberately excludes the display name, notes and status: renaming
    a provider does not change what a probe measured, but changing the endpoint,
    the credential, the protocol kind or the timeout does (review findings R3 and
    R7).

    ``secret_ref`` is the credential *marker* from :func:`credential_marker`, not
    a value. The payload key keeps its Phase 02 name so every fingerprint already
    in the database still reproduces — ADR-0006 changed what is stored, not the
    hash inputs of existing rows.
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
        default_company_budget: int = 1_000_000,
        probe_reservation_tokens: int = 512,
        cipher: SecretCipher | None = None,
    ) -> None:
        self._providers = ProviderRepository()
        self._models = ModelRepository()
        self._probes = ProbeRepository()
        self._usage = UsageRepository()
        self._audit = AuditRepository()
        # Phase 03: a probe is a paid call, so it draws on the same company
        # allowance as task execution. The hold is committed with the PENDING
        # intent, before the network call, and settled afterwards.
        self._budget = BudgetService(default_company_limit=default_company_budget)
        self._probe_reservation_tokens = probe_reservation_tokens
        # Tests inject an httpx.MockTransport here. Production passes nothing,
        # so the real network path is the only one that exists in a deployment.
        self._transport = transport
        # A separate transaction boundary is required for the pre-call intent
        # commit: the request transaction must not be the only place the fact of
        # a paid call exists (review finding R2).
        self._session_factory = session_factory
        # ADR-0006 credential encryption. A cipher with no data key refuses to
        # encrypt; it never falls back to storing plaintext.
        self._cipher = cipher if cipher is not None else SecretCipher()

    # ------------------------------------------------------------- queries
    def secret_configured(self, provider: Provider | str | None) -> bool:
        """Whether a usable credential is available for this provider.

        Accepts a provider row or a bare environment-variable name (the Phase 02
        call sites pass the name). "Usable" means a stored key this server can
        decrypt, or an environment variable that is actually set.
        """
        if isinstance(provider, Provider):
            return self.secret_state(provider) is SecretState.CONFIGURED
        return read_secret(provider) is not None

    def secret_state(self, provider: Provider) -> SecretState:
        """Classify the provider's credential without exposing anything.

        Cheap by design: a stored blob is only checked for format and key id, so
        listing providers never performs AES work. The first real decryption
        happens in :meth:`probe_model`, immediately before the paid call.
        """
        if provider.secret_ciphertext:
            return (
                SecretState.CONFIGURED
                if self._cipher.can_decrypt(provider.secret_ciphertext)
                else SecretState.UNDECRYPTABLE
            )
        if provider.secret_ref:
            return (
                SecretState.CONFIGURED
                if read_secret(provider.secret_ref) is not None
                else SecretState.MISSING_ENV
            )
        return SecretState.NONE

    def list_providers(self, session: Session) -> list[ProviderView]:
        providers = self._providers.list_all(session)
        return [
            ProviderView(
                provider=provider,
                models=self._models.list_for_provider(session, provider.id),
                secret_configured=self.secret_configured(provider),
                secret_state=self.secret_state(provider),
            )
            for provider in providers
        ]

    def get_provider_view(self, session: Session, provider_id: int) -> ProviderView:
        provider = self._providers.get(session, provider_id)
        if provider is None:
            raise ProviderNotFoundError()
        state = self.secret_state(provider)
        return ProviderView(
            provider=provider,
            models=self._models.list_for_provider(session, provider.id),
            secret_configured=state is SecretState.CONFIGURED,
            secret_state=state,
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
    def _encrypt_credential(self, value: str) -> str:
        """Encrypt a typed credential, refusing when no data key is configured.

        A deployment without ``SECRET_ENCRYPTION_KEY`` cannot store a key. It is
        refused with a 503 that names the setting, because the only alternative
        would be writing plaintext — exactly what ADR-0006 exists to prevent.
        """
        try:
            return self._cipher.encrypt(value)
        except EncryptionNotConfiguredError as exc:
            raise ServiceUnavailableError(
                "服务端未配置 SECRET_ENCRYPTION_KEY，无法保存供应商密钥。"
                "请先在部署环境中生成并配置该值（32 字节随机值的 base64）。",
                details={"setting": "SECRET_ENCRYPTION_KEY"},
            ) from exc

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
        secret_value: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> Provider:
        cleaned = self._validate_provider(
            name=name,
            kind=kind,
            base_url=base_url,
            secret_ref=secret_ref,
            secret_value=secret_value,
            status=status,
            notes=notes,
            timeout_seconds=timeout_seconds,
        )
        if self._providers.get_by_name(session, cleaned.name) is not None:
            raise ValidationFailedError([FieldError("name", "该供应商名称已存在。")])

        # A typed key is encrypted before anything is persisted; a legacy
        # environment reference is stored as a name, exactly as in Phase 02.
        ciphertext = (
            self._encrypt_credential(cleaned.secret_value)
            if cleaned.secret_value is not None
            else None
        )
        source = SecretSource.DB if ciphertext is not None else SecretSource.ENV
        stored_ref = None if ciphertext is not None else cleaned.secret_ref

        provider = self._providers.create(
            session,
            name=cleaned.name,
            kind=cleaned.kind,
            base_url=cleaned.base_url,
            secret_ref=stored_ref,
            secret_ciphertext=ciphertext,
            secret_source=source.value,
            status=cleaned.status,
            notes=cleaned.notes,
            timeout_seconds=cleaned.timeout_seconds,
            config_fingerprint=config_fingerprint(
                kind=cleaned.kind,
                base_url=cleaned.base_url,
                secret_ref=marker_for(ciphertext, stored_ref),
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
            # Source and presence only: never the key, its length or a prefix.
            detail=(
                f"name={provider.name};kind={provider.kind};"
                f"secret_source={provider.secret_source};"
                f"secret_configured={self.secret_configured(provider)}"
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
        secret_value: str | None = None,
        change_secret_value: bool = False,
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

        Credentials follow the same "omitted vs. cleared" rule, twice over:

        * ``change_secret_value=True`` with a value replaces the stored key
          (and drops any legacy environment reference).
        * ``change_secret_value=True`` with ``secret_value=None``/blank clears
          the stored key. Nothing falls back to an environment variable.
        * ``change_secret_value=False`` leaves the credential untouched, so an
          edit form that never shows the key cannot erase it.
        """
        provider = self._providers.get(session, provider_id, for_update=True)
        if provider is None:
            raise ProviderNotFoundError()
        if provider.version != expected_version:
            raise ConcurrentUpdateError("provider")

        # A legacy client may still submit an environment variable name. It is
        # only honoured when the request is explicitly changing the reference;
        # otherwise the stored credential stays as it is.
        resolved_secret_ref = provider.secret_ref if not change_secret_ref else secret_ref
        resolved_notes = notes if notes_provided else provider.notes
        cleaned = self._validate_provider(
            name=name if name is not None else provider.name,
            kind=kind if kind is not None else provider.kind,
            base_url=base_url if base_url is not None else provider.base_url,
            secret_ref=resolved_secret_ref,
            secret_value=secret_value if change_secret_value else None,
            status=status if status is not None else provider.status,
            notes=resolved_notes,
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else provider.timeout_seconds
            ),
        )

        existing = self._providers.get_by_name(session, cleaned.name)
        if existing is not None and existing.id != provider.id:
            raise ValidationFailedError([FieldError("name", "该供应商名称已存在。")])

        # Credential transition, decided before touching the row so an encryption
        # failure leaves the stored key unchanged.
        new_ciphertext = provider.secret_ciphertext
        new_ref = provider.secret_ref
        new_source = provider.secret_source
        credential_changed = False
        if change_secret_value:
            credential_changed = True
            if cleaned.secret_value is not None:
                new_ciphertext = self._encrypt_credential(cleaned.secret_value)
                new_ref = None
                new_source = SecretSource.DB.value
            else:
                new_ciphertext = None
                new_ref = None
                new_source = SecretSource.ENV.value
        elif change_secret_ref:
            credential_changed = True
            new_ciphertext = None
            new_ref = cleaned.secret_ref
            new_source = SecretSource.ENV.value

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
        if credential_changed:
            provider.secret_ref = new_ref
            provider.secret_ciphertext = new_ciphertext
            provider.secret_source = new_source
            # The audit records that it changed, never what it changed to.
            changed.append("credential")
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
            secret_ref=credential_marker(provider),
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
                f"secret_source={provider.secret_source};"
                f"secret_configured={self.secret_configured(provider)};"
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
        secret_value: str | None,
        status: str,
        notes: str | None,
        timeout_seconds: float | None,
    ) -> ProviderInput:
        cleaned_name = _clean(name)
        cleaned_url = _clean(base_url).rstrip("/")
        cleaned_ref = _clean(secret_ref) if secret_ref else None
        cleaned_value = _clean(secret_value) if secret_value else None
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
        if cleaned_ref and cleaned_value is not None:
            field_errors.append(
                FieldError("secretValue", "只能选择一种密钥来源：直接填写密钥，或引用环境变量名。")
            )
        elif cleaned_ref and not ENV_NAME_PATTERN.match(cleaned_ref):
            # Legacy path: a Phase 02 client may still send an environment
            # variable name. It stays strictly validated so a pasted key can
            # never be mistaken for one.
            field_errors.append(
                FieldError(
                    "secretRef",
                    "密钥引用必须是服务端环境变量名（大写字母、数字、下划线，以字母开头）。",
                )
            )
        elif cleaned_ref and len(cleaned_ref) > 128:
            field_errors.append(FieldError("secretRef", "密钥引用名称过长。"))
        if cleaned_value is not None:
            if len(cleaned_value) > _CREDENTIAL_MAX:
                field_errors.append(
                    FieldError("secretValue", f"密钥不能超过 {_CREDENTIAL_MAX} 个字符。")
                )
            elif any(ord(char) < 0x20 or ord(char) == 0x7F for char in cleaned_value):
                field_errors.append(
                    FieldError("secretValue", "密钥包含换行或控制字符，请检查后重新粘贴。")
                )
        if status not in {item.value for item in ProviderStatus}:
            field_errors.append(FieldError("status", "未知的供应商状态。"))
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
            secret_ref=cleaned_ref,
            status=status,
            notes=cleaned_notes,
            timeout_seconds=timeout,
            secret_value=cleaned_value,
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
            secret_ref=credential_marker(provider),
            timeout_seconds=float(provider.timeout_seconds),
        )

        # Resolve the credential once, before any spend. A stored key this
        # server cannot decrypt is a configuration problem that no request can
        # fix, so the probe is refused locally (call_count 0, no usage row, no
        # budget settlement) with a message that says what to do.
        secret_value: str | None = None
        if provider.secret_ciphertext:
            secret_value = self._cipher.decrypt(provider.secret_ciphertext)
            if secret_value is None:
                return self._record_local_refusal(
                    session,
                    model=model,
                    provider=provider,
                    code=ProbeErrorCode.SECRET_UNDECRYPTABLE,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                    ip_address=ip_address,
                    config_fp=fingerprint,
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

        # Phase 03: a probe is a paid call, so the same company-wide gates apply.
        company = session.get(Company, 1)
        if company is not None and company.status == CompanyStatus.PAUSED.value:
            return self._record_local_refusal(
                session,
                model=model,
                provider=provider,
                code=ProbeErrorCode.COMPANY_PAUSED,
                actor_user_id=actor_user_id,
                request_id=request_id,
                ip_address=ip_address,
                config_fp=fingerprint,
            )

        # ---- phase 1: commit the intent and its budget hold before the call --
        # The hold is committed in the same short transaction as the PENDING
        # intent. If this process dies mid-call, both survive: the intent says
        # "usage unknown" and the hold stays *reserved* (never released), which is
        # the conservative reading of a call that may have been paid for.
        #
        # Before the company exists there is no allowance to draw on (Phase 02
        # allows configuring and testing models during setup). Such a probe is
        # still recorded — with its usage — but it books no budget hold, and the
        # response says so instead of implying the spend was budgeted.
        budgeted = company is not None
        try:
            probe_row = self._commit_probe_intent(
                session,
                model_id=model.id,
                provider_id=provider.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                config_fp=fingerprint,
                reserve_tokens=self._probe_reservation_tokens if budgeted else 0,
            )
        except (BudgetInsufficientError, CompanyPausedError) as exc:
            return self._record_local_refusal(
                session,
                model=model,
                provider=provider,
                code=(
                    ProbeErrorCode.COMPANY_PAUSED
                    if isinstance(exc, CompanyPausedError)
                    else ProbeErrorCode.BUDGET_INSUFFICIENT
                ),
                actor_user_id=actor_user_id,
                request_id=request_id,
                ip_address=ip_address,
                config_fp=fingerprint,
                message=exc.message,
            )

        request = ProbeRequest(
            base_url=provider.base_url,
            model_name=model.logical_name,
            kind=ProviderKind(provider.kind),
            # For a DB-stored key the literal value is authoritative; the adapter
            # never falls back to an environment variable for it.
            secret_ref=provider.secret_ref,
            secret_value=secret_value,
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
        reserve_tokens: int = 0,
    ) -> ModelProbe:
        """Write and COMMIT the PENDING intent (and its budget hold) on its own.

        Uses a dedicated session so committing does not also commit whatever else
        the request has pending. Without a configured factory (unit tests calling
        the service directly) the intent still exists in the caller's session, and
        the adapter-level call still happens exactly once.

        The hold is committed together with the intent: a crash after this point
        leaves the tokens reserved rather than silently available.
        """
        if self._session_factory is None:
            self._check_probe_company(session, reserve_tokens)
            row = self._probes.create_intent(
                session,
                model_id=model_id,
                provider_id=provider_id,
                initiated_by_user_id=actor_user_id,
                request_id=request_id,
                config_fingerprint=config_fp,
            )
            self._reserve_probe_budget(session, probe_id=row.id, amount=reserve_tokens)
            return row
        with self._session_factory() as intent_session:
            self._check_probe_company(intent_session, reserve_tokens)
            row = self._probes.create_intent(
                intent_session,
                model_id=model_id,
                provider_id=provider_id,
                initiated_by_user_id=actor_user_id,
                request_id=request_id,
                config_fingerprint=config_fp,
            )
            self._reserve_probe_budget(intent_session, probe_id=row.id, amount=reserve_tokens)
            intent_session.commit()
            probe_id = row.id
        # Re-read into the caller's session so phase 2 updates the committed row.
        committed = self._probes.get(session, probe_id)
        if committed is None:  # pragma: no cover - the row was just committed
            raise RuntimeError("probe intent row disappeared after commit")
        return committed

    def _check_probe_company(self, session: Session, reserve_tokens: int) -> None:
        # Authoritative stop gate: serialised with pause and held until the
        # intent and reservation commit. No network I/O happens under this lock.
        if reserve_tokens > 0:
            company = self._budget.lock_company(session)
            if company.status == CompanyStatus.PAUSED.value:
                raise CompanyPausedError()

    def _reserve_probe_budget(
        self, session: Session, *, probe_id: int, amount: int
    ) -> BudgetReservation | None:
        if amount <= 0:
            return None
        return self._budget.reserve_company_allowance(
            session,
            amount=amount,
            kind=ReservationKind.PROBE,
            owner_ref=probe_id,
            project_id=None,
            task_id=None,
            execution_id=None,
            probe_id=probe_id,
            detail=f"probe:{probe_id}",
        )

    def _settle_probe_budget(
        self,
        session: Session,
        *,
        probe_id: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        call_count: int,
    ) -> None:
        """Reconcile the probe's hold with what the provider reported.

        * no call at all → release any committed hold;
        * usage reported  → settle to the reported total;
        * usage unknown   → keep the full hold as consumed and flag it for manual
          reconciliation; releasing it would silently assume a free call.
        """
        reservation = session.scalars(
            select(BudgetReservation).where(
                BudgetReservation.probe_id == probe_id,
                BudgetReservation.kind == ReservationKind.PROBE.value,
                BudgetReservation.status == "RESERVED",
            )
        ).one_or_none()
        if reservation is None:
            return
        if call_count == 0:
            self._budget.release(
                session, reservation_id=reservation.id, detail=f"probe-local-refusal:{probe_id}"
            )
            return
        known = prompt_tokens is not None and completion_tokens is not None
        total = (prompt_tokens or 0) + (completion_tokens or 0) if known else None
        self._budget.settle(
            session,
            reservation_id=reservation.id,
            actual_tokens=total,
            known=known,
            detail=f"probe:{probe_id}",
        )

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
        message: str | None = None,
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
            message=message or _failure_message(code),
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
        self._settle_probe_budget(
            session,
            probe_id=row.id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            call_count=call_count,
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
        ProbeErrorCode.SECRET_UNDECRYPTABLE,
        ProbeErrorCode.INVALID_CONFIG,
        ProbeErrorCode.PROVIDER_DISABLED,
        ProbeErrorCode.MODEL_DISABLED,
        ProbeErrorCode.COMPANY_PAUSED,
        ProbeErrorCode.BUDGET_INSUFFICIENT,
    }
)


def _failure_message(code: ProbeErrorCode) -> str:
    return {
        ProbeErrorCode.MISSING_SECRET: "该供应商尚未配置密钥，未发起请求。",
        ProbeErrorCode.SECRET_UNDECRYPTABLE: (
            "已保存的密钥无法解密（服务端加密密钥已更换或缺失），请在模型中心重新填写密钥。"
        ),
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
        ProbeErrorCode.COMPANY_PAUSED: "公司已暂停，未发起请求。",
        ProbeErrorCode.BUDGET_INSUFFICIENT: "公司可用 Token 额度不足，未发起请求。",
    }[code]
