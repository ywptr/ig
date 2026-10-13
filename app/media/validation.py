from app.media.schemas import (
    MediaGenerationRequest,
)

#### Validation logic for media generation requests

class MediaRequestValidationError(ValueError):
    pass


def validate_media_request(
    request: MediaGenerationRequest,
) -> None:
    layout = request.output.layout

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