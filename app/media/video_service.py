"""Tenant-scoped video selection, generic validation and reference resolution."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.artifacts.storage.provider import artifact_store
from app.media.providers.registry import VIDEO_PROVIDERS, create_video_provider, video_provider_registration
from app.media.providers.video import ResolvedVideoReference, VideoProviderConfigurationError
from app.media.schemas import MediaGenerationRequest
from app.tenancy.provider_configs import get_provider_config, get_provider_credentials, list_provider_configs


@dataclass(frozen=True)
class VideoSelection:
    provider: str
    model: str


def load_video_provider(db: Session, *, tenant_id: str, provider: str, model: str):
    config = get_provider_config(db, tenant_id=tenant_id, provider=provider)
    if not config or not config.enabled:
        raise VideoProviderConfigurationError("Video provider is not enabled for this tenant")
    credentials = get_provider_credentials(db, tenant_id=tenant_id, provider=provider)
    return create_video_provider(provider, credentials=credentials, config=config.config_json or {}, model=model)


def resolve_video_selection(db: Session, request: MediaGenerationRequest, *, tenant_id: str) -> VideoSelection:
    advanced = request.advanced
    provider = (advanced.provider if advanced else None)
    if provider:
        provider = provider.strip().lower()
        video_provider_registration(provider)
    else:
        candidates = [c.provider for c in list_provider_configs(db, tenant_id=tenant_id)
                      if c.enabled and c.provider in VIDEO_PROVIDERS]
        if len(candidates) != 1:
            raise VideoProviderConfigurationError("Configure exactly one default video provider or select one in hard mode")
        provider = candidates[0]
    config = get_provider_config(db, tenant_id=tenant_id, provider=provider)
    if not config or not config.enabled:
        raise VideoProviderConfigurationError("Video provider is not enabled for this tenant")
    model = video_provider_registration(provider).resolve_tenant_model(
        config.config_json or {}, advanced.model if advanced else None,
    )
    load_video_provider(db, tenant_id=tenant_id, provider=provider, model=model)
    return VideoSelection(provider, model)


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
    return [ResolvedVideoReference(row.artifact_id, row.mime_type, data) for row, _, data in rows] if read_bytes else []
