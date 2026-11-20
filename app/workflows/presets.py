"""Curated workflow plans and tenant-aware model discovery."""
from typing import Literal

from pydantic import Field

from ig_core.media.technical import InputRole
from ig_core.workflows import (
    ArtifactInput, Contract, GenerationWorkflow, ModelConstraint, Operations, SIGNATURES,
    StepOutput, Text, TextInput, WorkflowStep, supports_operation,
)
from app.media.capabilities import media_capabilities

SINGLE_OPERATIONS = ("text_to_image", "image_to_image", "text_to_video", "image_to_video")
VOICE_OPERATIONS = ("text_to_speech", "image_audio_to_video")
ROLES: dict[Operations, tuple[InputRole, ...]] = {
    "image_to_image": ("previous_output",),
    "image_to_video": ("first_frame",),
    "image_audio_to_video": ("first_frame", "voice_track"),
}
IMPLEMENTED = frozenset(SINGLE_OPERATIONS)


class WorkflowSubmission(Contract):
    preset: Literal["single_generation", "voice_then_video"]
    operation: Literal["text_to_image", "image_to_image", "text_to_video", "image_to_video"] | None = None
    prompt: Text
    dialogue: Text | None = None
    image_artifact_id: str | None = Field(default=None, min_length=1, max_length=36)
    selection: ModelConstraint | None = None
    voice_selection: ModelConstraint | None = None
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)


def model_choices(db, tenant_id):
    choices = {operation: [] for operation in (*SINGLE_OPERATIONS, *VOICE_OPERATIONS)}
    for media in media_capabilities(db, tenant_id=tenant_id).media_types:
        for provider in media.providers:
            for model in provider.models:
                technical = model.technical
                if not model.usable or not technical or "hard" not in model.selectable_in:
                    continue
                for operation, options in choices.items():
                    roles = ROLES.get(operation, ())
                    if (operation in IMPLEMENTED and supports_operation(technical, operation)
                            and all(any(role in slot.roles for slot in technical.reference_inputs) for role in roles)
                            and all(slot.min_count <= sum(role in slot.roles for role in roles)
                                    for slot in technical.reference_inputs)):
                        options.append({"provider": provider.id, "model": model.id, "label": model.display_name})
    return choices


def preset_catalog(db, tenant_id):
    choices = model_choices(db, tenant_id)

    def plan(operation):
        return {"operation": operation, "output": SIGNATURES[operation][0],
                "models": choices[operation], "requires_image": "image" in SIGNATURES[operation][1]}

    missing = [operation for operation in VOICE_OPERATIONS if not choices[operation]]
    return [
        {"id": "single_generation", "label": "Single generation",
         "available": any(choices[op] for op in SINGLE_OPERATIONS), "experimental": False,
         "reason": None if any(choices[op] for op in SINGLE_OPERATIONS) else "No compatible tenant models are enabled.",
         "steps": [plan(op) for op in SINGLE_OPERATIONS]},
        {"id": "voice_then_video", "label": "Voice → Video", "experimental": True,
         "available": not missing,
         "reason": f"No executable tenant model for {', '.join(missing)}." if missing else None,
         "steps": [plan(op) for op in VOICE_OPERATIONS]},
    ]


def build_workflow(db, tenant_id, request: WorkflowSubmission) -> GenerationWorkflow:
    choices = model_choices(db, tenant_id)

    def select(operation, constraint):
        options = choices[operation]
        for option in options:
            selection = ModelConstraint(provider=option["provider"], model=option["model"])
            if constraint is None or constraint == selection:
                return selection
        raise ValueError(f"No compatible enabled model for {operation}")

    if request.preset == "voice_then_video":
        if request.operation or not request.dialogue or not request.image_artifact_id:
            raise ValueError("Voice → Video requires dialogue, a shot prompt and an image Artifact")
        voice = WorkflowStep(step_id="voice", operation="text_to_speech", output="audio",
                             inputs={"prompt": TextInput(value=request.dialogue)},
                             selection=select("text_to_speech", request.voice_selection))
        video = WorkflowStep(step_id="video", operation="image_audio_to_video", output="video",
                             dependencies=("voice",), selection=select("image_audio_to_video", request.selection),
                             inputs={"prompt": TextInput(value=request.prompt),
                                     "first_frame": ArtifactInput(media_type="image", role="first_frame",
                                                                  artifact_id=request.image_artifact_id),
                                     "voice_track": StepOutput(media_type="audio", role="voice_track", step_id="voice")})
        return GenerationWorkflow(steps=(voice, video))

    operation = request.operation
    if not operation or request.dialogue or request.voice_selection:
        raise ValueError("Single generation requires an operation and a prompt")
    output, required = SIGNATURES[operation]
    if bool(required) != bool(request.image_artifact_id):
        raise ValueError("Image Artifact is required only for image-conditioned operations")
    inputs = {"prompt": TextInput(value=request.prompt)}
    if request.image_artifact_id:
        inputs["image"] = ArtifactInput(media_type="image", role=ROLES[operation][0],
                                        artifact_id=request.image_artifact_id)
    return GenerationWorkflow(steps=(WorkflowStep(step_id="generation", operation=operation, inputs=inputs,
                              output=output, selection=select(operation, request.selection)),))
