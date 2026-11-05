"""Local, tenant-scoped discovery through the real FastAPI route and SQLite."""
from dataclasses import replace
from unittest.mock import patch

from tests.test_media_job_contracts import MediaJobTestCase, ImageExecutionTestCase
from sqlalchemy import select
from app.auth.dependencies import get_current_user
from app.db.models import TenantProviderConfig, TenantMembership, Job
from app.main import app
from app.media.constraints import IMAGE_ASPECT_RATIOS, VIDEO_ASPECT_RATIOS, VIDEO_RESOLUTIONS
from app.media.providers.alibaba.video import AlibabaWanVideoProvider
from app.media.providers.openai.image import OpenAIImageProvider, resolve_openai_model
from app.media.providers.registry import IMAGE_PROVIDERS, VIDEO_PROVIDERS, create_image_provider
from app.media.providers.video import VideoProviderConfigurationError
from app.media.video_service import resolve_video_selection
from app.media.schemas import MediaGenerationRequest
from app.media.validation import validate_media_request, MediaRequestValidationError
from app.jobs.worker import execute_job


class CapabilityDiscoveryTests(MediaJobTestCase):
    def setUp(self):
        super().setUp()
        # Listing must succeed even when neither credentials nor clients can be loaded.
        for target in (
            "app.tenancy.provider_configs.decrypt_credentials",
            "app.tenancy.provider_configs.get_provider_credentials",
            "app.media.providers.openai.image.OpenAI",
            "app.media.providers.alibaba.video.requests.request",
            "app.media.providers.alibaba.video.requests.get",
        ):
            self.enterContext(patch(target, side_effect=AssertionError("Discovery must remain local")))

    def configure(self, provider="openai", *, tenant="tenant-one", enabled=True,
                  credentials="opaque-encrypted-test-blob", config=None):
        with self.sessions() as db:
            row = db.get(TenantProviderConfig, (tenant, provider))
            if row is None:
                row = TenantProviderConfig(tenant_id=tenant, provider=provider)
                db.add(row)
            row.enabled = enabled
            row.encrypted_credentials = credentials
            row.config_json = config if config is not None else {}
            db.commit()

    def document(self, host="testserver"):
        response = self.client.get("/v2/media/capabilities", headers={"host": host})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def provider(self, media="image", provider="openai", host="testserver"):
        document = self.document(host)
        return next(p for m in document["media_types"] if m["id"] == media
                    for p in m["providers"] if p["id"] == provider)

    def test_unauthenticated_rejected(self):
        del app.dependency_overrides[get_current_user]
        self.assertEqual(self.client.get("/v2/media/capabilities").status_code, 401)

    def test_ordinary_member_can_list_empty_catalog(self):
        with self.sessions() as db:
            membership = db.get(TenantMembership, ("tenant-one", "user-one"))
            self.assertEqual(membership.role, "member")
        document = self.document()
        self.assertEqual([m["id"] for m in document["media_types"]], ["image", "video"])
        for media in document["media_types"]:
            self.assertFalse(media["available"])
            self.assertIsNone(media["default_provider"])
            for provider in media["providers"]:
                self.assertTrue(provider["registered"])
                self.assertFalse(provider["configured"])
                self.assertFalse(provider["enabled"])
                self.assertFalse(provider["available"])
                self.assertFalse(provider["models"][0]["available"])
                self.assertEqual(provider["models"][0]["selectable_in"], [])

    def test_non_member_cannot_inspect_tenant_configuration(self):
        with self.sessions() as db:
            db.delete(db.get(TenantMembership, ("tenant-one", "user-one")))
            db.commit()
        self.assertEqual(self.client.get("/v2/media/capabilities").status_code, 403)

    def test_configuration_is_tenant_scoped_and_does_not_leak_between_calls(self):
        self.configure()
        self.assertTrue(self.provider()["available"])
        self.assertFalse(self.provider(host="other.test")["configured"])
        self.configure(tenant="tenant-two", enabled=False)
        other = self.provider(host="other.test")
        self.assertTrue(other["configured"])
        self.assertFalse(other["enabled"])
        self.assertFalse(other["available"])
        self.assertTrue(self.provider()["available"])

    def test_missing_credentials_is_unconfigured_even_when_enabled(self):
        self.configure(credentials=None)
        provider = self.provider()
        self.assertTrue(provider["enabled"])
        self.assertFalse(provider["configured"])
        self.assertFalse(provider["available"])
        self.assertFalse(provider["default_model_available"])

    def test_disabled_provider_is_configured_but_unavailable(self):
        self.configure(enabled=False)
        provider = self.provider()
        self.assertTrue(provider["configured"])
        self.assertFalse(provider["available"])
        self.assertEqual(provider["models"][0]["selectable_in"], [])

    def test_video_requires_safe_non_secret_configuration(self):
        for base_url in (None, "http://example.test", "https://user:secret@example.test", "https://[broken"):
            with self.subTest(base_url=base_url):
                self.configure("alibaba", config={"base_url": base_url})
                self.assertFalse(self.provider("video", "alibaba")["configured"])
        self.configure("alibaba", config={"base_url": "https://example.test"})
        self.assertTrue(self.provider("video", "alibaba")["available"])

    def test_image_catalog_and_controls_match_current_implementation(self):
        self.configure()
        provider = self.provider()
        self.assertEqual(provider["display_name"], "OpenAI")
        self.assertEqual(provider["default_model"], "gpt-image-2")
        self.assertTrue(provider["default_model_available"])
        model = provider["models"][0]
        self.assertEqual((model["id"], model["display_name"]), ("gpt-image-2", "GPT Image 2"))
        controls = model["capabilities"]
        self.assertEqual(controls["aspect_ratios"], list(IMAGE_ASPECT_RATIOS))
        self.assertNotIn("9:16", controls["aspect_ratios"])
        self.assertEqual(controls["count"], {"min": 1, "max": 1})
        self.assertEqual(controls["max_references"], 8)
        self.assertIn("character", controls["reference_roles"])
        self.assertEqual(controls["reference_strengths"], ["loose", "normal", "strong"])
        self.assertEqual(controls["quality_profiles"], ["draft", "standard", "high"])
        self.assertIn("cinematic", controls["styles"])
        self.assertEqual(controls["layout_types"], ["single", "panel_grid"])
        self.assertEqual(controls["panel_counts"], [4])
        self.assertEqual(controls["panel_arrangements_selectable_in"], ["hard"])
        for ratio in controls["aspect_ratios"]:
            validate_media_request(MediaGenerationRequest(
                media_type="image", intent={"description": "test"}, output={"aspect_ratio": ratio},
            ))
        with self.assertRaises(MediaRequestValidationError):
            validate_media_request(MediaGenerationRequest(
                media_type="image", intent={"description": "test"}, output={"aspect_ratio": "9:16"},
            ))

    def test_video_catalog_and_generic_controls(self):
        self.configure("alibaba", config={"base_url": "https://example.test"})
        provider = self.provider("video", "alibaba")
        self.assertEqual(provider["display_name"], "Alibaba Model Studio")
        self.assertEqual(provider["default_model"], "wan3.0-video")
        model = provider["models"][0]
        self.assertEqual((model["id"], model["display_name"]), ("wan3.0-video", "Wan 3.0 Video"))
        controls = model["capabilities"]
        self.assertEqual(controls["aspect_ratios"], list(VIDEO_ASPECT_RATIOS))
        self.assertEqual(controls["resolutions"], list(VIDEO_RESOLUTIONS))
        self.assertEqual(controls["duration_seconds"], {"min": 2, "max": 30})
        self.assertEqual(controls["reference_roles"], ["first_frame"])
        self.assertEqual(controls["max_references"], 1)
        self.assertTrue(controls["audio"])
        self.assertTrue(controls["text_to_media"])
        self.assertEqual(controls["layout_types"], ["single"])
        self.assertEqual(controls["count"], {"min": 1, "max": 1})
        media = next(m for m in self.document()["media_types"] if m["id"] == "video")
        self.assertEqual(media["default_provider"], "alibaba")

    def test_interface_rules_and_model_selectability(self):
        self.configure()
        document = self.document()
        for level in ("easy", "medium", "hard"):
            self.assertEqual(document["interface_levels"][level], {
                "explicit_provider_selection": level == "hard",
                "explicit_model_selection": level == "hard",
            })
        model = self.provider()["models"][0]
        self.assertEqual(model["selectable_in"], ["hard"])
        self.assertEqual(model["supported_interface_levels"], ["easy", "medium", "hard"])
        self.assertEqual(model["use_cases"], [])

    def test_image_legacy_default_resolution_preserved(self):
        # The image pipeline uses default_model, not the video convention or ORM column.
        self.configure(config={"default_model": "legacy-model", "default_models": {"image": "ignored"}})
        with self.sessions() as db:
            db.get(TenantProviderConfig, ("tenant-one", "openai")).default_model = "also-ignored"
            db.commit()
        provider = self.provider()
        self.assertEqual(provider["default_model"], "legacy-model")
        self.assertFalse(provider["default_model_available"])
        self.assertTrue(provider["models"][0]["available"])
        for config in ({}, {"default_model": ""}, {"default_model": "gpt-image-2"}):
            self.configure(config=config)
            self.assertEqual(self.provider()["default_model"], resolve_openai_model(config.get("default_model")))

    def test_video_defaults_share_generation_resolution(self):
        for config in (
            {"base_url": "https://example.test"},
            {"base_url": "https://example.test", "default_models": {"video": "wan3.0-video"}},
        ):
            self.configure("alibaba", config=config)
            provider = self.provider("video", "alibaba")
            self.assertEqual(provider["default_model"], AlibabaWanVideoProvider.resolve_tenant_model(config))
            self.assertTrue(provider["default_model_available"])

    def test_invalid_video_default_does_not_break_hard_mode_catalog(self):
        for defaults in ({"video": "unsupported"},):
            self.configure("alibaba", config={"base_url": "https://example.test", "default_models": defaults})
            provider = self.provider("video", "alibaba")
            self.assertIsNone(provider["default_model"])
            self.assertFalse(provider["default_model_available"])
            self.assertTrue(provider["models"][0]["available"])

    def test_malformed_video_defaults_are_invalid_configuration(self):
        self.configure("alibaba", config={"base_url": "https://example.test", "default_models": "invalid"})
        provider = self.provider("video", "alibaba")
        self.assertFalse(provider["configured"])
        self.assertFalse(provider["available"])
        self.assertIsNone(provider["default_model"])

    def test_secret_and_provider_mechanics_are_not_exposed(self):
        self.configure("alibaba", credentials="encrypted-secret-canary", config={
            "base_url": "https://private-endpoint-canary.test", "api_key": "key-canary",
            "workspace": "workspace-canary", "Authorization": "auth-canary",
            "output_url": "https://temporary-url-canary.test", "storage_uri": "private-uri-canary",
        })
        response = self.client.get("/v2/media/capabilities")
        self.assertEqual(response.status_code, 200)
        for forbidden in (
            "canary", "encrypted_credentials", "api_key", "base_url", "workspace", "Authorization",
            "task_status", "task_id", "X-DashScope", "720P", "video_url", "response_format",
        ):
            self.assertNotIn(forbidden, response.text)
        self.queue.enqueue.assert_not_called()

    def test_unknown_provider_rows_are_not_advertised(self):
        self.configure("unregistered")
        self.assertNotIn("unregistered", str(self.document()))

    def test_future_registration_and_use_cases_need_no_route_changes(self):
        class FutureVideo(AlibabaWanVideoProvider):
            DEFAULT_MODEL = "test-model"
            MODELS = frozenset({DEFAULT_MODEL, "test-alternate"})
            CAPABILITIES = replace(AlibabaWanVideoProvider.CAPABILITIES,
                display_name="Test provider", models=(replace(
                    AlibabaWanVideoProvider.CAPABILITIES.models[0], id="test-model",
                    display_name="Test model", use_cases=("draft",),
                ), replace(AlibabaWanVideoProvider.CAPABILITIES.models[0],
                    id="test-alternate", display_name="Test alternate model"),))

        self.configure("alibaba", config={"base_url": "https://example.test"})
        self.configure("test-provider", config={
            "base_url": "https://example.test", "default_models": {"video": "test-alternate"},
        })
        with patch.dict(VIDEO_PROVIDERS, {"test-provider": FutureVideo}):
            document = self.document()
            video = next(m for m in document["media_types"] if m["id"] == "video")
            self.assertTrue(video["available"])
            self.assertIsNone(video["default_provider"])
            future = next(p for p in video["providers"] if p["id"] == "test-provider")
            self.assertEqual(future["default_model"], "test-alternate")
            self.assertEqual(future["models"][0]["use_cases"], ["draft"])
            with self.sessions() as db:
                with self.assertRaises(VideoProviderConfigurationError):
                    resolve_video_selection(db, MediaGenerationRequest(
                        media_type="video", intent={"description": "test"},
                    ), tenant_id="tenant-one")

    def test_image_registry_preserves_factory_behavior(self):
        with patch.object(OpenAIImageProvider, "__init__", return_value=None) as constructor:
            provider = create_image_provider(" OPENAI ", credentials={"api_key": "test-only"}, config={})
            self.assertIsInstance(provider, IMAGE_PROVIDERS["openai"])
            constructor.assert_called_once_with(api_key="test-only")
        with self.assertRaisesRegex(ValueError, "OpenAI credential 'api_key' is required"):
            create_image_provider("openai", credentials={}, config={})
        with self.assertRaisesRegex(ValueError, "Unsupported image provider"):
            create_image_provider("unknown", credentials={}, config={})


class ImageDiscoveryGenerationConsistencyTests(ImageExecutionTestCase):
    def test_discovered_legacy_default_is_used_by_real_image_execution_path(self):
        for configured_model in (None, "gpt-image-2", "custom-legacy-model"):
            with self.subTest(configured_model=configured_model):
                with self.sessions() as db:
                    row = db.get(TenantProviderConfig, ("tenant-one", "openai"))
                    row.config_json = {"default_model": configured_model}
                    db.commit()
                response = self.client.get("/v2/media/capabilities")
                self.assertEqual(response.status_code, 200)
                discovered = response.json()["media_types"][0]["providers"][0]["default_model"]
                submission = self.submit_image()
                self.assertEqual(submission.status_code, 202)
                with self.sessions() as db:
                    job_id = db.scalar(select(Job.job_id).where(Job.status == "queued"))
                execute_job(job_id)
                resolved = self.provider.generate.call_args.args[0]
                self.assertEqual(discovered, resolve_openai_model(resolved.model))
