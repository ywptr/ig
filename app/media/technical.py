"""Versioned, provider-neutral execution contracts declared by adapters."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MediaKind = Literal["text", "image", "video", "audio"]
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


class TechnicalCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    input_media: tuple[MediaKind, ...]
    output_media: tuple[MediaKind, ...]
    operations: tuple[GenerationOperation, ...]
    execution: ExecutionBehavior
    reference_mime_types: tuple[str, ...] = ()
    output_mime_types: tuple[str, ...] = ()
    max_prompt_characters: int | None = Field(default=None, gt=0)
