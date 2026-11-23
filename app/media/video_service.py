"""Tenant-scoped video selection, generic validation and reference resolution."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.artifacts.storage.provider import artifact_store
from app.media.model_governance import ModelGovernanceError, resolve_model, validate_selected_model
from ig_core.media.validation import MediaRequestValidationError
from app.media.providers.registry import VIDEO_PROVIDERS, create_video_provider, video_provider_registration
from ig_core.providers.video import ResolvedVideoReference, VideoProviderConfigurationError
from ig_core.media.schemas import MediaGenerationRequest
from app.tenancy.provider_configs import get_provider_config, get_provider_credentials, list_provider_configs


@dataclass(frozen=True)
class VideoSelection:
    provider: str
    model: str


def load_video_provider(db: Session, *, tenant_id: str, provider: str, model: str):
    config = get_provider_config(db, tenant_id=tenant_id, provider=provider)
    if not config or not config.enabled:
        raise VideoProviderConfigurationError("Video provider is not enabled for this tenant")
    validate_selected_model(video_provider_registration(provider), config.config_json or {}, "video", model)
    credentials = get_provider_credentials(db, tenant_id=tenant_id, provider=provider)
    return create_video_provider(provider, credentials=credentials, config=config.config_json or {}, model=model)


def resolve_video_selection(db: Session, request: MediaGenerationRequest, *, tenant_id: str, user_id: str | None = None) -> VideoSelection:
    advanced = request.advanced
    provider = advanced.provider.strip().lower() if advanced and advanced.provider else None
    explicit_model = advanced.model if advanced else None
    if provider:
        video_provider_registration(provider)
    configurations = {row.provider: row for row in list_provider_configs(db, tenant_id=tenant_id)}
    errors = []
    for name, adapter in VIDEO_PROVIDERS.items():
        if provider and provider != name:
            continue
        row = configurations.get(name)
        if not row or not row.enabled or not row.encrypted_credentials:
            continue
        config = row.config_json or {}
        if not adapter.configuration_is_valid(config):
            continue
        # Avoid reporting another provider's unknown-model error when model-only selection is used.
        if explicit_model and not provider and explicit_model not in adapter.MODELS:
            continue
        try:
            model = resolve_model(adapter, config, "video", explicit_model)
            validate_video_capabilities(request, name, model)
            if user_id and request.references:
                resolve_video_references(db, request, tenant_id=tenant_id, user_id=user_id, read_bytes=False,
                                         provider=name, model=model)
        except (MediaRequestValidationError, ModelGovernanceError, VideoProviderConfigurationError) as exc:
            errors.append(exc)
            continue
        load_video_provider(db, tenant_id=tenant_id, provider=name, model=model)
        return VideoSelection(name, model)
    if errors:
        raise errors[0]
    raise VideoProviderConfigurationError("No enabled video provider/model is available for the request")


def resolved_video_request(request, selection):
    """Pin model defaults before serializing the Job request."""
    if "audio" in request.output.model_fields_set:
        return request
    metadata = video_metadata(selection.provider, selection.model)
    if metadata.capabilities.native_audio_required:
        return request.model_copy(update={"output": request.output.model_copy(update={"audio": True})})
    return request


def resolve_video_references(
    db: Session, request: MediaGenerationRequest, *, tenant_id: str, user_id: str, read_bytes: bool,
    provider: str | None = None, model: str | None = None,
) -> list[ResolvedVideoReference]:
    from app.artifacts.references import reference_slots, resolve_references
    # Selection is persisted in the Job and resolved independently of actor input.
    if provider is None or model is None:
        selection = resolve_video_selection(db, request, tenant_id=tenant_id)
        provider, model = selection.provider, selection.model
    rows = resolve_references(db, request.references,
        slots=reference_slots(provider, model, "video"),
        tenant_id=tenant_id, user_id=user_id, store=artifact_store, read_bytes=read_bytes)
    return [ResolvedVideoReference(row.artifact_id, row.mime_type, data, values["role"]) for row, values, data in rows] if read_bytes else []


def video_metadata(provider, model):
    metadata = next((item for item in video_provider_registration(provider).CAPABILITIES.models if item.id == model), None)
    if metadata is None:
        raise VideoProviderConfigurationError("Video model capabilities are unknown")
    return metadata


def validate_video_capabilities(request, provider, model):
    from ig_core.media.validation import validate_model_capabilities

    metadata = video_metadata(provider, model)
    validate_model_capabilities(request, metadata)
    if metadata.technical.execution.mode != "asynchronous" or not metadata.technical.execution.polling_required:
        raise VideoProviderConfigurationError("Video model requires unsupported execution behavior")
