"""Local catalog, tenant governance and mocked image execution. Never paid I/O."""
import base64
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from sqlalchemy import select, func
from tests import test_media_job_contracts as contracts
from tests import test_service_access as access
from app.db.models import Artifact, Job, Execution, ImageRequest, TenantProviderConfig, TenantMembership, UserIdentity
from app.media.providers.openai.models import OPENAI_IMAGE_MODELS, DEFAULT_OPENAI_MODEL
from app.media.providers.openai.image import OpenAIImageProvider, resolve_openai_size, resolve_openai_quality
from app.media.providers.base import ImageProviderConfigurationError, ImageProviderResult, ResolvedImageRequest, ResolvedImageReference
from app.media.capability_schemas import ModelLifecycle
from app.media.model_governance import ModelGovernanceError, model_policy, resolve_model, validate_selected_model
from app.tenancy.provider_configs import set_provider_config
from app.jobs.worker import execute_job

IDS = tuple(OPENAI_IMAGE_MODELS)
ALTERNATE = "gpt-image-2.5-flare"
LAST = "gpt-image-2.6-sunburst"
PNG = b"\x89PNG\r\n\x1a\n" + b"mock-image"


class CatalogTests(unittest.TestCase):
    def test_five_explicit_definitions_and_lifecycle(self):
        self.assertEqual(set(IDS), {"gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2.6-flare", "gpt-image-2.6-sunburst"})
        for identity, definition in OPENAI_IMAGE_MODELS.items():
            self.assertEqual(definition.metadata.id, identity)
            self.assertEqual(definition.metadata.technical.output_mime_types, ("image/png",))
            self.assertEqual(definition.metadata.technical.max_prompt_characters, 32000)
            self.assertEqual(definition.metadata.technical.reference_inputs[0].max_count, 8)
            self.assertEqual(definition.output_format, "png")
        self.assertTrue(OPENAI_IMAGE_MODELS[DEFAULT_OPENAI_MODEL].metadata.lifecycle.deprecated)
        self.assertIsNone(OPENAI_IMAGE_MODELS[DEFAULT_OPENAI_MODEL].metadata.lifecycle.sunset_at)
        self.assertNotIn("gpt-image-1.5", IDS)

    def test_every_size_quality_and_edit_translation_uses_selected_definition(self):
        client = Mock()
        client.images.generate.return_value = client.images.edit.return_value = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode())])
        with patch("ig_core.providers.openai.image.OpenAI", return_value=client):
            adapter = OpenAIImageProvider(api_key="fake-only")
        for identity, definition in OPENAI_IMAGE_MODELS.items():
            for ratio, size in definition.sizes.items():
                for profile, quality in definition.qualities.items():
                    with self.subTest(model=identity, ratio=ratio, quality=profile):
                        request = ResolvedImageRequest("A lake", profile, ratio, "auto", "single", None, None, model=identity, provider="openai")
                        result = adapter.generate(request)
                        wire = client.images.generate.call_args.kwargs
                        self.assertEqual((wire["model"], wire["size"], wire["quality"], wire["output_format"]), (identity, size, quality, "png"))
                        self.assertEqual((result.model, result.mime_type), (identity, "image/png"))
            reference = ResolvedImageReference("artifact", "previous_output", "normal", "image/png", "ref.png", PNG)
            adapter.generate(replace(request, references=[reference], parameters={"background": "transparent"}))
            wire = client.images.edit.call_args.kwargs
            self.assertEqual(wire["model"], identity)
            self.assertEqual(wire["background"], "transparent")
            self.assertTrue(wire["image"][0].closed)
            with self.assertRaises(ImageProviderConfigurationError):
                adapter.generate(replace(request, references=[reference] * 9))
            with self.assertRaises(ImageProviderConfigurationError):
                resolve_openai_size("9:16", identity)
            with self.assertRaises(ImageProviderConfigurationError):
                resolve_openai_quality("maximum", identity)

    def test_model_specific_parameters_and_unknown_values_fail_before_io(self):
        client = Mock()
        client.images.generate.return_value = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode())])
        with patch("ig_core.providers.openai.image.OpenAI", return_value=client):
            adapter = OpenAIImageProvider(api_key="fake-only")
        for identity, definition in OPENAI_IMAGE_MODELS.items():
            request = ResolvedImageRequest("A lake", "draft", "1:1", "auto", "single", None, None, model=identity, provider="openai")
            if definition.complexity_levels:
                adapter.generate(replace(request, parameters={"complexity": "low"}))
                self.assertEqual(client.images.generate.call_args.kwargs["extra_body"]["complexity"], "low")
            else:
                with self.assertRaises(ImageProviderConfigurationError): adapter.generate(replace(request, parameters={"complexity": "low"}))
            for values in ({"size": "huge"}, {"output_format": "svg"}, {"quality": "max"}, {"background": "invalid"}):
                client.images.generate.reset_mock()
                with self.assertRaises(ImageProviderConfigurationError): adapter.generate(replace(request, parameters=values))
                client.images.generate.assert_not_called()
        for unknown in ("invented-model", "gpt-image-1.5"):
            with self.assertRaises(ValueError): adapter.generate(replace(request, model=unknown))

    def test_pinned_sdk_serializes_new_models_and_complexity_for_generations_and_edits(self):
        import json
        import httpx2
        from openai import OpenAI
        requests = []
        def handle(request):
            requests.append(request)
            request.read()
            return httpx2.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        with httpx2.Client(transport=httpx2.MockTransport(handle)) as transport:
            client = OpenAI(api_key="fake-only", http_client=transport)
            with patch("ig_core.providers.openai.image.OpenAI", return_value=client):
                adapter = OpenAIImageProvider(api_key="fake-only")
            request = ResolvedImageRequest("A lake", "draft", "1:1", "auto", "single", None, None,
                provider="openai", model=LAST, parameters={"complexity": "low"})
            adapter.generate(request)
            body = json.loads(requests[-1].content)
            self.assertEqual((body["model"], body["complexity"], body["output_format"]), (LAST, "low", "png"))
            reference = ResolvedImageReference("artifact", "previous_output", "normal", "image/png", "ref.png", PNG)
            adapter.generate(replace(request, references=[reference]))
            self.assertIn(b'name="complexity"', requests[-1].content)
            self.assertIn(b"low", requests[-1].content)
            self.assertIn(LAST.encode(), requests[-1].content)

    def test_legacy_and_media_specific_defaults_and_restrictions(self):
        adapter = OpenAIImageProvider
        self.assertEqual(resolve_model(adapter, {}, "image"), DEFAULT_OPENAI_MODEL)
        self.assertEqual(resolve_model(adapter, {"default_model": ALTERNATE}, "image"), ALTERNATE)
        self.assertEqual(resolve_model(adapter, {"default_model": "ignored", "default_models": {"image": LAST}}, "image"), LAST)
        self.assertEqual(resolve_model(adapter, {"enabled_models": {"image": [LAST, ALTERNATE]}}, "image"), ALTERNATE)  # Catalog order, not user array order.
        for config in ({"default_model": "unknown"}, {"default_models": {"image": "unknown"}},
                       {"enabled_models": {"image": [LAST]}, "default_model": DEFAULT_OPENAI_MODEL},
                       {"enabled_models": {"image": ["unknown"]}}, {"enabled_models": {"image": [LAST, LAST]}},
                       {"enabled_models": []}, {"default_models": {"image": ""}}):
            with self.subTest(config=config), self.assertRaises(ModelGovernanceError): model_policy(adapter, config, "image")
        self.assertIsNone(model_policy(adapter, {"enabled_models": {"image": []}}, "image").default)

    def test_deprecated_usable_until_explicit_sunset_without_default_migration(self):
        metadata = OPENAI_IMAGE_MODELS[DEFAULT_OPENAI_MODEL].metadata
        self.assertEqual(resolve_model(OpenAIImageProvider, {"default_model": DEFAULT_OPENAI_MODEL}, "image"), DEFAULT_OPENAI_MODEL)
        retired = replace(metadata, lifecycle=ModelLifecycle(deprecated=True, sunset_at=date(2000, 1, 1), replacement_model=ALTERNATE))
        declaration = replace(OpenAIImageProvider.CAPABILITIES, models=(retired, *OpenAIImageProvider.CAPABILITIES.models[1:]))
        with patch.object(OpenAIImageProvider, "CAPABILITIES", declaration):
            with self.assertRaisesRegex(ModelGovernanceError, "sunset"):
                model_policy(OpenAIImageProvider, {"default_model": DEFAULT_OPENAI_MODEL}, "image")
            self.assertEqual(model_policy(OpenAIImageProvider, {}, "image").default, ALTERNATE)


class GovernanceTests(contracts.ImageExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.provider.generate.side_effect = lambda request: ImageProviderResult(request.provider, request.model, "image/png", PNG, len(PNG))

    def config(self, value):
        with self.sessions() as db:
            return set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True, config=value)

    def catalog(self):
        return next(provider for media in self.client.get("/v2/media/capabilities").json()["media_types"] if media["id"] == "image"
                    for provider in media["providers"] if provider["id"] == "openai")

    def admin(self):
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"; db.commit()

    def job(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            row = db.scalar(select(Job).where(Job.input_json["request_id"].as_string() == response.json()["request_id"]))
            return row.job_id, row.input_json["selection"]

    def test_discovery_and_explicit_auto_use_same_tenant_policy(self):
        self.assertEqual([model["id"] for model in self.catalog()["models"] if model["available"]], list(IDS))
        self.config({"enabled_models": {"image": [LAST]}, "default_models": {"image": LAST}})
        catalog = self.catalog()
        self.assertEqual(catalog["default_model"], LAST)
        self.assertEqual([model["id"] for model in catalog["models"] if model["available"]], [LAST])
        self.assertTrue(all(model["known"] for model in catalog["models"]))
        self.assertEqual([model["id"] for model in catalog["models"] if model["enabled"]], [LAST])
        denied = self.submit_image(interface_level="hard", advanced={"provider": "openai", "model": DEFAULT_OPENAI_MODEL})
        self.assertEqual(denied.status_code, 400)
        with self.sessions() as db: self.assertEqual(db.scalar(select(func.count()).select_from(Job)), 0)
        _, selected = self.job(self.submit_image())
        self.assertEqual(selected["model"], LAST)

    def test_unknown_legacy_default_and_explicit_unknown_never_enqueue(self):
        for value in ({"default_model": "unknown"}, {"default_models": {"image": "unknown"}}):
            with self.assertRaises(ModelGovernanceError): self.config(value)
            with self.sessions() as db:
                db.get(TenantProviderConfig, ("tenant-one", "openai")).config_json = value; db.commit()
            self.assertEqual(self.submit_image().status_code, 400)
            self.assertFalse(self.catalog()["available"])
            self.assertIsNotNone(self.catalog()["configuration_error"])
        self.config({})
        self.assertEqual(self.submit_image(interface_level="hard", advanced={"provider": "openai", "model": "unknown"}).status_code, 400)
        self.queue.enqueue.assert_not_called()

    def test_pinned_job_and_artifact_keep_model_after_default_change(self):
        self.config({"default_models": {"image": ALTERNATE}})
        job_id, selection = self.job(self.submit_image())
        self.config({"default_models": {"image": LAST}})
        execute_job(job_id)
        self.assertEqual(self.provider.generate.call_args.args[0].model, ALTERNATE)
        with self.sessions() as db:
            job = db.scalar(select(Job)); execution = db.scalar(select(Execution)); artifact = db.scalar(select(Artifact))
            self.assertEqual(job.input_json["selection"], selection)
            self.assertEqual((execution.model, artifact.metadata_json["model"], artifact.metadata_json["selection"]["model"]), (ALTERNATE,) * 3)

    def test_disabling_pinned_model_preserves_accepted_work_and_unknown_model_fails(self):
        job_id, selection = self.job(self.submit_image())
        self.config({"enabled_models": {"image": [LAST]}, "default_models": {"image": LAST}})
        execute_job(job_id)
        self.assertEqual(self.provider.generate.call_args.args[0].model, selection["model"])
        with self.sessions() as db:
            job = db.scalar(select(Job))
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.input_json["selection"], selection)
        self.provider.generate.reset_mock()
        self.config({})
        other, _ = self.job(self.submit_image())
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.job_id == other)); value = dict(job.input_json)
            value["selection"] = {**value["selection"], "model": "historical-unknown"}; job.input_json = value; db.commit()
        with self.assertRaises(ValueError): execute_job(other)
        self.provider.generate.assert_not_called()

    def test_invalid_controls_prompt_and_parameters_reject_before_jobs(self):
        for fields in ({"output": {"count": 2}}, {"intent": {"description": "x" * 32001}},
                       {"advanced": {"provider": "openai", "model": DEFAULT_OPENAI_MODEL, "parameters": {"complexity": "low"}}, "interface_level": "hard"},
                       {"advanced": {"provider": "openai", "model": LAST, "parameters": {"output_format": "svg"}}, "interface_level": "hard"}):
            self.assertEqual(self.submit_image(**fields).status_code, 400)
        self.queue.enqueue.assert_not_called()

    def test_admin_governance_updates_preserve_credentials_and_unrelated_config(self):
        self.admin(); self.config({"default_model": DEFAULT_OPENAI_MODEL, "operator_setting": "preserved"})
        with self.sessions() as db: encrypted = db.get(TenantProviderConfig, ("tenant-one", "openai")).encrypted_credentials
        path = "/v2/tenant/model-governance/openai"
        response = self.client.patch(path, json={"media_type": "image", "enabled_models": [ALTERNATE, LAST], "default_model": LAST})
        self.assertEqual(response.status_code, 200, response.text)
        for private in ("encrypted_credentials", "operator_setting", "api_key", encrypted): self.assertNotIn(private, response.text)
        with self.sessions() as db:
            row = db.get(TenantProviderConfig, ("tenant-one", "openai"))
            self.assertEqual(row.encrypted_credentials, encrypted)
            self.assertEqual(row.config_json["operator_setting"], "preserved")
            self.assertNotIn("default_model", row.config_json)
            self.assertEqual(row.config_json["default_models"]["image"], LAST)
        self.assertEqual(self.catalog()["default_model"], LAST)
        response = self.client.patch(path, json={"media_type": "image", "enabled_models": None, "default_model": None})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(model["enabled"] for model in self.catalog()["models"]))

    def test_member_and_service_cannot_administer_and_invalid_policy_is_atomic(self):
        path = "/v2/tenant/model-governance"
        self.assertEqual(self.client.get(path).status_code, 403)
        body = {"media_type": "image", "enabled_models": [LAST], "default_model": LAST}
        self.assertEqual(self.client.patch(path + "/openai", json=body).status_code, 403)
        self.admin()
        invalid = self.client.patch(path + "/openai", json={**body, "default_model": ALTERNATE})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.catalog()["default_model"], DEFAULT_OPENAI_MODEL)
        self.assertEqual(self.client.put("/v2/tenant/providers/openai", json={"config": {"default_model": "unknown"}}).status_code, 400)
        self.assertEqual(self.client.patch(path + "/openai", json={**body, "credentials": {"api_key": "rejected"}}).status_code, 422)

    def test_empty_allowlist_disables_auto_and_explicit_selection(self):
        self.config({"enabled_models": {"image": []}})
        self.assertFalse(self.catalog()["available"])
        self.assertIsNone(self.catalog()["default_model"])
        self.assertEqual(self.submit_image().status_code, 400)
        self.assertEqual(self.submit_image(interface_level="hard", advanced={"provider": "openai", "model": LAST}).status_code, 400)

    def test_service_idempotency_retains_selected_model_after_default_change(self):
        from app.auth.dependencies import get_current_user
        from app.main import app
        access.ServiceAccessTests.setUpClass.__func__(type(self))
        for name in ("register", "token", "headers", "submit"):
            setattr(self, name, getattr(access.ServiceAccessTests, name).__get__(self, type(self)))
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="actor", user_id="user-one", issuer=access.ISSUER, subject="human-one"))
            self.register(db); db.commit()
        self.admin()
        app.dependency_overrides.pop(get_current_user)
        self.enterContext(patch("app.media.submissions.get_job_queue", return_value=self.queue))
        self.config({"default_models": {"image": ALTERNATE}})
        first = self.submit(); self.assertEqual(first.status_code, 202, first.text)
        self.config({"default_models": {"image": LAST}})
        self.assertEqual(self.submit().json()["job_id"], first.json()["job_id"])
        execute_job(first.json()["job_id"]); execute_job(first.json()["job_id"])
        self.provider.generate.assert_called_once()
        self.assertEqual(self.provider.generate.call_args.args[0].model, ALTERNATE)
        self.assertEqual(self.client.get("/v2/tenant/model-governance", headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.patch("/v2/tenant/model-governance/openai", headers=self.headers(), json={"media_type": "image", "enabled_models": None, "default_model": None}).status_code, 403)
