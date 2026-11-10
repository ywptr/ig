"""Technical contracts and validation; no external provider requests."""
from dataclasses import replace
import unittest

from pydantic import ValidationError
from tests import test_media_capabilities as discovery
from app.media.providers.openai.image import OpenAIImageProvider
from app.media.capability_schemas import IntegerRange
from app.media.schemas import MediaGenerationRequest
from app.media.technical import TechnicalCapabilities, ExecutionBehavior
from app.media.validation import validate_model_capabilities, MediaRequestValidationError


class TechnicalContractTests(unittest.TestCase):
    def setUp(self):
        self.model = OpenAIImageProvider.CAPABILITIES.models[0]

    def request(self, **changes):
        return MediaGenerationRequest.model_validate({
            "media_type": "image", "intent": {"description": "A castle"}, **changes,
        })

    def test_current_openai_contract(self):
        technical = self.model.technical
        self.assertEqual(technical.version, 1)
        self.assertEqual(technical.output_media, ("image",))
        self.assertEqual(technical.input_media, ("text", "image"))
        self.assertIn("multi_reference_conditioning", technical.operations)
        self.assertNotIn("inpainting", technical.operations)
        self.assertEqual(technical.execution.mode, "synchronous")
        self.assertFalse(technical.execution.polling_required)
        self.assertFalse(technical.execution.provider_cancellation_supported)
        validate_model_capabilities(self.request(), self.model)
        validate_model_capabilities(self.request(references=[{"artifact_id": "one"}, {"artifact_id": "two"}]), self.model)

    def test_unsupported_output_and_constraints(self):
        for request in (
            self.request(media_type="video"),
            self.request(output={"aspect_ratio": "9:16"}),
            self.request(references=[{"artifact_id": str(i)} for i in range(9)]),
        ):
            with self.subTest(request=request), self.assertRaises(MediaRequestValidationError):
                validate_model_capabilities(request, self.model)

    def test_reference_support_is_explicit(self):
        technical = self.model.technical.model_copy(update={
            "input_media": ("text",), "operations": ("text_to_image",),
        })
        with self.assertRaisesRegex(MediaRequestValidationError, "reference images"):
            validate_model_capabilities(self.request(references=[{"artifact_id": "one"}]),
                                        replace(self.model, technical=technical))

    def test_typed_frozen_versioned_contract(self):
        for field, value in (("operations", ("invented",)), ("output_media", ("invented",)), ("version", 2)):
            data = self.model.technical.model_dump()
            data[field] = value
            with self.assertRaises(ValidationError):
                TechnicalCapabilities.model_validate(data)
        with self.assertRaises(ValidationError):
            self.model.technical.version = 2

    def test_illustrative_video_requires_no_adapter(self):
        technical = TechnicalCapabilities(
            input_media=("text", "image"), output_media=("video",),
            operations=("text_to_video", "image_to_video", "reference_image_conditioning"),
            execution=ExecutionBehavior(mode="asynchronous", polling_required=True),
        )
        model = replace(self.model, id="illustrative-video", technical=technical,
                        capabilities=self.model.capabilities.model_copy(update={"aspect_ratios": ["16:9"],
                            "resolutions": ["720p"], "duration_seconds": IntegerRange(min=3, max=8)}))
        self.assertEqual(model.technical.output_media, ("video",))
        self.assertTrue(model.technical.execution.polling_required)
        validate_model_capabilities(self.request(media_type="video"), model)
        for output in ({"duration_seconds": 10}, {"resolution": "1080p"}, {"audio": True}):
            with self.subTest(output=output), self.assertRaises(MediaRequestValidationError):
                validate_model_capabilities(self.request(media_type="video", output=output), model)

    def test_prompt_limit_and_operation_checked(self):
        for technical in (
            self.model.technical.model_copy(update={"max_prompt_characters": 2}),
            self.model.technical.model_copy(update={"operations": ("upscale",)}),
        ):
            with self.assertRaises(MediaRequestValidationError):
                validate_model_capabilities(self.request(), replace(self.model, technical=technical))


class TechnicalDiscoveryTests(discovery.MediaJobTestCase):
    configure = discovery.CapabilityDiscoveryTests.configure
    document = discovery.CapabilityDiscoveryTests.document
    provider = discovery.CapabilityDiscoveryTests.provider

    def test_contract_is_exposed_with_tenant_usability(self):
        self.assertFalse(self.provider()["models"][0]["usable"])
        self.configure()
        model = self.provider()["models"][0]
        self.assertTrue(model["usable"])
        self.assertEqual(model["technical"]["output_media"], ["image"])
        self.assertEqual(model["technical"]["execution"]["mode"], "synchronous")
        self.assertFalse(self.provider(host="other.test")["models"][0]["usable"])

    def test_invalid_capability_rejected_before_enqueue(self):
        self.configure()
        response = self.client.post("/v2/media/generations", json={
            "media_type": "image", "intent": {"description": "A castle"},
            "references": [{"artifact_id": str(i)} for i in range(9)],
        })
        self.assertEqual(response.status_code, 400)
        self.queue.enqueue.assert_not_called()
