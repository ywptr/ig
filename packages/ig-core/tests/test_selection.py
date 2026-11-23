from unittest import TestCase

from pydantic import ValidationError

from ig_core.media.selection import ModelSelectionPolicy, ModelSelectionResult
from ig_core.workflows import GenerationWorkflow


class SelectionContractTests(TestCase):
    def test_order_and_serialization(self):
        policy = ModelSelectionPolicy.model_validate({
            "mode": "PREFERRED", "candidates": [{"model": "first"}, {"provider": "vendor", "model": "second"}],
        })
        result = ModelSelectionResult(policy=policy, selected=policy.candidates[1], reason="PREFERRED_CANDIDATE")
        self.assertEqual(result, ModelSelectionResult.model_validate_json(result.model_dump_json()))
        self.assertEqual([candidate.model for candidate in result.policy.candidates], ["first", "second"])

    def test_invalid_modes(self):
        for policy in (
            {"mode": "AUTO", "allow_fallback": True},
            {"mode": "LOCKED", "candidates": [{"model": "missing-provider"}]},
            {"mode": "LOCKED", "candidates": [{"provider": "vendor", "model": "model"}], "allow_fallback": True},
            {"mode": "PREFERRED"},
            {"mode": "PREFERRED", "candidates": [{"model": "duplicate"}] * 2},
        ):
            with self.subTest(policy=policy), self.assertRaises(ValidationError):
                ModelSelectionPolicy.model_validate(policy)

    def test_workflow_supports_and_serializes_normalized_selection_policy(self):
        workflow = GenerationWorkflow.model_validate({"steps": [{
            "step_id": "voice", "operation": "text_to_speech", "output": "audio",
            "inputs": {"prompt": {"kind": "text", "value": "Hello."}},
            "selection_policy": {"mode": "PREFERRED", "candidates": [{"model": "voice-model"}]},
        }]})
        self.assertEqual(workflow, GenerationWorkflow.model_validate_json(workflow.model_dump_json()))
