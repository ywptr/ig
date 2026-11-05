"""Tenant-scoped video selection, generic validation and reference resolution."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.storage.provider import artifact_store
from app.db.models import Artifact
from app.media.providers.registry import VIDEO_PROVIDERS, create_video_provider, video_provider_registration
from app.media.providers.video import ResolvedVideoReference, VideoProviderConfigurationError
from app.media.schemas import MediaGenerationRequest
from app.media.validation import MediaRequestValidationError
from app.tenancy.provider_configs import get_provider_config, get_provider_credentials, list_provider_configs

MAX_REFERENCE_BYTES = 10 * 1024 * 1024
REFERENCE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}


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
) -> list[ResolvedVideoReference]:
    references = []
    for ref in request.references:
        artifact = db.scalar(select(Artifact).where(
            Artifact.artifact_id == ref.artifact_id, Artifact.tenant_id == tenant_id,
            Artifact.user_id == user_id,
        ))
        if artifact is None or artifact.media_type != "image" or artifact.mime_type not in REFERENCE_MIME_TYPES:
            raise MediaRequestValidationError("First-frame image not found or not accessible/supported")
        if artifact.size_bytes is None or not 0 < artifact.size_bytes <= MAX_REFERENCE_BYTES:
            raise MediaRequestValidationError("First-frame image exceeds supported size")
        if read_bytes:
            chunks, total = [], 0
            for chunk in artifact_store.iter_bytes(artifact.storage_uri):
                total += len(chunk)
                if total > MAX_REFERENCE_BYTES:
                    raise MediaRequestValidationError("First-frame image exceeds supported size")
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                raise MediaRequestValidationError("First-frame image content is missing")
            references.append(ResolvedVideoReference(artifact.artifact_id, artifact.mime_type, data))
    return references
