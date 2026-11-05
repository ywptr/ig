"""Tenant-aware local discovery; never constructs adapters or decrypts credentials."""
import re
from typing import get_args

from sqlalchemy.orm import Session

from app.media.capability_schemas import (
    InterfaceSelection, MediaCapabilities, MediaCapability, ModelCapability, ProviderCapability,
)
from app.media.constraints import EXPLICIT_SELECTION_LEVELS
from app.media.providers.registry import IMAGE_PROVIDERS, VIDEO_PROVIDERS
from app.media.schemas import InterfaceLevel, ImagePurpose
from app.media.routing import normal_image_selection
from app.tenancy.provider_configs import list_provider_configs


def media_capabilities(db: Session, *, tenant_id: str) -> MediaCapabilities:
    configurations = {row.provider: row for row in list_provider_configs(db, tenant_id=tenant_id)}
    media_types = []
    for media_type, registrations in (("image", IMAGE_PROVIDERS), ("video", VIDEO_PROVIDERS)):
        providers = []
        for provider_id, adapter in registrations.items():
            row = configurations.get(provider_id)
            config = row.config_json if row is not None else {}
            valid_config = isinstance(config, dict) and adapter.configuration_is_valid(config)
            configured = bool(row is not None and row.encrypted_credentials and valid_config)
            enabled = bool(row is not None and row.enabled)
            available = configured and enabled
            try:
                default_model = adapter.default_model_for(config if isinstance(config, dict) else {})
            except (ValueError, TypeError, AttributeError):
                default_model = None
            # Only a model identifier may escape configuration, never arbitrary JSON or URLs.
            if not isinstance(default_model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", default_model):
                default_model = None
            metadata = adapter.CAPABILITIES
            models = [ModelCapability(
                id=model.id, display_name=model.display_name, available=available,
                supported_interface_levels=list(model.supported_interface_levels),
                selectable_in=[level for level in EXPLICIT_SELECTION_LEVELS
                               if level in model.supported_interface_levels] if available else [],
                capabilities=model.capabilities.model_copy(deep=True), use_cases=list(model.use_cases),
            ) for model in metadata.models]
            providers.append(ProviderCapability(
                id=provider_id, display_name=metadata.display_name, configured=configured,
                enabled=enabled, available=available, default_model=default_model,
                default_model_available=any(model.available and model.id == default_model for model in models),
                models=models,
            ))
        if media_type == "image":
            try:
                default_provider = normal_image_selection(configurations).provider
            except ValueError:
                default_provider = None
        else:
            # Preserve the existing video rule, including enabled-but-incomplete
            # rows participating in automatic-provider ambiguity.
            candidates = [provider_id for provider_id in registrations
                          if provider_id in configurations and configurations[provider_id].enabled]
            default_provider = next((provider.id for provider in providers
                if len(candidates) == 1 and provider.id == candidates[0]
                and provider.default_model_available), None)
        media_types.append(MediaCapability(
            id=media_type, available=any(provider.available for provider in providers),
            default_provider=default_provider, providers=providers,
            purposes=list(get_args(ImagePurpose)) if media_type == "image" else [],
        ))
    return MediaCapabilities(
        interface_levels={level: InterfaceSelection(
            explicit_provider_selection=level in EXPLICIT_SELECTION_LEVELS,
            explicit_model_selection=level in EXPLICIT_SELECTION_LEVELS,
        ) for level in get_args(InterfaceLevel)},
        media_types=media_types,
    )
