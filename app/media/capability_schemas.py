"""IG discovery DTOs with transitional catalog imports."""
from typing import Literal, get_args

from pydantic import BaseModel, Field

from ig_core.media.catalog import (
    GenerationControls as GenerationControls,
    IntegerRange as IntegerRange,
    ModelLifecycle as ModelLifecycle,
    ModelMetadata as ModelMetadata,
    ProviderMetadata as ProviderMetadata,
)
from ig_core.media.schemas import InterfaceLevel
from ig_core.media.speech import SpeechCapabilities
from ig_core.media.technical import TechnicalCapabilities


class InterfaceSelection(BaseModel):
    explicit_provider_selection: bool
    explicit_model_selection: bool


class ModelCapability(BaseModel):
    known: Literal[True] = True
    supported: bool = True
    enabled: bool = True
    lifecycle: ModelLifecycle = Field(default_factory=ModelLifecycle)
    id: str
    display_name: str
    available: bool
    usable: bool
    technical: TechnicalCapabilities | None = None
    speech: SpeechCapabilities | None = Field(default=None, exclude_if=lambda value: value is None)
    supported_interface_levels: list[InterfaceLevel] = Field(default_factory=lambda: list(get_args(InterfaceLevel)))
    selectable_in: list[InterfaceLevel]
    capabilities: GenerationControls
    use_cases: list[str] = Field(default_factory=list)


class ProviderCapability(BaseModel):
    id: str
    display_name: str
    registered: Literal[True] = True
    configuration_error: str | None = None
    configured: bool
    enabled: bool
    available: bool
    default_model: str | None
    default_model_available: bool
    models: list[ModelCapability]


class MediaCapability(BaseModel):
    id: str
    available: bool
    default_provider: str | None
    providers: list[ProviderCapability]
    purposes: list[str] = Field(default_factory=list)


class MediaCapabilities(BaseModel):
    schema_version: Literal[1] = 1
    interface_levels: dict[InterfaceLevel, InterfaceSelection]
    media_types: list[MediaCapability]
