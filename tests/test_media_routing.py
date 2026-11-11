"""Purpose selection, durable dispatch and provenance with isolated SQLite."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.test_media_job_contracts import ImageExecutionTestCase
from sqlalchemy import create_engine, func, select
from pydantic import ValidationError

from app.db.models import Artifact, Execution, ImageRequest, Job, TenantProviderConfig
from app.jobs.worker import execute_job
from app.media.capability_schemas import ProviderMetadata
from app.media.providers.base import ImageProviderResult
from app.media.providers.openai.image import OpenAIImageProvider
from app.media.providers.registry import IMAGE_PROVIDERS
from app.media.schemas import MediaGenerationRequest
from app.tenancy.provider_configs import set_provider_config


class MediaRoutingTests(ImageExecutionTestCase):
    def create_test_engine(self):
        directory = self.enterContext(TemporaryDirectory())
        return create_engine(f"sqlite:///{Path(directory) / 'routing.sqlite'}",
                             connect_args={"check_same_thread": False})

    def setUp(self):
        super().setUp()
        self.provider.generate.side_effect = lambda request: ImageProviderResult(
            request.provider, request.model, "image/png", b"fake-image", 10,
        )

    def configure(self, provider="google", *, tenant="tenant-one", enabled=True, config=None, credentials=True):
        with self.sessions() as db:
            set_provider_config(db, tenant_id=tenant, provider=provider, enabled=enabled,
                                credentials={"api_key": "test-only"} if credentials else None,
                                config=config if config is not None else {})

    def disable(self, provider="openai"):
        with self.sessions() as db:
            db.get(TenantProviderConfig, ("tenant-one", provider)).enabled = False
            db.commit()

    def submit(self, *, purpose="general", **fields):
        return self.submit_image(output={"purpose": purpose}, **fields)

    def selection(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.input_json["request_id"].as_string() == response.json()["request_id"]))
            return job.job_id, job.input_json["selection"]

    def assert_no_submission(self, response):
        self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()
        self.provider_factory.assert_not_called()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Job)))
            self.assertIsNone(db.scalar(select(ImageRequest)))

    def test_purpose_schema_defaults_and_unknown_rejected(self):
        request = MediaGenerationRequest(media_type="image", intent={"description": "image"})
        self.assertEqual(request.output.purpose, "general")
        response = self.submit_image(output={"purpose": "cover"})
        self.assertEqual(response.status_code, 422)
        with self.assertRaises(ValidationError):
            MediaGenerationRequest(media_type="video", intent={"description": "video"}, output={"purpose": "thumbnail"})
        self.queue.enqueue.assert_not_called()

    def test_general_preserves_existing_default_with_both_providers(self):
        self.configure()
        for fields in ({}, {"output": {"purpose": "general"}}):
            _, selection = self.selection(self.submit_image(**fields))
            self.assertEqual(selection, {"provider": "openai", "model": "gpt-image-2",
                                         "purpose": "general", "reason": "tenant_default"})

    def test_google_only_automatic_easy_medium_and_hard_without_override(self):
        self.configure()
        self.disable()
        for level in ("easy", "medium", "hard"):
            job_id, selection = self.selection(self.submit(interface_level=level))
            self.assertEqual((selection["provider"], selection["model"]), ("google", "gemini-3.1-flash-lite-image"))
            self.assertEqual(selection["reason"], "adapter_default")
            execute_job(job_id)
        self.assertEqual(self.provider.generate.call_count, 3)

    def test_openai_only_easy_medium_preserve_default(self):
        for level in ("easy", "medium"):
            _, selection = self.selection(self.submit(interface_level=level))
            self.assertEqual((selection["provider"], selection["model"]), ("openai", "gpt-image-2"))

    def test_no_available_provider_fails_before_projection_and_enqueue(self):
        self.disable()
        self.assert_no_submission(self.submit())

    def test_another_tenants_config_cannot_supply_route(self):
        self.disable()
        self.configure(tenant="tenant-two")
        self.assert_no_submission(self.submit(purpose="thumbnail"))

    def test_routing_never_decrypts_credentials_or_constructs_clients(self):
        self.configure()
        with patch("app.tenancy.provider_configs.decrypt_credentials", side_effect=AssertionError("No decryption")):
            _, selection = self.selection(self.submit(purpose="thumbnail"))
        self.assertEqual(selection["reason"], "purpose:thumbnail")
        self.provider_factory.assert_not_called()

    def test_invalid_default_configuration_fails_without_exposing_raw_values(self):
        self.disable()
        self.configure(config={"default_models": {"image": "https://private-config-canary.test"}})
        response = self.submit()
        self.assert_no_submission(response)
        self.assertNotIn("private-config-canary", response.text)

    def test_thumbnail_routes_from_tags_without_changing_output_preferences(self):
        self.configure()
        response = self.submit_image(output={"purpose": "thumbnail", "quality": "high", "aspect_ratio": "16:9", "style": "illustration"})
        job_id, selection = self.selection(response)
        self.assertEqual(selection, {"provider": "google", "model": "gemini-3.1-flash-lite-image",
                                     "purpose": "thumbnail", "reason": "purpose:thumbnail"})
        execute_job(job_id)
        request = self.provider.generate.call_args.args[0]
        self.assertEqual((request.quality, request.aspect_ratio, request.style), ("high", "16:9", "illustration"))

    def test_disabled_specialist_falls_back_to_normal_default(self):
        self.configure(enabled=False)
        _, selection = self.selection(self.submit(purpose="thumbnail"))
        self.assertEqual(selection["provider"], "openai")
        self.assertEqual(selection["reason"], "purpose:thumbnail:fallback:tenant_default")

    def test_unconfigured_specialist_falls_back(self):
        self.configure(credentials=False)
        _, selection = self.selection(self.submit(purpose="thumbnail"))
        self.assertEqual(selection["provider"], "openai")
        self.assertEqual(selection["reason"], "purpose:thumbnail:fallback:tenant_default")

    def test_no_specialist_fallback_records_adapter_default(self):
        self.configure(provider="openai", config={})
        _, selection = self.selection(self.submit(purpose="thumbnail"))
        self.assertEqual(selection["reason"], "purpose:thumbnail:fallback:adapter_default")

    def test_incompatible_reference_count_does_not_choose_specialist(self):
        self.configure()
        with self.sessions() as db:
            for identity in ("ref-one", "ref-two"):
                db.add(Artifact(artifact_id=identity, tenant_id="tenant-one", user_id="user-one",
                    media_type="image", mime_type="image/png", size_bytes=3, storage_uri="file:///unused"))
            db.commit()
        response = self.submit_image(output={"purpose": "thumbnail"}, references=[
            {"artifact_id": "ref-one"}, {"artifact_id": "ref-two"},
        ])
        _, selection = self.selection(response)
        self.assertEqual(selection["provider"], "openai")
        self.assertIn(":fallback:", selection["reason"])

    def test_hard_openai_override_beats_thumbnail(self):
        self.configure()
        _, selection = self.selection(self.submit(purpose="thumbnail", interface_level="hard",
            advanced={"provider": "openai", "model": "gpt-image-2"}))
        self.assertEqual((selection["provider"], selection["reason"]), ("openai", "explicit"))

    def test_hard_google_explicit_provider_only_and_model_only(self):
        self.configure()
        for advanced in ({"provider": "google"}, {"model": "gemini-3.1-flash-lite-image"},
                         {"provider": "google", "model": "gemini-3.1-flash-lite-image"}):
            _, selection = self.selection(self.submit(interface_level="hard", advanced=advanced))
            self.assertEqual((selection["provider"], selection["reason"]), ("google", "explicit"))

    def test_hard_unsupported_model_rejected_for_existing_provider(self):
        self.assert_no_submission(self.submit(interface_level="hard", advanced={"provider": "openai", "model": "unsupported"}))

    def test_hard_unavailable_explicit_provider_never_uses_purpose_fallback(self):
        self.assert_no_submission(self.submit(purpose="thumbnail", interface_level="hard", advanced={"provider": "google"}))

    def test_easy_medium_explicit_selection_remains_rejected(self):
        for level in ("easy", "medium"):
            response = self.submit(interface_level=level, advanced={"provider": "openai", "model": "gpt-image-2"})
            self.assertEqual(response.status_code, 400)
        self.queue.enqueue.assert_not_called()

    def test_selection_is_committed_before_enqueue(self):
        def check(job_id):
            with self.sessions() as db:
                job = db.scalar(select(Job).where(Job.job_id == job_id))
                self.assertEqual(job.input_json["selection"]["provider"], "openai")
                self.assertEqual(job.input_json["selection"]["purpose"], "general")
                self.assertIsNotNone(db.scalar(select(ImageRequest)))
        self.queue.enqueue.side_effect = check
        self.selection(self.submit())
        self.queue.enqueue.assert_called_once()

    def test_changed_defaults_do_not_reroute_queued_job_and_provenance_survives(self):
        self.configure()
        job_id, selection = self.selection(self.submit(purpose="thumbnail"))
        # Even a now-invalid default mapping cannot change this accepted model.
        self.configure(config={"default_models": "changed-after-enqueue"})
        with patch("app.images.tasks.resolve_image_model", side_effect=AssertionError("Must not resolve again")), patch(
            "app.media.routing.resolve_image_selection", side_effect=AssertionError("Must not reroute"),
        ):
            execute_job(job_id)
        request = self.provider.generate.call_args.args[0]
        self.assertEqual((request.provider, request.model), (selection["provider"], selection["model"]))
        with self.sessions() as db:
            execution = db.scalar(select(Execution))
            artifact = db.scalar(select(Artifact))
            for metadata in (execution.metadata_json, artifact.metadata_json):
                self.assertEqual(metadata["selection"], selection)
                self.assertEqual(metadata["purpose"], "thumbnail")
            self.assertEqual(execution.status, "completed")
            self.assertEqual(db.scalar(select(Job)).status, "completed")
            self.assertEqual(db.scalar(select(ImageRequest)).status, "completed")
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)

    def test_openai_default_is_pinned_before_tenant_change(self):
        job_id, selection = self.selection(self.submit())
        self.configure(provider="openai", config={"default_model": "later-model"})
        execute_job(job_id)
        self.assertEqual(self.provider.generate.call_args.args[0].model, selection["model"])

    def test_client_cannot_supply_a_trusted_selection(self):
        _, selection = self.selection(self.submit_image(selection={"provider": "untrusted", "model": "other", "reason": "explicit"}))
        self.assertEqual(selection["provider"], "openai")

    def test_disabled_after_enqueue_fails_without_replacement(self):
        self.configure()
        job_id, selection = self.selection(self.submit(purpose="thumbnail"))
        self.disable("google")
        with self.assertRaisesRegex(RuntimeError, "not configured"):
            execute_job(job_id)
        self.provider.generate.assert_not_called()
        with self.sessions() as db:
            job = db.scalar(select(Job))
            self.assertEqual(job.input_json["selection"], selection)
            self.assertEqual(job.status, "failed")

    def test_discovery_has_purposes_and_current_automatic_default(self):
        self.configure()
        response = self.client.get("/v2/media/capabilities")
        self.assertEqual(response.status_code, 200)
        image, video = response.json()["media_types"]
        self.assertEqual(image["purposes"], ["general", "thumbnail"])
        self.assertEqual(image["default_provider"], "openai")
        self.assertEqual(video["purposes"], [])
        google = next(provider for provider in image["providers"] if provider["id"] == "google")
        self.assertIn("thumbnail", google["models"][0]["use_cases"])
        self.assertEqual(google["models"][0]["supported_interface_levels"], ["easy", "medium", "hard"])
        self.disable()
        image = self.client.get("/v2/media/capabilities").json()["media_types"][0]
        self.assertEqual(image["default_provider"], "google")

    def test_future_tagged_provider_and_model_order_are_generic_and_deterministic(self):
        first = replace(OpenAIImageProvider.CAPABILITIES.models[0], id="test-first", use_cases=("thumbnail",))
        second = replace(first, id="test-second")
        class TaggedProvider(OpenAIImageProvider):
            CAPABILITIES = ProviderMetadata("Test provider", (first, second))
            @staticmethod
            def default_model_for(config):
                return "test-second"
        self.configure(provider="test-provider")
        with patch.dict(IMAGE_PROVIDERS, {"test-provider": TaggedProvider}):
            _, selection = self.selection(self.submit(purpose="thumbnail"))
            self.assertEqual((selection["provider"], selection["model"]), ("test-provider", "test-first"))
            self.configure()
            _, selection = self.selection(self.submit(purpose="thumbnail"))
            self.assertEqual(selection["provider"], "google")  # Earlier registered suitable provider.

    def test_older_job_without_selection_retains_legacy_execution(self):
        job_id, _ = self.selection(self.submit())
        with self.sessions() as db:
            job = db.scalar(select(Job))
            job.input_json = {key: value for key, value in job.input_json.items() if key != "selection"}
            db.commit()
        execute_job(job_id)
        self.assertEqual(self.provider.generate.call_args.args[0].provider, "openai")

    def test_no_available_provider_is_also_clean_for_legacy_v1(self):
        self.disable()
        self.assert_no_submission(self.client.post("/v1/images/generations", json={"prompt": "image"}))
