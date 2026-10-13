from dataclasses import dataclass

from app.images.service import (
    submit_image_generation,
)
from app.media.schemas import (
    MediaGenerationRequest,
)
from app.media.validation import (
    validate_media_request,
)

### Media generation service and its supporting types

@dataclass(frozen=True)
class MediaSubmission:
    media_type: str
    request_id: str
    status: str


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

GridArrangement = Literal[
    "2x2",
    "1x4",
    "4x1",
]


class MediaLayout(BaseModel):
    type: MediaLayoutType = "single"

    panel_count: int | None = Field(
        default=None,
        ge=2,
        le=4,
    )

    arrangement: GridArrangement | None = None

def resolve_aspect_ratio(
    request: MediaGenerationRequest,
) -> str:
    if request.output.aspect_ratio is not None:
        return request.output.aspect_ratio

    if request.output.layout.type == "panel_grid":
        return "4:5"

    return "1:1"

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
    tenant_id: str,
    user_id: str,
) -> MediaSubmission:
    validate_media_request(request)

    capability = CAPABILITIES[
        request.media_type
    ]
    resolved_aspect_ratio = resolve_aspect_ratio(
        request
    )

    if request.media_type != "image":
        raise MediaCapabilityNotAvailableError(
            capability
        )

    image_record = submit_image_generation(
        prompt=request.intent.description,
        tenant_id=tenant_id,
        user_id=user_id,
        media_request={
            "request_version": "1",
            "media_type": request.media_type,
            "interface_level": (
                request.interface_level
            ),
            "intent": request.intent.model_dump(),
            "output": {
                **request.output.model_dump(),
                "aspect_ratio": resolved_aspect_ratio,
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
    )