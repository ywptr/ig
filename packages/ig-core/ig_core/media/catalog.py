"""Generation controls and provider catalog declarations."""
from dataclasses import dataclass, field
from typing import get_args
from datetime import date

from pydantic import BaseModel, Field

from ig_core.media.schemas import InterfaceLevel
from ig_core.media.speech import SpeechCapabilities
from ig_core.media.technical import TechnicalCapabilities


class IntegerRange(BaseModel):
    min: int
    max: int


class GenerationControls(BaseModel):
    text_to_media: bool = True
    reference_roles: list[str] = Field(default_factory=list)
    max_references: int = 0
    reference_strengths: list[str] = Field(default_factory=list)
    aspect_ratios: list[str]
    resolutions: list[str] = Field(default_factory=list)
    duration_seconds: IntegerRange | None = None
    audio: bool = False
    native_audio_required: bool = Field(default=False, exclude_if=lambda value: not value)
    count: IntegerRange
    layout_types: list[str]
    panel_counts: list[int] = Field(default_factory=list)
    panel_arrangements: list[str] = Field(default_factory=list)
    panel_arrangements_selectable_in: list[InterfaceLevel] = Field(default_factory=list)
    quality_profiles: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)


class ModelLifecycle(BaseModel):
    deprecated: bool = False
    sunset_at: date | None = None
    replacement_model: str | None = None


@dataclass(frozen=True)
class ModelMetadata:
    id: str
    display_name: str
    capabilities: GenerationControls
    use_cases: tuple[str, ...] = ()
    supported_interface_levels: tuple[InterfaceLevel, ...] = get_args(InterfaceLevel)
    technical: TechnicalCapabilities | None = None
    lifecycle: ModelLifecycle = field(default_factory=ModelLifecycle)
    speech: SpeechCapabilities | None = None


@dataclass(frozen=True)
class ProviderMetadata:
    display_name: str
    models: tuple[ModelMetadata, ...]


