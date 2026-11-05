import base64
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from app.media.providers.base import (
    ImageProviderConfigurationError, ImageProviderError, ImageProviderResult,
    ResolvedImageReference, ResolvedImageRequest,
)
from app.media.providers.google.image import GoogleImageProvider
from app.media.providers.openai.image import OpenAIImageProvider
from app.media.providers.openai.prompts import build_image_prompt as legacy_prompt
from app.media.prompts import build_image_prompt
from app.media.providers.registry import IMAGE_PROVIDERS, create_image_provider, resolve_image_model
from tests.google_support import GoogleResponse, JPEG, MODEL, PNG, TEST_CREDENTIAL, WEBP, response_body


class GoogleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = GoogleImageProvider(api_key=TEST_CREDENTIAL)
        self.http = self.enterContext(patch("app.media.providers.google.image.requests.post"))
        self.http.return_value = GoogleResponse()

    def request(self, **fields):
        return ResolvedImageRequest(**{
            "description": "A lighthouse at sunset", "provider": "google", "model": MODEL,
            "quality": "standard", "aspect_ratio": "1:1", "style": "auto",
            "layout_type": "single", "panel_count": None, "arrangement": None, **fields,
        })

    def reference(self, **fields):
        return ResolvedImageReference(**{
            "artifact_id": "test-artifact", "mime_type": "image/png", "data": PNG,
            "filename": "private-name.png", "role": "character", "strength": "strong", **fields,
        })

    def test_registry_factory_default_and_missing_credentials(self):
        self.assertIs(IMAGE_PROVIDERS["google"], GoogleImageProvider)
        self.assertIn(MODEL, GoogleImageProvider.MODELS)
        self.assertEqual(GoogleImageProvider.DEFAULT_MODEL, MODEL)
        self.assertIsInstance(create_image_provider(" GOOGLE ", credentials={"api_key": TEST_CREDENTIAL}, config={}), GoogleImageProvider)
        for credentials in ({}, {"api_key": ""}, {"api_key": "\n"}):
            with self.subTest(credentials_present=bool(credentials)):
                with self.assertRaises(ImageProviderConfigurationError) as error:
                    create_image_provider("google", credentials=credentials, config={})
                self.assertNotIn(TEST_CREDENTIAL, str(error.exception))
        self.http.assert_not_called()

    def test_resolution_explicit_then_media_default_then_legacy_then_adapter(self):
        for config, explicit in (
            ({"default_models": {"image": "unsupported"}}, MODEL),
            ({"default_models": {"image": MODEL}, "default_model": "ignored"}, None),
            ({"default_model": MODEL}, None), ({}, None),
        ):
            self.assertEqual(resolve_image_model("google", config=config, model=explicit), MODEL)
            if not explicit:
                self.assertEqual(GoogleImageProvider.default_model_for(config), MODEL)
        self.adapter.generate(self.request(model=""))
        self.assertIn(MODEL, self.http.call_args.args[0])

    def test_unsupported_model_and_invalid_config_fail_before_http(self):
        for config in ({"default_models": "invalid"}, {"default_models": {"image": "unsupported"}}):
            with self.assertRaises(ImageProviderConfigurationError):
                resolve_image_model("google", config=config)
        for model in ("unsupported", "../../models/other", "gemini-nano-banana-2.1"):
            with self.assertRaises(ImageProviderConfigurationError):
                self.adapter.generate(self.request(model=model))
        self.http.assert_not_called()

    def test_text_request_uses_header_auth_one_candidate_and_generic_controls(self):
        result = self.adapter.generate(self.request())
        call = self.http.call_args
        self.assertEqual(call.args, (f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",))
        self.assertEqual(call.kwargs["headers"], {"x-goog-api-key": TEST_CREDENTIAL, "Content-Type": "application/json"})
        self.assertEqual(call.kwargs["timeout"], (5, 90))
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertTrue(call.kwargs["stream"])
        payload = call.kwargs["json"]
        self.assertEqual(payload["generationConfig"], {
            "candidateCount": 1, "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "1:1", "imageSize": "1K"},
        })
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertIn("A lighthouse at sunset", payload["contents"][0]["parts"][0]["text"])
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(payload))
        self.assertIsInstance(result, ImageProviderResult)
        self.assertEqual((result.provider, result.model, result.mime_type), ("google", MODEL, "image/png"))
        self.assertEqual((result.data, result.size_bytes), (PNG, len(PNG)))

    def test_all_common_ratios_and_styles_translate_semantically(self):
        controls = GoogleImageProvider.MODELS[MODEL].capabilities
        for ratio in controls.aspect_ratios:
            self.adapter.generate(self.request(aspect_ratio=ratio))
            self.assertEqual(self.http.call_args.kwargs["json"]["generationConfig"]["imageConfig"]["aspectRatio"], ratio)
        for style in controls.styles:
            request = self.request(style=style)
            self.adapter.generate(request)
            prompt = self.http.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
            self.assertEqual(prompt, build_image_prompt(request))
            if style == "auto":
                self.assertNotIn("Render this", prompt)

    def test_quality_profiles_do_not_change_resolution_or_thinking(self):
        for quality in ("draft", "standard", "high"):
            self.adapter.generate(self.request(quality=quality))
            config = self.http.call_args.kwargs["json"]["generationConfig"]
            self.assertEqual(config["imageConfig"]["imageSize"], "1K")
            self.assertNotIn("thinkingConfig", config)

    def test_semantic_reference_bytes_roles_and_strength(self):
        for role in GoogleImageProvider.MODELS[MODEL].capabilities.reference_roles:
            reference = self.reference(role=role)
            result = self.adapter.generate(self.request(references=[reference]))
            parts = self.http.call_args.kwargs["json"]["contents"][0]["parts"]
            self.assertEqual(parts[1], {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}})
            self.assertIn("Reference image 1:", parts[0]["text"])
            self.assertIn("Match it closely", parts[0]["text"])
            self.assertNotIn("private-name", json.dumps(parts))
            self.assertNotIn("test-artifact", json.dumps(parts))
            self.assertEqual(result.metadata["reference_count"], 1)

    def test_four_panel_layout_uses_existing_semantic_prompt(self):
        request = self.request(layout_type="panel_grid", panel_count=4, arrangement="2x2")
        self.adapter.generate(request)
        prompt = self.http.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertEqual(prompt, build_image_prompt(request))
        self.assertIn("4 distinct visual panels", prompt)

    def test_unsupported_controls_and_references_fail_before_http(self):
        for fields in (
            {"provider": "openai"}, {"aspect_ratio": "9:16"}, {"style": "unknown"},
            {"quality": "4k"}, {"parameters": {"grounding": True}},
            {"references": [self.reference(), self.reference()]},
            {"references": [self.reference(mime_type="image/svg+xml")]},
            {"references": [self.reference(data=b"")]},
            {"references": [self.reference(role="unknown")]},
            {"layout_type": "panel_grid", "panel_count": 2, "arrangement": None},
        ):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(ImageProviderConfigurationError):
                    self.adapter.generate(self.request(**fields))
        self.http.assert_not_called()

    def test_mime_decoding_and_metadata_allowlist(self):
        for data, mime_type in ((PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")):
            self.http.return_value = GoogleResponse(response_body(data=data, mime_type=mime_type))
            result = self.adapter.generate(self.request())
            self.assertEqual((result.data, result.size_bytes, result.mime_type), (data, len(data), mime_type))
            self.assertEqual(result.metadata, {
                "aspect_ratio": "1:1", "resolution": "1k", "quality_profile": "standard",
                "reference_count": 0, "request_id": "test-response-1",
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            })
            self.assertNotIn(TEST_CREDENTIAL, json.dumps(result.metadata))

    def test_http_failures_are_sanitized_and_never_retried(self):
        for status in (302, 400, 401, 403, 429, 500):
            self.http.reset_mock()
            self.http.return_value = GoogleResponse({"error": TEST_CREDENTIAL}, status=status)
            with self.assertRaises(ImageProviderError) as error:
                self.adapter.generate(self.request())
            self.assertNotIn(TEST_CREDENTIAL, str(error.exception))
            self.http.assert_called_once()
        self.http.side_effect = requests.Timeout(TEST_CREDENTIAL)
        with self.assertRaises(ImageProviderError) as error:
            self.adapter.generate(self.request())
        self.assertNotIn(TEST_CREDENTIAL, str(error.exception))
        self.assertIsNone(error.exception.__cause__)

    def test_blocked_truncated_missing_or_multiple_images_fail(self):
        bodies = [{}, [], {"promptFeedback": {"blockReason": "SAFETY"}}]
        for reason in ("SAFETY", "NO_IMAGE", "MAX_TOKENS", "UNKNOWN"):
            body = response_body()
            body["candidates"][0]["finishReason"] = reason
            bodies.append(body)
        body = response_body()
        body["candidates"][0]["content"]["parts"] = [{"text": "No image"}]
        bodies.append(body)
        body = response_body()
        body["candidates"][0]["content"]["parts"] *= 2
        bodies.append(body)
        for body in bodies:
            with self.subTest(body_type=type(body).__name__):
                self.http.return_value = GoogleResponse(body)
                with self.assertRaises(ImageProviderError):
                    self.adapter.generate(self.request())

    def test_malformed_mime_base64_and_signature_fail_safely(self):
        for mime_type, encoded in (
            ("image/svg+xml", "PHN2Zz4="), ("image/png", "%%%"), ("image/png", ""),
            ("image/png", base64.b64encode(JPEG).decode()), ({}, "abc"),
        ):
            body = response_body()
            body["candidates"][0]["content"]["parts"][1]["inlineData"] = {"mimeType": mime_type, "data": encoded}
            self.http.return_value = GoogleResponse(body)
            with self.assertRaises(ImageProviderError):
                self.adapter.generate(self.request())
        self.http.return_value = GoogleResponse(raw=b"not-json")
        with self.assertRaises(ImageProviderError):
            self.adapter.generate(self.request())

    def test_response_and_image_size_caps(self):
        with patch("app.media.providers.google.image.MAX_RESPONSE_BYTES", 8):
            with self.assertRaises(ImageProviderError):
                self.adapter.generate(self.request())
        with patch("app.media.providers.google.image.MAX_IMAGE_BYTES", 4):
            with self.assertRaises(ImageProviderError):
                self.adapter.generate(self.request())
        with patch("app.media.providers.google.image.MAX_REFERENCE_BYTES", 4):
            self.http.reset_mock()
            with self.assertRaises(ImageProviderConfigurationError):
                self.adapter.generate(self.request(references=[self.reference()]))
            self.http.assert_not_called()

    def test_thought_images_are_not_used_as_output(self):
        body = response_body()
        body["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "inlineData": {"mimeType": "image/png", "data": "private-thought"}})
        self.http.return_value = GoogleResponse(body)
        self.assertEqual(self.adapter.generate(self.request()).data, PNG)

    def test_response_deadline_stops_slow_progress_without_retry(self):
        with patch("app.media.providers.google.image.time.monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(ImageProviderError, "deadline exceeded"):
                self.adapter.generate(self.request())
        self.http.assert_called_once()


class OpenAIAdapterRegressionTests(unittest.TestCase):
    def test_text_and_reference_wire_requests_remain_unchanged(self):
        client = Mock()
        response = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode())])
        client.images.generate.return_value = response
        client.images.edit.return_value = response
        with patch("app.media.providers.openai.image.OpenAI", return_value=client):
            adapter = OpenAIImageProvider(api_key="test-only-not-an-openai-key")
        request = ResolvedImageRequest("A lighthouse", "high", "4:5", "auto", "single", None, None, provider="openai")
        result = adapter.generate(request)
        client.images.generate.assert_called_once_with(
            model="gpt-image-2", prompt=build_image_prompt(replace(request, model="gpt-image-2")),
            size="1024x1280", quality="high",
        )
        self.assertIs(legacy_prompt, build_image_prompt)
        self.assertEqual((result.data, result.mime_type), (PNG, "image/png"))
        reference = ResolvedImageReference("artifact", "character", "strong", "image/png", "ref.png", PNG)
        request = replace(request, references=[reference])
        result = adapter.generate(request)
        call = client.images.edit.call_args.kwargs
        self.assertEqual(call["model"], "gpt-image-2")
        self.assertEqual((call["size"], call["quality"]), ("1024x1280", "high"))
        self.assertEqual(call["prompt"], build_image_prompt(request))
        self.assertTrue(call["image"][0].closed)
        self.assertEqual(result.metadata["reference_count"], 1)
