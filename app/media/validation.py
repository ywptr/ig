from app.media.schemas import (
    MediaGenerationRequest,
)

#### Validation logic for media generation requests

class MediaRequestValidationError(ValueError):
    pass


ALLOWED_ASPECT_RATIOS = {
    "image": frozenset({"2:3", "4:5", "1:1", "16:9"}),
    "video": frozenset({"16:9", "9:16", "1:1"}),
}


def validate_media_request(
    request: MediaGenerationRequest,
) -> None:
    layout = request.output.layout
    aspect_ratio = request.output.aspect_ratio
    allowed_ratios = ALLOWED_ASPECT_RATIOS.get(request.media_type)
    if aspect_ratio is not None and allowed_ratios is not None and aspect_ratio not in allowed_ratios:
        raise MediaRequestValidationError(
            f"Unsupported {request.media_type} aspect ratio: {aspect_ratio}"
        )

    if request.media_type == "video":
        if request.strategy != "single" or layout.type != "single":
            raise MediaRequestValidationError("Video supports only single strategy and layout")
        if len(request.references) > 1:
            raise MediaRequestValidationError("Video supports at most one first_frame image")
        if request.advanced and request.advanced.parameters:
            raise MediaRequestValidationError("Video accepts generic controls, not provider parameters")
        if not request.intent.description.strip():
            raise MediaRequestValidationError("Video intent must not be blank")
        if len(request.intent.description) > 20000:
            raise MediaRequestValidationError("Video intent exceeds the supported length")

    if (
        request.media_type != "image"
        and layout.type == "panel_grid"
    ):
        raise MediaRequestValidationError(
            "panel_grid layout is only supported "
            "for image generation"
        )

    if layout.type == "panel_grid":
        if layout.panel_count is None:
            raise MediaRequestValidationError(
                "panel_count is required for "
                "panel_grid layout"
            )

    if (
        layout.type == "single"
        and layout.panel_count is not None
    ):
        raise MediaRequestValidationError(
            "panel_count may only be specified "
            "for panel_grid layout"
        )

    if (
        request.interface_level
        in {"easy", "medium"}
        and request.advanced is not None
    ):
        raise MediaRequestValidationError(
            "advanced provider/model settings "
            "are only available in hard mode"
        )

    if (
        layout.type == "single"
        and layout.arrangement is not None
    ):
        raise MediaRequestValidationError(
            "arrangement may only be specified "
            "for panel_grid layout"
        )

    if (
        layout.arrangement is not None
        and request.interface_level != "hard"
    ):
        raise MediaRequestValidationError(
            "panel arrangement override is only "
            "available in hard mode"
        )

    if (
        layout.arrangement in {
            "2x2",
            "1x4",
            "4x1",
        }
        and layout.panel_count != 4
    ):
        raise MediaRequestValidationError(
            "This arrangement requires "
            "panel_count=4"
        )

    if len(request.references) > 8:
        raise MediaRequestValidationError(
            "A maximum of 8 reference images "
            "is supported"
        )
