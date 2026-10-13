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