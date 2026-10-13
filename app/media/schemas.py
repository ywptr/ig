from typing import Any, Literal

from pydantic import BaseModel, Field

### Collection of schemas for media generation requests and responses

MediaType = Literal[
    "image",
    "video",
    "audio",
]

InterfaceLevel = Literal[
    "easy",
    "medium",
    "hard",
]

MediaQuality = Literal[
    "draft",
    "standard",
    "high",
]

MediaLayoutType = Literal[
    "single",
    "panel_grid",
]

AspectRatio = Literal[
    "4:5",
    "1:1",
    "16:9",
]


class MediaIntent(BaseModel):
    description: str = Field(
        min_length=1,
    )


class MediaLayout(BaseModel):
    type: MediaLayoutType = "single"

    panel_count: int | None = Field(
        default=None,
        ge=2,
        le=4,
    )


class MediaOutputPreferences(BaseModel):
    quality: MediaQuality = "standard"

    aspect_ratio: AspectRatio | None = None

    count: int = Field(
        default=1,
        ge=1,
        le=4,
    )

    layout: MediaLayout = Field(
        default_factory=MediaLayout,
    )


class MediaReference(BaseModel):
    artifact_id: str

    role: str | None = None


class MediaAdvancedOptions(BaseModel):
    provider: str | None = None

    model: str | None = None

    parameters: dict[str, Any] = Field(
        default_factory=dict,
    )


class MediaGenerationRequest(BaseModel):
    media_type: MediaType

    interface_level: InterfaceLevel = "easy"

    intent: MediaIntent

    output: MediaOutputPreferences = Field(
        default_factory=MediaOutputPreferences,
    )

    references: list[MediaReference] = Field(
        default_factory=list,
    )

    advanced: MediaAdvancedOptions | None = None