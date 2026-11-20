"""A standalone consumer can build workflow plans with no IG app imports."""
import unittest

from pydantic import ValidationError

from ig_core.workflows import GenerationWorkflow


def voice_plan():
    return {"steps": [
        {"step_id": "voice", "operation": "text_to_speech", "output": "audio",
         "inputs": {"prompt": {"kind": "text", "value": "Ready to go?"}}},
        {"step_id": "video", "operation": "image_audio_to_video", "output": "video",
         "dependencies": ["voice"], "inputs": {
             "prompt": {"kind": "text", "value": "A wide shot of the deck"},
             "frame": {"kind": "artifact", "media_type": "image", "role": "first_frame", "artifact_id": "frame"},
             "track": {"kind": "step_output", "media_type": "audio", "role": "voice_track", "step_id": "voice"},
         }},
    ]}


class WorkflowContracts(unittest.TestCase):
    def test_standalone_round_trip(self):
        plan = GenerationWorkflow.model_validate(voice_plan())
        self.assertEqual(GenerationWorkflow.model_validate_json(plan.model_dump_json()), plan)
        self.assertEqual(plan.steps[1].dependencies, ("voice",))

    def test_rejects_unknown_operation_and_invalid_types(self):
        for field, value in (("operation", "provider_operation"), ("output", "image"),
                             ("inputs", {"prompt": {"kind": "text", "value": " "}})):
            plan = voice_plan()
            plan["steps"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                GenerationWorkflow.model_validate(plan)

    def test_rejects_missing_dependency_and_cycle(self):
        for index, dependencies in ((0, ["video"]), (1, ["missing"]), (1, []), (1, ["video"])):
            plan = voice_plan()
            plan["steps"][index]["dependencies"] = dependencies
            with self.subTest(dependencies=dependencies), self.assertRaises(ValidationError):
                GenerationWorkflow.model_validate(plan)

    def test_rejects_binding_to_wrong_output_kind(self):
        plan = voice_plan()
        plan["steps"][0].update(operation="text_to_image", output="image")
        with self.assertRaises(ValidationError):
            GenerationWorkflow.model_validate(plan)

    def test_video_audio_input_requires_an_explicit_capability(self):
        from dataclasses import replace

        from ig_core.media.schemas import MediaGenerationRequest
        from ig_core.media.technical import ReferenceInputSlot
        from ig_core.media.validation import MediaRequestValidationError, validate_model_capabilities
        from ig_core.providers.alibaba.video import AlibabaWanVideoProvider

        request = MediaGenerationRequest.model_validate({
            "media_type": "video", "intent": {"description": "A wide shot"},
            "references": [{"artifact_id": "frame", "role": "first_frame"},
                           {"artifact_id": "audio", "role": "voice_track"}],
        })
        base = AlibabaWanVideoProvider.CAPABILITIES.models[0]
        with self.assertRaises(MediaRequestValidationError):
            validate_model_capabilities(request, base)
        # A synthetic declaration exercises core admission without a provider call.
        audio = ReferenceInputSlot(name="voice", media_types=("audio",), mime_types=("audio/wav",),
                                   roles=("voice_track",), max_count=1, max_bytes=100000)
        technical = base.technical.model_copy(update={
            "reference_inputs": (*base.technical.reference_inputs, audio),
            "input_media": (*base.technical.input_media, "audio"),
            "operations": ("image_audio_to_video",),
        })
        model = replace(base, technical=technical,
                        capabilities=base.capabilities.model_copy(update={"max_references": 2}))
        validate_model_capabilities(request, model)
