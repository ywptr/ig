from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.media.capability_schemas import ModelMetadata

from app.media.schemas import (
    MediaGenerationRequest,
)
from app.media.constraints import (
    IMAGE_ASPECT_RATIOS, VIDEO_ASPECT_RATIOS, MAX_IMAGE_REFERENCES,
    MAX_VIDEO_REFERENCES, EXPLICIT_SELECTION_LEVELS,
)

#### Validation logic for media generation requests

class MediaRequestValidationError(ValueError):
    pass


ALLOWED_ASPECT_RATIOS = {
    "image": frozenset(IMAGE_ASPECT_RATIOS),
    "video": frozenset(VIDEO_ASPECT_RATIOS),
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
        if len(request.references) > MAX_VIDEO_REFERENCES:
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
        request.interface_level not in EXPLICIT_SELECTION_LEVELS
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
        and request.interface_level not in EXPLICIT_SELECTION_LEVELS
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

    if len(request.references) > MAX_IMAGE_REFERENCES:
        raise MediaRequestValidationError(
            f"A maximum of {MAX_IMAGE_REFERENCES} reference images "
            "is supported"
        )


def validate_model_capabilities(request: MediaGenerationRequest, metadata: "ModelMetadata") -> None:
    """Validate a selected declaration before creating work; no tenant or provider I/O.

    Count/layout legacy acceptance remains adapter-owned. Undeclared legacy models
    must be handled explicitly by callers, never inferred from another model.
    """
    technical = metadata.technical
    if technical is None:
        raise MediaRequestValidationError("Model capabilities are unknown")
    if request.media_type not in technical.output_media:
        raise MediaRequestValidationError("Model does not support requested output media")
    if "text" not in technical.input_media:
        raise MediaRequestValidationError("Model does not accept text input")
    if not request.references:
        operation = {"image": "text_to_image", "video": "text_to_video"}.get(request.media_type)
        if operation is None or operation not in technical.operations:
            raise MediaRequestValidationError("Model does not support text generation for requested media")
    from app.artifacts.references import validate_reference_shape
    validate_reference_shape(request.references, technical.reference_inputs)
    controls = metadata.capabilities
    if request.references and (
        "image" not in technical.input_media
        or "reference_image_conditioning" not in technical.operations
    ):
        raise MediaRequestValidationError("Model does not support reference images")
    if not technical.reference_inputs and len(request.references) > controls.max_references:
        raise MediaRequestValidationError("Too many reference images for model")
    if len(request.references) > 1 and "multi_reference_conditioning" not in technical.operations:
        raise MediaRequestValidationError("Model does not support multiple references")
    ratio = request.output.aspect_ratio
    if ratio is not None and ratio not in controls.aspect_ratios:
        raise MediaRequestValidationError("Unsupported model aspect ratio")
    if technical.max_prompt_characters and len(request.intent.description) > technical.max_prompt_characters:
        raise MediaRequestValidationError("Prompt exceeds model limit")
    duration = getattr(request.output, "duration_seconds", None)
    if duration is not None and (controls.duration_seconds is None or not
        controls.duration_seconds.min <= duration <= controls.duration_seconds.max):
        raise MediaRequestValidationError("Unsupported model duration")
    resolution = getattr(request.output, "resolution", None)
    if resolution is not None and resolution not in controls.resolutions:
        raise MediaRequestValidationError("Unsupported model resolution")
    if getattr(request.output, "audio", False) and not controls.audio:
        raise MediaRequestValidationError("Model does not support audio output")
