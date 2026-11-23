"""Serializable contracts for ordered media generation workflows."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ig_core.media.schemas import MediaType
from ig_core.media.selection import ModelSelectionPolicy
from ig_core.media.speech import SpeechOptions
from ig_core.media.technical import GenerationOperation, InputRole, TechnicalCapabilities

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32000)]
Operations = Literal[
    "text_to_speech", "text_to_image", "image_to_image", "text_to_video",
    "image_to_video", "image_audio_to_video", "audio_to_video", "reference_to_video",
]
SIGNATURES: dict[Operations, tuple[MediaType, frozenset[MediaType]]] = {
    "text_to_speech": ("audio", frozenset()),
    "text_to_image": ("image", frozenset()),
    "image_to_image": ("image", frozenset({"image"})),
    "text_to_video": ("video", frozenset()),
    "image_to_video": ("video", frozenset({"image"})),
    "reference_to_video": ("video", frozenset({"image"})),
    "image_audio_to_video": ("video", frozenset({"image", "audio"})),
    "audio_to_video": ("video", frozenset({"audio"})),
}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextInput(Contract):
    kind: Literal["text"] = "text"
    value: Text


class ArtifactBinding(Contract):
    media_type: MediaType
    role: InputRole

    @model_validator(mode="after")
    def validate_role(self):
        if (self.role == "voice_track") != (self.media_type == "audio"):
            raise ValueError("The voice_track role requires an audio Artifact")
        return self


class ArtifactInput(ArtifactBinding):
    kind: Literal["artifact"] = "artifact"
    artifact_id: str = Field(min_length=1, max_length=36)


class StepOutput(ArtifactBinding):
    kind: Literal["step_output"] = "step_output"
    step_id: Identifier


WorkflowInput = Annotated[TextInput | ArtifactInput | StepOutput, Field(discriminator="kind")]


class ModelConstraint(Contract):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=150)


class WorkflowStep(Contract):
    step_id: Identifier
    operation: Operations
    inputs: dict[Identifier, WorkflowInput] = Field(min_length=1, max_length=17)
    dependencies: tuple[Identifier, ...] = ()
    selection: ModelConstraint | None = None
    selection_policy: ModelSelectionPolicy | None = Field(default=None, exclude_if=lambda value: value is None)
    output: MediaType
    speech: SpeechOptions | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_signature(self):
        if self.selection and self.selection_policy:
            raise ValueError("Use either selection or selection_policy")
        if self.speech is not None and self.operation != "text_to_speech":
            raise ValueError("Speech options require text_to_speech")
        output, media = SIGNATURES[self.operation]
        references = [item for item in self.inputs.values() if isinstance(item, ArtifactBinding)]
        if (not isinstance(self.inputs.get("prompt"), TextInput)
                or sum(isinstance(item, TextInput) for item in self.inputs.values()) != 1):
            raise ValueError("Step requires one text input named prompt")
        if self.output != output or frozenset(item.media_type for item in references) != media:
            raise ValueError("Step media inputs or output do not match its operation")
        return self


class GenerationWorkflow(Contract):
    version: Literal[1] = 1
    steps: tuple[WorkflowStep, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_dependencies(self):
        preceding = {}
        for step in self.steps:
            if step.step_id in preceding:
                raise ValueError("Duplicate step ID")
            if len(set(step.dependencies)) != len(step.dependencies) or any(
                dependency not in preceding for dependency in step.dependencies
            ):
                raise ValueError("Dependencies must be unique earlier steps")
            for binding in step.inputs.values():
                if isinstance(binding, StepOutput) and (
                    binding.step_id not in step.dependencies or preceding[binding.step_id] != binding.media_type
                ):
                    raise ValueError("Output binding must match an earlier dependency's media type")
            preceding[step.step_id] = step.output
        return self


def supports_operation(technical: TechnicalCapabilities, operation: GenerationOperation) -> bool:
    if operation not in SIGNATURES:
        return False
    output, inputs = SIGNATURES[operation]
    return (operation in technical.operations and output in technical.output_media
            and {"text", *inputs}.issubset(technical.input_media))
