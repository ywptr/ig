"""Tenant-aware local discovery; never constructs adapters or decrypts credentials."""
from typing import get_args
from sqlalchemy.orm import Session
from app.media.capability_schemas import InterfaceSelection, MediaCapabilities, MediaCapability, ModelCapability, ProviderCapability
from ig_core.media.constraints import EXPLICIT_SELECTION_LEVELS
from app.media.providers.registry import MEDIA_PROVIDERS
from ig_core.media.schemas import InterfaceLevel, ImagePurpose
from app.media.routing import normal_image_selection
from app.media.model_governance import model_policy, model_is_current, enabled_model_ids, ModelGovernanceError
from app.tenancy.provider_configs import list_provider_configs


def media_capabilities(db: Session, *, tenant_id: str, configurations=None) -> MediaCapabilities:
    if configurations is None:
        configurations = {row.provider: row for row in list_provider_configs(db, tenant_id=tenant_id)}
    media_types = []
    for media_type, registrations in MEDIA_PROVIDERS.items():
        providers = []
        for provider_id, adapter in registrations.items():
            row = configurations.get(provider_id)
            config = row.config_json if row is not None else {}
            valid_config = isinstance(config, dict) and adapter.configuration_is_valid(config)
            configuration_error = None
            policy = None
            try:
                allowed = enabled_model_ids(adapter, config, media_type)
            except ModelGovernanceError:
                allowed = []
            try:
                policy = model_policy(adapter, config, media_type)
            except (ModelGovernanceError, TypeError, AttributeError):
                configuration_error = "Invalid tenant model governance; update provider settings"
            configured = bool(row is not None and row.encrypted_credentials and valid_config and not configuration_error)
            enabled = bool(row is not None and row.enabled)
            metadata = adapter.CAPABILITIES
            models = []
            for model in metadata.models:
                model_enabled = model.id in allowed
                available = bool(configured and enabled and model_enabled and model_is_current(model))
                models.append(ModelCapability(
                    id=model.id, display_name=model.display_name, enabled=model_enabled, lifecycle=model.lifecycle,
                    supported=model_is_current(model) and model.technical is not None,
                    available=available, usable=available and model.technical is not None, technical=model.technical,
                    supported_interface_levels=list(model.supported_interface_levels),
                    selectable_in=[level for level in EXPLICIT_SELECTION_LEVELS if level in model.supported_interface_levels]
                        if available and model.technical else [],
                    speech=model.speech, capabilities=model.capabilities.model_copy(deep=True), use_cases=list(model.use_cases)))
            default_model = policy.default if policy else None
            providers.append(ProviderCapability(
                id=provider_id, display_name=metadata.display_name, configured=configured, enabled=enabled,
                available=any(model.usable for model in models), configuration_error=configuration_error,
                default_model=default_model, default_model_available=any(model.usable and model.id == default_model for model in models),
                models=models))
        if media_type == "image":
            try:
                default_provider = normal_image_selection(configurations).provider
            except ValueError:
                default_provider = None
        else:
            default_provider = next((provider.id for provider in providers if provider.default_model_available), None)
        media_types.append(MediaCapability(
            id=media_type, available=any(provider.available for provider in providers), default_provider=default_provider,
            providers=providers, purposes=list(get_args(ImagePurpose)) if media_type == "image" else []))
    return MediaCapabilities(interface_levels={level: InterfaceSelection(
        explicit_provider_selection=level in EXPLICIT_SELECTION_LEVELS,
        explicit_model_selection=level in EXPLICIT_SELECTION_LEVELS) for level in get_args(InterfaceLevel)}, media_types=media_types)
