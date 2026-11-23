"""OpenAI's bounded binary Speech API adapter."""
from datetime import date
from time import monotonic

import requests

from ig_core.artifacts.types import ArtifactPayload
from ig_core.media.catalog import GenerationControls, IntegerRange, ModelLifecycle, ModelMetadata, ProviderMetadata
from ig_core.media.speech import SpeechCapabilities, SpeechFormat, SpeechGenerationRequest, SpeechProviderError, VoiceSelection
from ig_core.media.technical import ExecutionBehavior, TechnicalCapabilities

MODEL = "gpt-4o-mini-tts"
VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer", "verse", "marin", "cedar")
FORMATS = {"mp3": "audio/mpeg", "wav": "audio/wav"}
RESPONSE_MIMES = {"mp3": {"audio/mpeg", "audio/mp3"}, "wav": {"audio/wav", "audio/x-wav", "audio/wave"}}
MAX_AUDIO_BYTES = 16 * 1024 * 1024
SPEECH_URL = "https://api.openai.com/v1/audio/speech"
SPEECH = SpeechCapabilities(
    voices=tuple(VoiceSelection(id=voice, label=voice.capitalize()) for voice in VOICES),
    default_voice="coral", formats=tuple(SpeechFormat(id=id_, mime_type=mime) for id_, mime in FORMATS.items()),
    default_format="mp3", max_text_characters=4096, speaking_rate_range=(0.25, 4),
    max_instructions_characters=4096,
)


def _matches_format(data: bytes, format_: str) -> bool:
    if format_ == "wav":
        return len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    return len(data) > 10 and (data.startswith(b"ID3") or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0))


class OpenAISpeechProvider:
    DEFAULT_MODEL = MODEL
    CAPABILITIES = ProviderMetadata("OpenAI", (ModelMetadata(
        MODEL, "GPT-4o mini TTS", GenerationControls(
            aspect_ratios=[], count=IntegerRange(min=1, max=1), layout_types=["single"]),
        technical=TechnicalCapabilities(
            input_media=("text",), output_media=("audio",), operations=("text_to_speech",),
            execution=ExecutionBehavior(mode="synchronous"), output_mime_types=tuple(FORMATS.values()),
            max_prompt_characters=SPEECH.max_text_characters),
        lifecycle=ModelLifecycle(deprecated=True, sunset_at=date(2027, 1, 6), replacement_model="gpt-realtime-2.1-mini"),
        speech=SPEECH,
    ),))

    def __init__(self, api_key: str, model: str):
        if model != MODEL:
            raise ValueError("Unsupported OpenAI speech model")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("OpenAI speech requires tenant API credentials")
        self._api_key = api_key
        self.model = model

    @classmethod
    def configuration_is_valid(cls, config: dict) -> bool:
        return isinstance(config, dict)

    @classmethod
    def from_config(cls, *, credentials, config, model):
        return cls(credentials.get("api_key"), model)

    def synthesize(self, request: SpeechGenerationRequest) -> ArtifactPayload:
        resolved = SPEECH.resolve(request)
        format_ = resolved.output_format
        mime = FORMATS[format_]
        params = {"model": self.model, "input": resolved.text, "voice": resolved.voice,
                  "response_format": format_, "stream_format": "audio"}
        if resolved.speaking_rate is not None:
            params["speed"] = resolved.speaking_rate
        if resolved.instructions is not None:
            params["instructions"] = resolved.instructions
        deadline = monotonic() + 120
        data = bytearray()
        try:
            with requests.post(SPEECH_URL, json=params, headers={"Authorization": f"Bearer {self._api_key}"},
                               timeout=(5, 25), allow_redirects=False, stream=True) as response:
                if response.status_code in {401, 403}:
                    raise SpeechProviderError("SPEECH_PROVIDER_ACCESS: Provider rejected access")
                if response.status_code == 429:
                    raise SpeechProviderError("SPEECH_PROVIDER_RATE_LIMIT: Provider rate limit reached")
                if response.status_code != 200:
                    raise SpeechProviderError("SPEECH_PROVIDER_FAILED: Provider could not generate speech")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"", "application/octet-stream", *RESPONSE_MIMES[format_]}:
                    raise SpeechProviderError("SPEECH_INVALID_AUDIO: Unexpected provider content type")
                for chunk in response.iter_content(chunk_size=65536):
                    if monotonic() > deadline or len(data) + len(chunk) > MAX_AUDIO_BYTES:
                        raise SpeechProviderError("SPEECH_PROVIDER_UNCERTAIN: Audio exceeded download bounds")
                    data.extend(chunk)
        except requests.RequestException:
            raise SpeechProviderError("SPEECH_PROVIDER_UNCERTAIN: Speech transport failed") from None
        if not _matches_format(data, format_):
            raise SpeechProviderError("SPEECH_INVALID_AUDIO: Invalid audio response")
        return ArtifactPayload("audio", mime, format_, bytes(data), {
            "voice": resolved.voice, "output_format": format_, "container": format_,
            **({"codec": "mp3"} if format_ == "mp3" else {}),
        })
