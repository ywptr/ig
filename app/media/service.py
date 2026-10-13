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

    if request.media_type != "image":
        raise MediaCapabilityNotAvailableError(
            capability
        )

    image_record = submit_image_generation(
        prompt=request.intent.description,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    return MediaSubmission(
        media_type=request.media_type,
        request_id=image_record.request_id,
        status=image_record.status,
    )