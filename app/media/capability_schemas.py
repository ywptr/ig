"""Allowlisted discovery DTOs and provider registration metadata."""
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel, Field

from app.media.schemas import InterfaceLevel
from app.media.technical import TechnicalCapabilities


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
    count: IntegerRange
    layout_types: list[str]
    panel_counts: list[int] = Field(default_factory=list)
    panel_arrangements: list[str] = Field(default_factory=list)
    panel_arrangements_selectable_in: list[InterfaceLevel] = Field(default_factory=list)
    quality_profiles: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ModelMetadata:
    id: str
    display_name: str
    capabilities: GenerationControls
    use_cases: tuple[str, ...] = ()
    supported_interface_levels: tuple[InterfaceLevel, ...] = get_args(InterfaceLevel)
    technical: TechnicalCapabilities | None = None


@dataclass(frozen=True)
class ProviderMetadata:
    display_name: str
    models: tuple[ModelMetadata, ...]


class InterfaceSelection(BaseModel):
    explicit_provider_selection: bool
    explicit_model_selection: bool


class ModelCapability(BaseModel):
    id: str
    display_name: str
    available: bool
    usable: bool
    technical: TechnicalCapabilities | None = None
    supported_interface_levels: list[InterfaceLevel] = Field(default_factory=lambda: list(get_args(InterfaceLevel)))
    selectable_in: list[InterfaceLevel]
    capabilities: GenerationControls
    use_cases: list[str] = Field(default_factory=list)


class ProviderCapability(BaseModel):
    id: str
    display_name: str
    registered: Literal[True] = True
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
