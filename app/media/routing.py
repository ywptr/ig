"""Local image selection from tenant state and generic model metadata.

No clients, credential decryption, provider I/O, or post-failure fallback.
Registration order is the deterministic tie-breaker; the legacy image provider
remains preferred for general requests whenever its configured default is usable.
"""
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.media.providers.base import ImageProviderConfigurationError
from app.media.providers.registry import DEFAULT_IMAGE_PROVIDER, IMAGE_PROVIDERS
from app.media.validation import validate_model_capabilities
from app.media.schemas import ImagePurpose, InterfaceLevel, MediaGenerationRequest
from app.tenancy.provider_configs import list_provider_configs


class MediaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_-]+$")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    purpose: ImagePurpose = "general"
    reason: str = Field(min_length=1, max_length=100)


def provider_is_available(adapter, row) -> bool:
    return bool(row is not None and row.enabled and row.encrypted_credentials
                and isinstance(row.config_json, dict) and adapter.configuration_is_valid(row.config_json))


def normal_image_selection(
    configurations: dict, *, purpose: ImagePurpose = "general", interface_level: InterfaceLevel = "easy",
) -> MediaSelection:
    ordered = sorted(IMAGE_PROVIDERS, key=lambda provider: provider != DEFAULT_IMAGE_PROVIDER)
    for provider in ordered:
        adapter = IMAGE_PROVIDERS[provider]
        row = configurations.get(provider)
        if not provider_is_available(adapter, row):
            continue
        try:
            model = adapter.default_model_for(row.config_json)
            metadata = next((item for item in adapter.CAPABILITIES.models if item.id == model), None)
            if metadata is not None and interface_level not in metadata.supported_interface_levels:
                continue
            source = "tenant_default" if adapter.tenant_default_model(row.config_json) else "adapter_default"
            return MediaSelection(provider=provider, model=model, purpose=purpose, reason=source)
        except (ValueError, TypeError, AttributeError):
            # An unusable configured default does not prevent another registered
            # provider from supplying the normal route. No generation has run.
            continue
    raise ImageProviderConfigurationError("No available image provider")


def _validate_options(adapter, request: MediaGenerationRequest, model: str, config: dict) -> None:
    metadata = next((item for item in adapter.CAPABILITIES.models if item.id == model), None)
    # Preserve legacy custom defaults, whose technical capabilities are unknown.
    if metadata is not None:
        validate_model_capabilities(request, metadata)
    adapter.validate_submission_options(
        config=config, model=model, count=request.output.count, strategy=request.strategy,
        reference_count=len(request.references),
        parameters=request.advanced.parameters if request.advanced else {},
    )


def _compatible(metadata, request: MediaGenerationRequest) -> bool:
    controls, output = metadata.capabilities, request.output
    return (
        request.interface_level in metadata.supported_interface_levels
        and (output.aspect_ratio is None or output.aspect_ratio in controls.aspect_ratios)
        and output.quality in controls.quality_profiles and output.style in controls.styles
        and controls.count.min <= output.count <= controls.count.max
        and len(request.references) <= controls.max_references
        and all(ref.role in controls.reference_roles and ref.strength in controls.reference_strengths
                for ref in request.references)
        and output.layout.type in controls.layout_types
        and (output.layout.type != "panel_grid" or output.layout.panel_count in controls.panel_counts)
    )


def resolve_image_selection(db: Session, request: MediaGenerationRequest, *, tenant_id: str) -> MediaSelection:
    if request.media_type != "image":
        raise ImageProviderConfigurationError("Image routing requires an image request")
    configurations = {row.provider: row for row in list_provider_configs(db, tenant_id=tenant_id)}
    purpose = request.output.purpose
    advanced = request.advanced
    provider = advanced.provider.strip().lower() if advanced and advanced.provider else None
    model = advanced.model if advanced else None
    if provider or model:
        if request.interface_level != "hard":
            raise ImageProviderConfigurationError("Explicit image selection requires hard mode")
        # Model-only hard selection uses the first available registration that
        # declares the model. A supplied provider is never replaced by routing.
        providers = [provider] if provider else list(IMAGE_PROVIDERS)
        for selected_provider in providers:
            adapter = IMAGE_PROVIDERS.get(selected_provider)
            row = configurations.get(selected_provider)
            if adapter is None or not provider_is_available(adapter, row):
                continue
            if model and not any(item.id == model and request.interface_level in item.supported_interface_levels
                                 for item in adapter.CAPABILITIES.models):
                continue
            try:
                selected_model = model or adapter.default_model_for(row.config_json)
                _validate_options(adapter, request, selected_model, row.config_json)
            except (ValueError, TypeError, AttributeError):
                raise ImageProviderConfigurationError("Invalid image selection or options") from None
            return MediaSelection(provider=selected_provider, model=selected_model, purpose=purpose, reason="explicit")
        raise ImageProviderConfigurationError("Explicit image provider/model unavailable")

    if purpose != "general":
        for selected_provider, adapter in IMAGE_PROVIDERS.items():
            row = configurations.get(selected_provider)
            if not provider_is_available(adapter, row):
                continue
            for metadata in adapter.CAPABILITIES.models:
                if purpose not in metadata.use_cases or not _compatible(metadata, request):
                    continue
                try:
                    _validate_options(adapter, request, metadata.id, row.config_json)
                except (ValueError, TypeError, AttributeError):
                    continue
                return MediaSelection(provider=selected_provider, model=metadata.id, purpose=purpose,
                                      reason=f"purpose:{purpose}")

    selection = normal_image_selection(configurations, purpose=purpose, interface_level=request.interface_level)
    adapter = IMAGE_PROVIDERS[selection.provider]
    try:
        _validate_options(adapter, request, selection.model, configurations[selection.provider].config_json)
    except (ValueError, TypeError, AttributeError):
        raise ImageProviderConfigurationError("Invalid image selection or options") from None
    if purpose != "general":
        selection = selection.model_copy(update={"reason": f"purpose:{purpose}:fallback:{selection.reason}"})
    return selection
