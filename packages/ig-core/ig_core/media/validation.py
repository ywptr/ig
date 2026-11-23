from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ig_core.media.catalog import ModelMetadata

from ig_core.media.schemas import (
    MediaGenerationRequest,
)
from ig_core.media.constraints import (
    IMAGE_ASPECT_RATIOS, MAX_IMAGE_REFERENCES, EXPLICIT_SELECTION_LEVELS,
)

#### Validation logic for media generation requests

class MediaRequestValidationError(ValueError):
    pass


ALLOWED_ASPECT_RATIOS = {
    "image": frozenset(IMAGE_ASPECT_RATIOS),
}


def validate_media_request(
    request: MediaGenerationRequest,
) -> None:
    if request.media_type == "audio":
        if (request.operation != "text_to_speech" or request.strategy != "single" or request.references
                or not request.intent.description.strip() or (request.advanced and request.advanced.parameters)):
            raise MediaRequestValidationError("Audio requires text_to_speech, nonblank text and no references or provider parameters")
        if request.advanced is not None and request.interface_level not in EXPLICIT_SELECTION_LEVELS:
            raise MediaRequestValidationError("Advanced provider/model settings are only available in hard mode")
        return
    if request.operation is not None:
        raise MediaRequestValidationError("Speech operation requires audio media")
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
        if request.advanced and request.advanced.parameters:
            raise MediaRequestValidationError("Video accepts generic controls, not provider parameters")
        if not request.intent.description.strip():
            raise MediaRequestValidationError("Video intent must not be blank")

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

    if request.media_type == "image" and len(request.references) > MAX_IMAGE_REFERENCES:
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
    if request.media_type == "audio":
        from ig_core.media.speech import SpeechGenerationRequest, resolve_model_speech

        validate_media_request(request)
        try:
            resolve_model_speech(metadata, SpeechGenerationRequest(
                text=request.intent.description, **request.output.model_dump()), request.interface_level)
        except ValueError as exc:
            raise MediaRequestValidationError(str(exc)) from None
        return
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
    references = validate_reference_shape(request.references, technical.reference_inputs)
    controls = metadata.capabilities
    if request.interface_level not in metadata.supported_interface_levels:
        raise MediaRequestValidationError("Unsupported interface level for model")
    if (not controls.count.min <= request.output.count <= controls.count.max
        or request.output.layout.type not in controls.layout_types
        or (request.media_type == "image" and (request.output.quality not in controls.quality_profiles
            or request.output.style not in controls.styles))):
        raise MediaRequestValidationError("Unsupported model count, layout, quality or style")
    if request.output.layout.type == "panel_grid" and request.output.layout.panel_count not in controls.panel_counts:
        raise MediaRequestValidationError("Unsupported model panel count")
    if request.media_type == "video":
        if (not request.references and not controls.text_to_media) or len(request.references) > controls.max_references:
            raise MediaRequestValidationError("Unsupported model generation inputs")
        has_audio = any("audio" in slot.media_types and values["role"] == "voice_track"
                        for values, slot in references)
        has_image = any("image" in slot.media_types and values["role"] != "voice_track"
                        for values, slot in references)
        if has_audio:
            operation = "image_audio_to_video" if has_image else "audio_to_video"
        else:
            operation = "image_to_video" if request.references else "text_to_video"
        if (operation not in technical.operations or (has_audio and "audio" not in technical.input_media)
                or (has_image and "image" not in technical.input_media)):
            raise MediaRequestValidationError("Model does not support requested video operation")
        if request.interface_level not in metadata.supported_interface_levels:
            raise MediaRequestValidationError("Unsupported interface level for model")
        if not controls.count.min <= request.output.count <= controls.count.max or request.output.layout.type not in controls.layout_types:
            raise MediaRequestValidationError("Unsupported model count/layout")
    if request.references and request.media_type != "video" and (
        "image" not in technical.input_media or "reference_image_conditioning" not in technical.operations
    ):
        raise MediaRequestValidationError("Model does not support reference images")
    if not technical.reference_inputs and len(request.references) > controls.max_references:
        raise MediaRequestValidationError("Too many reference images for model")
    image_reference_count = sum("image" in slot.media_types and values.get("role") != "voice_track"
                                for values, slot in references)
    if image_reference_count > 1 and "multi_reference_conditioning" not in technical.operations:
        raise MediaRequestValidationError("Model does not support multiple references")
    ratio = request.output.aspect_ratio
    if ratio is not None and ratio not in controls.aspect_ratios:
        raise MediaRequestValidationError(f"Unsupported video aspect ratio: {ratio}" if request.media_type == "video" else "Unsupported model aspect ratio")
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


def validate_resolved_video(request, metadata):
    """Adapter defense uses the same declaration as submission validation."""
    normalized = MediaGenerationRequest.model_validate({"media_type": "video",
        "intent": {"description": request.description},
        "output": {"duration_seconds": request.duration_seconds, "aspect_ratio": request.aspect_ratio,
                   "resolution": request.resolution, "audio": request.audio},
        "references": [{"artifact_id": item.artifact_id, "role": item.role} for item in request.references]})
    validate_media_request(normalized)
    validate_model_capabilities(normalized, metadata)
    selected = validate_reference_shape(normalized.references, metadata.technical.reference_inputs)
    for reference, (_, slot) in zip(request.references, selected):
        if reference.mime_type not in slot.mime_types or not 0 < len(reference.data) <= slot.max_bytes:
            raise MediaRequestValidationError("Unsupported reference MIME type or size")


def validate_reference_shape(references, slots):
    counts = {slot.name: 0 for slot in slots}
    selected = []
    for ref in references:
        values = ref if isinstance(ref, dict) else ref.model_dump()
        slot = next((slot for slot in slots if values.get("role", "previous_output") in slot.roles), None)
        if slot is None:
            raise MediaRequestValidationError("Unsupported reference role")
        strength = values.get("strength")
        if strength is not None and strength not in slot.strengths:
            raise MediaRequestValidationError("Unsupported reference strength")
        counts[slot.name] += 1
        selected.append((values, slot))
    for slot in slots:
        if not slot.min_count <= counts[slot.name] <= slot.max_count:
            raise MediaRequestValidationError("Unsupported reference count for input slot")
    return selected
