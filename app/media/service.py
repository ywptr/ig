from dataclasses import dataclass
from dataclasses import asdict
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.models import Job

from app.images.service import (
    submit_image_generation,
)
from ig_core.media.schemas import (
    MediaGenerationRequest,
)
from ig_core.media.validation import (
    validate_media_request,
)
from app.jobs.service import create_job
from app.media.video_service import VideoSelection, resolve_video_selection, resolved_video_request
from app.media.selection import accept_selection, legacy_snapshot

### Media generation service and its supporting types

@dataclass(frozen=True)
class MediaSubmission:
    media_type: str
    request_id: str
    status: str
    job_id: str | None = None


class MediaCapabilityNotAvailableError(
    ValueError
):
    def __init__(
        self,
        capability: str,
    ) -> None:
        super().__init__(
            f"Capability is not available: "
            f"{capability}"
        )

        self.capability = capability


CAPABILITIES = {
    "image": "image.generate",
    "video": "video.generate",
    "audio": "audio.generate",
}

def resolve_aspect_ratio(
    request: MediaGenerationRequest,
) -> str:
    if request.output.aspect_ratio is not None:
        return request.output.aspect_ratio

    if request.output.layout.type == "panel_grid":
        return "4:5"

    return "2:3"

def resolve_grid_arrangement(
    request: MediaGenerationRequest,
) -> str | None:
    layout = request.output.layout

    if layout.type != "panel_grid":
        return None

    if layout.arrangement is not None:
        return layout.arrangement

    if layout.panel_count == 4:
        return "2x2"

    return None

def submit_media_generation(
    request: MediaGenerationRequest,
    *,
    db: Session,
    tenant_id: str,
    user_id: str,
    defer_dispatch: bool = False,
    accepted_candidate=None,
) -> MediaSubmission:
    if request.media_type == "audio" and request.operation is None:
        raise MediaCapabilityNotAvailableError(CAPABILITIES["audio"])
    validate_media_request(request)

    if request.media_type == "audio":
        from app.media.speech import SpeechRequestError, select_speech, speech_request

        if request.selection_policy is not None:
            from app.media.model_governance import registered_model
            from app.media.providers.registry import SPEECH_PROVIDERS
            from ig_core.media.speech import resolve_model_speech

            candidate, audit = accept_selection(db, request, tenant_id=tenant_id, user_id=user_id, accepted_candidate=accepted_candidate)
            model = registered_model(SPEECH_PROVIDERS[candidate.provider], candidate.model)
            selection = candidate.model_dump()
            payload = resolve_model_speech(model, speech_request(request), request.interface_level)
        else:
            try:
                selection, payload = select_speech(db, request, speech_request(request), tenant_id=tenant_id)
            except ValueError as exc:
                raise SpeechRequestError(str(exc)) from None
            audit = legacy_snapshot(request, selection["provider"], selection["model"], "LEGACY_SPEECH_ROUTING")
        job = create_job(
            db, capability="audio.generate", tenant_id=tenant_id, user_id=user_id,
            defer_dispatch=defer_dispatch,
            input_data={"request_version": "1", "media_type": "audio",
                        "request": request.model_dump(), "speech": payload.model_dump(),
                        "selection": selection, "selection_audit": audit},
        )
        return MediaSubmission("audio", job.job_id, job.status, job.job_id)

    if request.media_type == "video":
        if request.selection_policy is not None:
            candidate, audit = accept_selection(db, request, tenant_id=tenant_id, user_id=user_id, accepted_candidate=accepted_candidate)
            selection = VideoSelection(candidate.provider, candidate.model)
        else:
            selection = resolve_video_selection(db, request, tenant_id=tenant_id, user_id=user_id)
            audit = legacy_snapshot(request, selection.provider, selection.model, "LEGACY_VIDEO_ROUTING")
        request = resolved_video_request(request, selection)
        job = create_job(
            db, capability="video.generate", tenant_id=tenant_id, user_id=user_id,
            defer_dispatch=defer_dispatch,
            input_data={"request_version": "1", "media_type": "video",
                        "request": request.model_dump(), "selection": asdict(selection), "selection_audit": audit},
        )
        return MediaSubmission("video", job.job_id, job.status, job.job_id)

    capability = CAPABILITIES[
        request.media_type
    ]
    resolved_aspect_ratio = resolve_aspect_ratio(
        request
    )
    resolved_arrangement = (
        resolve_grid_arrangement(
            request
        )
    )

    if request.media_type != "image":
        raise MediaCapabilityNotAvailableError(
            capability
        )

    image_record = submit_image_generation(
        db,
        prompt=request.intent.description,
        defer_dispatch=defer_dispatch,
        accepted_candidate=accepted_candidate,
        tenant_id=tenant_id,
        user_id=user_id,
        media_request={
            "request_version": "1",
            "media_type": request.media_type,
            "strategy": request.strategy,
            "interface_level": (
                request.interface_level
            ),
            "intent": request.intent.model_dump(),
            "output": {
                **request.output.model_dump(),
                "aspect_ratio": resolved_aspect_ratio,
                "layout": {
                    **request.output.layout.model_dump(),
                    "arrangement": (
                        resolved_arrangement
                    ),
                },
            },
            "references": [
                reference.model_dump()
                for reference in request.references
            ],
            **({"selection_policy": request.selection_policy.model_dump(mode="json")}
               if request.selection_policy is not None else {}),
            "advanced": (
                request.advanced.model_dump()
                if request.advanced is not None
                else None
            ),
        },
    )

    return MediaSubmission(
        media_type=request.media_type,
        request_id=image_record.request_id,
        status=image_record.status,
        job_id=db.scalar(select(Job.job_id).where(
            Job.tenant_id == tenant_id, Job.user_id == user_id,
            Job.input_json["request_id"].as_string() == image_record.request_id,
        )) if defer_dispatch else None,
    )
