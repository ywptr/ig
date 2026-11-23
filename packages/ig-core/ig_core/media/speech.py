"""Provider-neutral synthesis controls, voice references and adapter protocol."""
from typing import Annotated, ClassVar, Literal, Protocol, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ig_core.artifacts.types import ArtifactPayload

if TYPE_CHECKING:
    from ig_core.media.catalog import ModelMetadata, ProviderMetadata

OpaqueID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SpeechOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    voice: OpaqueID | None = None
    output_format: OpaqueID | None = None
    speaking_rate: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    instructions: str | None = Field(default=None, min_length=1, max_length=8192)


class SpeechGenerationRequest(SpeechOptions):
    operation: Literal["text_to_speech"] = "text_to_speech"
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32000)]


class VoiceSelection(BaseModel):
    """Model-scoped opaque reference with presentation metadata."""
    id: OpaqueID
    label: str


class SpeechFormat(BaseModel):
    id: OpaqueID
    mime_type: str


class SpeechCapabilities(BaseModel):
    voices: tuple[VoiceSelection, ...]
    default_voice: str
    formats: tuple[SpeechFormat, ...]
    default_format: str
    max_text_characters: int
    speaking_rate_range: tuple[float, float] | None = None
    max_instructions_characters: int | None = None

    def resolve(self, request: SpeechGenerationRequest) -> SpeechGenerationRequest:
        voice = request.voice or self.default_voice
        output_format = request.output_format or self.default_format
        if len(request.text) > self.max_text_characters:
            raise ValueError(f"Speech text exceeds the {self.max_text_characters}-character limit")
        if voice not in {item.id for item in self.voices}:
            raise ValueError("Unsupported voice for selected speech model")
        if output_format not in {item.id for item in self.formats}:
            raise ValueError("Unsupported speech output format")
        if request.speaking_rate is not None and (
            not self.speaking_rate_range or not self.speaking_rate_range[0] <= request.speaking_rate <= self.speaking_rate_range[1]
        ):
            raise ValueError("Unsupported speech speaking rate")
        if request.instructions and (
            not self.max_instructions_characters or len(request.instructions) > self.max_instructions_characters
        ):
            raise ValueError("Unsupported speech instructions or instruction length")
        return request.model_copy(update={"voice": voice, "output_format": output_format})


def resolve_model_speech(metadata: "ModelMetadata", request: SpeechGenerationRequest, level: str) -> SpeechGenerationRequest:
    technical = metadata.technical
    if (technical is None or request.operation not in technical.operations
            or "text" not in technical.input_media or "audio" not in technical.output_media
            or metadata.speech is None or level not in metadata.supported_interface_levels):
        raise ValueError("Model does not support requested speech generation")
    return metadata.speech.resolve(request)


class SpeechProviderError(RuntimeError):
    """A sanitized provider failure safe for Job and Execution records."""


class SpeechProvider(Protocol):
    CAPABILITIES: ClassVar["ProviderMetadata"]

    @classmethod
    def configuration_is_valid(cls, config: dict) -> bool: ...

    @classmethod
    def from_config(cls, *, credentials: dict[str, str], config: dict, model: str) -> "SpeechProvider": ...

    def synthesize(self, request: SpeechGenerationRequest) -> ArtifactPayload: ...
