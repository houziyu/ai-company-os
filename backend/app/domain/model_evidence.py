"""One validity rule for model capabilities at every read boundary."""

from app.models.organization import Model, Provider


def evidence_is_current(model: Model, provider: Provider | None) -> bool:
    return bool(
        provider is not None
        and provider.config_fingerprint
        and model.probe_config_fingerprint
        and model.probe_config_fingerprint == provider.config_fingerprint
    )


def json_capability(model: Model, provider: Provider | None) -> bool | None:
    return model.supports_json_output if evidence_is_current(model, provider) else None


def evidence_is_stale(model: Model, provider: Provider | None) -> bool:
    # A newly created model has no evidence to invalidate.
    has_evidence = any(
        value is not None
        for value in (
            model.probe_config_fingerprint,
            model.supports_json_output,
            model.resolved_name,
            model.last_probe_outcome,
            model.last_probe_at,
        )
    )
    return has_evidence and not evidence_is_current(model, provider)
