"""Versioned, provider-neutral execution contracts declared by adapters."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MediaKind = Literal["text", "image", "video", "audio"]
InputRole = Literal["reference", "character", "outfit", "style", "pose", "composition",
                    "environment", "previous_output", "first_frame"]
GenerationOperation = Literal[
    "text_to_image", "image_to_image", "reference_image_conditioning",
    "multi_reference_conditioning", "text_to_video", "image_to_video",
    "image_editing", "inpainting", "variation", "upscale",
]


class ExecutionBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["synchronous", "asynchronous"]
    polling_required: bool = False
    webhook_supported: bool = False
    # IG cancellation fences work/results; it does not stop billable provider calls.
    cancellation: Literal["none", "queue_only", "cooperative"] = "cooperative"
    provider_cancellation_supported: bool = False


class ReferenceInputSlot(BaseModel):
    """A bounded group of artifact inputs, selected unambiguously by role."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    media_types: tuple[MediaKind, ...] = Field(min_length=1)
    mime_types: tuple[str, ...] = Field(min_length=1)
    roles: tuple[InputRole, ...] = Field(min_length=1)
    min_count: int = Field(default=0, ge=0)
    max_count: int = Field(ge=1)
    max_bytes: int = Field(gt=0)
    strengths: tuple[Literal["loose", "normal", "strong"], ...] = ()

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.min_count > self.max_count or len(set(self.roles)) != len(self.roles):
            raise ValueError("Invalid reference slot bounds/roles")
        return self


class TechnicalCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    input_media: tuple[MediaKind, ...]
    output_media: tuple[MediaKind, ...]
    operations: tuple[GenerationOperation, ...]
    execution: ExecutionBehavior
    reference_inputs: tuple[ReferenceInputSlot, ...] = ()
    reference_mime_types: tuple[str, ...] = ()
    output_mime_types: tuple[str, ...] = ()
    max_prompt_characters: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def unique_slots(self):
        names = [slot.name for slot in self.reference_inputs]
        roles = [role for slot in self.reference_inputs for role in slot.roles]
        if len(set(names)) != len(names) or len(set(roles)) != len(roles):
            raise ValueError("Reference slots must have unique names and non-overlapping roles")
        return self
