"""Pure tenant policy over adapter-owned catalogs; no credentials or provider I/O."""
from dataclasses import dataclass
from app.core.time import utc_now


class ModelGovernanceError(ValueError):
    pass


def registered_model(adapter, model):
    metadata = next((item for item in adapter.CAPABILITIES.models if item.id == model), None)
    if metadata is None:
        raise ModelGovernanceError("Model is not registered for this provider")
    return metadata


def model_is_current(metadata):
    return metadata.lifecycle.sunset_at is None or utc_now().date() < metadata.lifecycle.sunset_at


def _mapping(config, key):
    if not isinstance(config, dict):
        raise ModelGovernanceError("Provider configuration must be an object")
    value = config.get(key, {})
    if not isinstance(value, dict) or set(value) - {"image", "video", "audio"}:
        raise ModelGovernanceError("Invalid model governance structure")
    return value


def enabled_model_ids(adapter, config, media):
    restrictions = _mapping(config, "enabled_models")
    supported = [item.id for item in adapter.CAPABILITIES.models]
    if media not in restrictions:
        return supported
    values = restrictions[media]
    if (not isinstance(values, list) or any(not isinstance(item, str) or item not in supported for item in values)
        or len(set(values)) != len(values)):
        raise ModelGovernanceError("Enabled models must be unique registered model IDs")
    return values


def validate_selected_model(adapter, config, media, model):
    """For pinned work, validate eligibility without resolving current defaults."""
    metadata = registered_model(adapter, model)
    if not model_is_current(metadata):
        raise ModelGovernanceError("Selected model is sunset and no longer supported")
    if model not in enabled_model_ids(adapter, config, media):
        raise ModelGovernanceError("Selected model is disabled for this tenant")
    return metadata


@dataclass(frozen=True)
class ModelPolicy:
    enabled: tuple[str, ...]
    default: str | None
    source: str


def model_policy(adapter, config, media):
    defaults = _mapping(config, "default_models")
    allowed = enabled_model_ids(adapter, config, media)
    legacy = config.get("default_model") if media == "image" else None
    configured = defaults[media] if media in defaults else legacy
    if configured is not None and not isinstance(configured, str):
        raise ModelGovernanceError("Default model must be a registered model ID")
    # Empty legacy values historically meant adapter default. Explicit media defaults must be valid.
    if media in defaults and not configured:
        raise ModelGovernanceError("Default model must be a registered model ID")
    if configured:
        validate_selected_model(adapter, config, media, configured)
        return ModelPolicy(tuple(allowed), configured, "tenant_default")
    eligible = [item.id for item in adapter.CAPABILITIES.models if item.id in allowed and model_is_current(item)]
    preferred = getattr(adapter, "DEFAULT_MODEL", None)
    selected = preferred if preferred in eligible else next(iter(eligible), None)
    return ModelPolicy(tuple(allowed), selected, "adapter_default" if selected == preferred else "catalog_order")


def resolve_model(adapter, config, media, explicit=None):
    policy = model_policy(adapter, config, media)
    selected = policy.default if explicit is None or explicit == "" else explicit
    if selected is None:
        raise ModelGovernanceError("No enabled supported model for this provider")
    validate_selected_model(adapter, config, media, selected)
    return selected


def provider_adapters(provider):
    from app.media.providers.registry import IMAGE_PROVIDERS, VIDEO_PROVIDERS
    return {media: registry[provider] for media, registry in (("image", IMAGE_PROVIDERS), ("video", VIDEO_PROVIDERS)) if provider in registry}


def validate_provider_model_config(provider, config):
    """Validate governance on writes, while preserving unrelated provider config."""
    if not isinstance(config, dict):
        raise ModelGovernanceError("Provider configuration must be an object")
    adapters = provider_adapters(provider)
    if not adapters:
        if any(key in config for key in ("default_models", "enabled_models", "default_model")):
            raise ModelGovernanceError("Model governance requires a registered provider")
        return
    for key in ("default_models", "enabled_models"):
        if set(_mapping(config, key)) - set(adapters):
            raise ModelGovernanceError("Model governance media type is not supported by this provider")
    for media, adapter in adapters.items():
        model_policy(adapter, config, media)
