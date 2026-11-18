"""Smoke test for an installed wheel, runnable outside the repository."""
from importlib import import_module
from importlib.util import find_spec
import pkgutil
import sys
import unittest
from unittest.mock import patch

import ig_core
from ig_core.media.schemas import MediaGenerationRequest
from ig_core.media.validation import validate_media_request, validate_model_capabilities
from ig_core.providers.alibaba.video import AlibabaWanVideoProvider
from ig_core.providers.runtime import ProviderRuntimeConfig, create_video_adapter
from ig_core.providers.video import ResolvedVideoRequest, VideoProviderPending


class IndependentConsumerTests(unittest.TestCase):
    def test_validate_translate_and_submit(self):
        for module in pkgutil.walk_packages(ig_core.__path__, "ig_core."):
            import_module(module.name)
        request = MediaGenerationRequest(
            media_type="video", intent={"description": "A lighthouse at dusk"},
            output={"duration_seconds": 5, "aspect_ratio": "9:16"},
        )
        model = AlibabaWanVideoProvider.CAPABILITIES.models[0]
        self.assertIn("text_to_video", model.technical.operations)
        validate_media_request(request)
        validate_model_capabilities(request, model)

        runtime = ProviderRuntimeConfig(
            provider="alibaba", credentials={"api_key": "mock-key"},
            model=model.id, endpoint="https://provider.example",
        )
        adapter = create_video_adapter(AlibabaWanVideoProvider, runtime)
        self.assertNotIn("mock-key", repr(runtime))
        resolved = ResolvedVideoRequest(
            description=request.intent.description, provider=runtime.provider,
            model=model.id, duration_seconds=request.output.duration_seconds,
            aspect_ratio=request.output.aspect_ratio, resolution=request.output.resolution,
            audio=request.output.audio,
        )
        with patch("ig_core.providers.alibaba.video.requests.request") as transport:
            response = transport.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {
                "output": {"task_id": "mock-operation", "task_status": "PENDING"},
            }
            outcome = adapter.submit(resolved)
        self.assertEqual(transport.call_args.kwargs["json"]["parameters"]["ratio"], "9:16")
        self.assertIsInstance(outcome, VideoProviderPending)
        self.assertEqual(outcome.operation_id, "mock-operation")

        for module in ("app", "fastapi", "sqlalchemy", "redis", "rq", "vercel"):
            self.assertIsNone(find_spec(module))
            self.assertNotIn(module, sys.modules)


if __name__ == "__main__":
    unittest.main()
