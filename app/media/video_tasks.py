"""Generic video orchestration; provider protocol details stay in adapters."""
from app.artifacts.types import ArtifactPayload
from app.db.database import SessionLocal
from app.jobs.result import Completed, Pending, ProviderOperation, TerminalOperationError
from app.media.providers.video import (
    ResolvedVideoRequest, VideoProviderCompleted, VideoProviderPending, VideoProviderTerminalError,
)
from app.media.schemas import MediaGenerationRequest
from app.media.validation import validate_media_request
from app.media.video_service import load_video_provider, resolve_video_references


def execute_video_generation(input_data: dict, *, tenant_id: str, user_id: str | None) -> Pending:
    if not tenant_id or not user_id:
        raise RuntimeError("Video generation requires trusted tenant and user ownership")
    request = MediaGenerationRequest.model_validate(input_data["request"])
    validate_media_request(request)
    if request.media_type != "video":
        raise ValueError("Video handler requires a video request")
    selection = input_data["selection"]
    with SessionLocal() as db:
        adapter = load_video_provider(
            db, tenant_id=tenant_id, provider=selection["provider"], model=selection["model"],
        )
        references = resolve_video_references(
            db, request, tenant_id=tenant_id, user_id=user_id, read_bytes=True,
        )
    # No database transaction spans provider submission.
    result = adapter.submit(ResolvedVideoRequest(
        description=request.intent.description, provider=selection["provider"], model=selection["model"],
        duration_seconds=request.output.duration_seconds, aspect_ratio=request.output.aspect_ratio,
        resolution=request.output.resolution, audio=request.output.audio, references=references,
    ))
    if not isinstance(result, VideoProviderPending):
        raise TypeError("Video submission must return a provider operation")
    return Pending(result.provider, result.model, result.operation_id, {
        "output": request.output.model_dump(), "provider_metadata": result.metadata,
    })


def resume_video_generation(operation: ProviderOperation, *, tenant_id: str, user_id: str):
    with SessionLocal() as db:
        adapter = load_video_provider(
            db, tenant_id=tenant_id, provider=operation.provider, model=operation.model,
        )
    try:
        result = adapter.poll(operation.operation_id, operation.metadata.get("provider_metadata", {}))
    except VideoProviderTerminalError as exc:
        raise TerminalOperationError(str(exc)) from None
    if isinstance(result, VideoProviderPending):
        return Pending(result.provider, result.model, result.operation_id, {
            **operation.metadata, "provider_metadata": result.metadata,
        })
    if not isinstance(result, VideoProviderCompleted):
        raise TypeError("Unexpected video provider outcome")
    return Completed(
        provider=result.provider, model=result.model,
        artifact=ArtifactPayload(
            result.media_type, result.mime_type, result.extension, result.data,
            {"output": operation.metadata.get("output", {}), "provider_metadata": result.metadata},
        ),
    )
