from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.media.constraints import VIDEO_DURATION_MIN, VIDEO_DURATION_MAX, VideoResolution

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
    "2:3",
    "4:5",
    "1:1",
    "16:9",
    "9:16",
]

GridArrangement = Literal[
    "2x2",
    "1x4",
    "4x1",
]

GenerationStrategy = Literal[
    "single",
    "variations",
    "compare",
]

ReferenceRole = Literal[
    "character",
    "outfit",
    "style",
    "pose",
    "composition",
    "environment",
    "previous_output",
]

ImageStyle = Literal[
    "auto",
    "photoreal",
    "anime",
    "3d",
    "painterly",
    "illustration",
    "comic",
    "cinematic",
]

class MediaReference(BaseModel):
    artifact_id: str

    role: ReferenceRole = "previous_output"

    strength: Literal[
        "loose",
        "normal",
        "strong",
    ] = "normal"

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

    arrangement: GridArrangement | None = None


class MediaOutputPreferences(BaseModel):
    quality: MediaQuality = "standard"

    aspect_ratio: AspectRatio | None = None

    style: ImageStyle = "auto"

    count: int = Field(
        default=1,
        ge=1,
        le=4,
    )

    layout: MediaLayout = Field(
        default_factory=MediaLayout,
    )

class MediaAdvancedOptions(BaseModel):
    provider: str | None = None

    model: str | None = None

    parameters: dict[str, Any] = Field(
        default_factory=dict,
    )


class VideoOutputPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_seconds: int = Field(default=5, ge=VIDEO_DURATION_MIN, le=VIDEO_DURATION_MAX, strict=True)
    aspect_ratio: AspectRatio = "16:9"
    resolution: VideoResolution = "720p"
    audio: bool = Field(default=False, strict=True)
    count: Literal[1] = 1
    style: Literal["auto"] = "auto"
    quality: Literal["standard"] = "standard"
    layout: MediaLayout = Field(default_factory=MediaLayout)


class VideoReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=1)
    role: Literal["first_frame"] = "first_frame"


class MediaGenerationRequest(BaseModel):
    media_type: MediaType

    interface_level: InterfaceLevel = "easy"

    strategy: GenerationStrategy = "single"

    intent: MediaIntent

    output: MediaOutputPreferences | VideoOutputPreferences = Field(
        default_factory=MediaOutputPreferences,
    )

    references: list[MediaReference | VideoReference] = Field(
        default_factory=list,
    )

    advanced: MediaAdvancedOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_media_preferences(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if value.get("media_type") == "video":
            if set(value) - set(cls.model_fields):
                raise ValueError("Unsupported video request fields")
            if isinstance(value.get("intent"), dict) and set(value["intent"]) - {"description"}:
                raise ValueError("Unsupported video intent fields")
            advanced = value.get("advanced")
            if isinstance(advanced, dict) and set(advanced) - {"provider", "model", "parameters"}:
                raise ValueError("Unsupported advanced video fields")
            value["output"] = VideoOutputPreferences.model_validate(value.get("output", {}))
            value["references"] = [VideoReference.model_validate(ref) for ref in value.get("references", [])]
        else:
            value["output"] = MediaOutputPreferences.model_validate(value.get("output", {}))
            value["references"] = [MediaReference.model_validate(ref) for ref in value.get("references", [])]
        return value
