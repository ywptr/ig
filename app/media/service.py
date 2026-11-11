from dataclasses import dataclass
from dataclasses import asdict
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.models import Job

from app.images.service import (
    submit_image_generation,
)
from app.media.schemas import (
    MediaGenerationRequest,
)
from app.media.validation import (
    validate_media_request,
)
from app.jobs.service import create_job
from app.media.video_service import resolve_video_selection, resolve_video_references

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
) -> MediaSubmission:
    validate_media_request(request)

    if request.media_type == "video":
        selection = resolve_video_selection(db, request, tenant_id=tenant_id)
        resolve_video_references(db, request, tenant_id=tenant_id, user_id=user_id, read_bytes=False,
                                 provider=selection.provider, model=selection.model)
        job = create_job(
            db, capability="video.generate", tenant_id=tenant_id, user_id=user_id,
            defer_dispatch=defer_dispatch,
            input_data={"request_version": "1", "media_type": "video",
                        "request": request.model_dump(), "selection": asdict(selection)},
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
