from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
import wave

import requests
from pydantic import ValidationError

from ig_core.artifacts.media_metadata import inspect_media
from ig_core.artifacts.types import ArtifactPayload
from ig_core.media.speech import SpeechGenerationRequest, SpeechProvider, SpeechProviderError
from ig_core.providers.openai.speech import MODEL, SPEECH, SPEECH_URL, OpenAISpeechProvider
from ig_core.workflows import supports_operation


def wav_bytes():
    data = BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(1600))
    return data.getvalue()


class SpeechTests(TestCase):
    def test_contract_and_capability(self):
        request = SpeechGenerationRequest(text="A short line.", voice="marin", output_format="wav", speaking_rate=0.95)
        self.assertEqual(request, SpeechGenerationRequest.model_validate_json(request.model_dump_json()))
        with self.assertRaises(ValidationError):
            SpeechGenerationRequest(text="A short line.", operation="image_to_video")
        model = OpenAISpeechProvider.CAPABILITIES.models[0]
        self.assertTrue(supports_operation(model.technical, "text_to_speech"))
        for options, error in (({"voice": "unknown"}, "voice"), ({"output_format": "pcm"}, "format"),
                               ({"text": "x" * 4097}, "limit"), ({"speaking_rate": 5}, "rate"),
                               ({"instructions": "x" * 4097}, "instructions")):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                SPEECH.resolve(SpeechGenerationRequest.model_validate({**request.model_dump(), **options}))

    def test_adapter_translation_and_mime(self):
        target = "ig_core.providers.openai.speech.requests.post"
        self.assertRaisesRegex(ValueError, "Unsupported", OpenAISpeechProvider, "test-key", "tts-1")
        request = SpeechGenerationRequest(text="Hello.", voice="marin", output_format="wav",
                                          speaking_rate=1.2, instructions="Speak softly.")
        data = wav_bytes()
        with patch(target) as post:
            response = post.return_value.__enter__.return_value
            response.headers = {"Content-Type": "application/octet-stream"}
            response.status_code = 200
            response.iter_content.return_value = [data[:50], data[50:]]
            payload = OpenAISpeechProvider("test-key", MODEL).synthesize(request)
        self.assertEqual(post.call_args.args, (SPEECH_URL,))
        self.assertEqual(post.call_args.kwargs["json"], {"model": MODEL, "input": "Hello.", "voice": "marin",
                         "response_format": "wav", "stream_format": "audio", "speed": 1.2, "instructions": "Speak softly."})
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual((payload.mime_type, payload.extension, payload.data), ("audio/wav", "wav", data))
        self.assertEqual(inspect_media(data, "audio")["duration_seconds"], 0.1)

    def test_provider_error_translation(self):
        provider = OpenAISpeechProvider("test-key", MODEL)
        request = SpeechGenerationRequest(text="Hello.")
        with patch("ig_core.providers.openai.speech.requests.post") as post:
            response = post.return_value.__enter__.return_value
            for code, error in ((401, "ACCESS"), (403, "ACCESS"), (429, "RATE_LIMIT"), (500, "FAILED")):
                response.status_code = code
                with self.subTest(code=code), self.assertRaisesRegex(SpeechProviderError, "SPEECH_PROVIDER_" + error):
                    provider.synthesize(request)
            response.status_code = 200
            response.headers = {"Content-Type": "text/html"}
            with self.assertRaisesRegex(SpeechProviderError, "SPEECH_INVALID_AUDIO"):
                provider.synthesize(request)
            post.side_effect = requests.Timeout("sensitive provider detail")
            with self.assertRaisesRegex(SpeechProviderError, "^SPEECH_PROVIDER_UNCERTAIN: Speech transport failed$"):
                provider.synthesize(request)

    def test_other_provider_uses_opaque_voice_ids(self):
        class SyntheticSpeech:
            CAPABILITIES = OpenAISpeechProvider.CAPABILITIES

            @classmethod
            def configuration_is_valid(cls, config):
                return True

            @classmethod
            def from_config(cls, *, credentials, config, model):
                return cls()

            def synthesize(self, request):
                return ArtifactPayload("audio", "audio/wav", "wav", wav_bytes(), {"voice": request.voice})

        adapter: SpeechProvider = SyntheticSpeech.from_config(credentials={}, config={}, model="synthetic")
        payload = adapter.synthesize(SpeechGenerationRequest(text="Hello.", voice="custom:42"))
        self.assertEqual(payload.metadata["voice"], "custom:42")
