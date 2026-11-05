"""Google integration through the existing API, worker, image and Artifact path."""
import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_media_job_contracts import MediaJobTestCase
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.artifacts.service import create_artifact
from app.db.models import Artifact, Execution, ImageRequest, Job
from app.jobs.worker import execute_job
from app.media.providers.base import ImageProviderError
from app.tenancy.provider_configs import set_provider_config
from tests.google_support import GoogleResponse, JPEG, MODEL, PNG, TEST_CREDENTIAL, WEBP, response_body


class GoogleImageGenerationTests(MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {"TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode()}))
        self.configure()
        self.enterContext(patch("app.jobs.worker.SessionLocal", self.sessions))
        self.enterContext(patch("app.images.tasks.SessionLocal", self.sessions))
        self.enterContext(patch("app.artifacts.service.SessionLocal", self.sessions))
        self.http = self.enterContext(patch("app.media.providers.google.image.requests.post", return_value=GoogleResponse()))
        self.store = Mock(spec=["put_bytes", "exists", "iter_bytes"])
        self.store.put_bytes.return_value = "file:///isolated-test-output"
        self.store.exists.return_value = True
        self.store.iter_bytes.return_value = [PNG]
        self.enterContext(patch("app.images.tasks.artifact_store", self.store))
        self.enterContext(patch("app.api.images.artifact_store", self.store))

    def configure(self, *, provider="google", tenant="tenant-one", enabled=True, credentials=True, config=None):
        with self.sessions() as db:
            set_provider_config(
                db, tenant_id=tenant, provider=provider, enabled=enabled,
                credentials={"api_key": TEST_CREDENTIAL} if credentials else None,
                config=config if config is not None else {"default_models": {"image": MODEL}},
            )

    def submit(self, **fields):
        return self.submit_image(**{
            "interface_level": "hard", "advanced": {"provider": "google", "model": MODEL}, **fields,
        })

    def execute(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.status == "queued"))
            job_id = job.job_id
            self.assertEqual((job.tenant_id, job.user_id), ("tenant-one", "user-one"))
            self.assertEqual(job.capability, "image.generate")
            self.assertEqual(job.input_json["request_id"], response.json()["request_id"])
        execute_job(job_id)
        return job_id

    def catalog(self, host="testserver"):
        response = self.client.get("/v2/media/capabilities", headers={"host": host})
        self.assertEqual(response.status_code, 200)
        media = next(m for m in response.json()["media_types"] if m["id"] == "image")
        provider = next(p for p in media["providers"] if p["id"] == "google")
        return media, provider

    def reference(self, *, tenant="tenant-one", user="user-one", media_type="image", mime_type="image/png"):
        with self.sessions() as db:
            artifact = create_artifact(
                db, tenant_id=tenant, user_id=user, media_type=media_type, mime_type=mime_type,
                storage_uri="vercel-blob:///private-reference", filename="private-reference.png", size_bytes=len(PNG),
            )
            db.commit()
            return artifact.artifact_id

    def test_hard_google_uses_existing_projection_worker_execution_and_artifact(self):
        response = self.submit()
        job_id = self.execute(response)
        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            artifact = db.scalar(select(Artifact))
            job = db.scalar(select(Job).where(Job.job_id == job_id))
            execution = db.scalar(select(Execution))
            self.assertEqual((job.status, execution.status, image.status), ("completed", "completed", "completed"))
            self.assertEqual((execution.provider, execution.model), ("google", MODEL))
            self.assertEqual((image.model, image.artifact_id), (MODEL, artifact.artifact_id))
            self.assertEqual((artifact.tenant_id, artifact.user_id), ("tenant-one", "user-one"))
            self.assertEqual((artifact.media_type, artifact.mime_type, artifact.size_bytes), ("image", "image/png", len(PNG)))
            self.assertTrue(artifact.filename.endswith(".png"))
            self.assertEqual((artifact.metadata_json["provider"], artifact.metadata_json["model"]), ("google", MODEL))
            self.assertNotIn(TEST_CREDENTIAL, json.dumps(artifact.metadata_json))
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)
        image_response = self.client.get(f"/v1/images/{response.json()['request_id']}")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.json()["model"], MODEL)
        content = self.client.get(f"/v1/images/{response.json()['request_id']}/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers["content-type"], "image/png")
        self.assertEqual(content.content, PNG)
        execute_job(job_id)
        self.http.assert_called_once()
        self.store.put_bytes.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Execution)), 1)

    def test_default_model_discovery_and_generation_agree(self):
        for config in ({}, {"default_models": {"image": MODEL}}, {"default_model": MODEL}):
            with self.subTest(config=config):
                self.configure(config=config)
                _, provider = self.catalog()
                self.assertEqual(provider["default_model"], MODEL)
                self.execute(self.submit(advanced={"provider": "google"}))
                self.assertIn(f"/{MODEL}:generateContent", self.http.call_args.args[0])

    def test_explicit_model_overrides_tenant_default(self):
        self.configure(config={"default_models": {"image": "unsupported-default"}})
        self.execute(self.submit())
        self.http.assert_called_once()
        self.assertIn(MODEL, self.http.call_args.args[0])

    def test_explicit_provider_is_canonicalized_consistently_with_factory(self):
        self.execute(self.submit(advanced={"provider": " GOOGLE ", "model": MODEL}))
        self.http.assert_called_once()

    def test_easy_medium_explicit_selection_is_rejected_before_enqueue(self):
        for level in ("easy", "medium"):
            response = self.submit(interface_level=level)
            self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_unsupported_model_count_strategy_parameters_and_reference_count_rejected(self):
        for fields in (
            {"advanced": {"provider": "google", "model": "unsupported"}},
            {"output": {"count": 2}}, {"strategy": "variations"},
            {"advanced": {"provider": "google", "parameters": {"imageSize": "4K"}}},
            {"references": [{"artifact_id": "one"}, {"artifact_id": "two"}]},
        ):
            with self.subTest(fields=list(fields)):
                response = self.submit(**fields)
                self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Job)))
            self.assertIsNone(db.scalar(select(ImageRequest)))

    def test_reference_bytes_and_semantic_role_reach_adapter_without_private_urls(self):
        artifact_id = self.reference()
        self.execute(self.submit(references=[{"artifact_id": artifact_id, "role": "character", "strength": "strong"}]))
        parts = self.http.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), PNG)
        self.assertIn("character's identity", parts[0]["text"])
        self.assertIn("Match it closely", parts[0]["text"])
        self.assertNotIn("vercel-blob", json.dumps(parts))
        self.assertNotIn(artifact_id, json.dumps(parts))
        self.store.iter_bytes.assert_called_once_with("vercel-blob:///private-reference")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 2)  # Reference + output.

    def test_other_user_reference_rejected_before_bytes_or_http(self):
        artifact_id = self.reference(user="user-two")
        response = self.submit(references=[{"artifact_id": artifact_id}])
        with self.assertRaisesRegex(RuntimeError, "not accessible"):
            self.execute(response)
        self.http.assert_not_called()
        self.store.iter_bytes.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job)).status, "failed")
            # Existing image projection gap, documented rather than refactored here.
            self.assertEqual(db.scalar(select(ImageRequest)).status, "generating")

    def test_other_tenant_reference_rejected_before_bytes_or_http(self):
        artifact_id = self.reference(tenant="tenant-two")
        response = self.submit(references=[{"artifact_id": artifact_id}])
        with self.assertRaisesRegex(RuntimeError, "not accessible"):
            self.execute(response)
        self.http.assert_not_called()
        self.store.iter_bytes.assert_not_called()

    def test_unsupported_reference_mime_rejected_before_provider_http(self):
        artifact_id = self.reference(mime_type="image/svg+xml")
        with self.assertRaisesRegex(ValueError, "Unsupported image reference"):
            self.execute(self.submit(references=[{"artifact_id": artifact_id}]))
        self.http.assert_not_called()

    def test_non_png_artifact_filename_matches_mime(self):
        for data, mime_type, extension in ((JPEG, "image/jpeg", ".jpg"), (WEBP, "image/webp", ".webp")):
            self.http.return_value = GoogleResponse(response_body(data=data, mime_type=mime_type))
            response = self.submit()
            self.execute(response)
            with self.sessions() as db:
                image = db.scalar(select(ImageRequest).where(ImageRequest.request_id == response.json()["request_id"]))
                artifact = db.get(Artifact, image.artifact_id)
                self.assertEqual((image.mime_type, artifact.mime_type), (mime_type, mime_type))
                self.assertTrue(image.filename.endswith(extension))
                self.assertTrue(artifact.filename.endswith(extension))

    def test_http_failure_keeps_persisted_errors_sanitized(self):
        self.http.return_value = GoogleResponse({"error": {"message": TEST_CREDENTIAL}}, status=401)
        with self.assertRaises(ImageProviderError):
            self.execute(self.submit())
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(ImageRequest)).status, "failed")
            self.assertEqual(db.scalar(select(Job)).status, "failed")
            self.assertNotIn(TEST_CREDENTIAL, db.scalar(select(Execution)).error_message)
            self.assertNotIn(TEST_CREDENTIAL, db.scalar(select(Job)).error_message)
            self.assertIsNone(db.scalar(select(Artifact)))

    def test_discovery_is_automatic_safe_and_tenant_scoped(self):
        with patch("app.tenancy.provider_configs.decrypt_credentials", side_effect=AssertionError("No decryption")), patch(
            "app.media.providers.google.image.GoogleImageProvider.__init__", side_effect=AssertionError("No construction"),
        ):
            media, provider = self.catalog()
            _, other = self.catalog("other.test")
        self.assertTrue(provider["registered"])
        self.assertTrue(provider["configured"])
        self.assertTrue(provider["enabled"])
        self.assertTrue(provider["available"])
        self.assertFalse(other["configured"])
        self.assertFalse(other["available"])
        self.assertIsNone(media["default_provider"])  # Google is not an automatic route.
        model = provider["models"][0]
        self.assertEqual(model["id"], MODEL)
        self.assertEqual(model["selectable_in"], ["hard"])
        self.assertEqual(model["supported_interface_levels"], ["hard"])
        self.assertEqual(model["use_cases"], ["thumbnail", "draft", "preview"])
        self.assertEqual(model["capabilities"]["resolutions"], ["1k"])
        self.assertEqual(model["capabilities"]["max_references"], 1)
        for private in (TEST_CREDENTIAL, "encrypted_credentials", "inlineData", "responseModalities", "x-goog-api-key", "generateContent"):
            self.assertNotIn(private, json.dumps(provider))
        self.http.assert_not_called()

    def test_disabled_and_credentialless_google_are_not_available(self):
        self.configure(enabled=False)
        _, provider = self.catalog()
        self.assertTrue(provider["configured"])
        self.assertFalse(provider["available"])
        self.configure(tenant="tenant-two", credentials=False)
        _, provider = self.catalog("other.test")
        self.assertFalse(provider["configured"])
        self.assertFalse(provider["available"])

    def test_automatic_easy_medium_stay_openai_when_both_providers_configured(self):
        self.configure(provider="openai", config={"default_model": "gpt-image-2", "default_models": {"image": "ignored"}})
        client = Mock()
        client.images.generate.return_value = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode())])
        with patch("app.media.providers.openai.image.OpenAI", return_value=client):
            for level in ("easy", "medium"):
                self.execute(self.submit_image(interface_level=level))
        self.assertEqual(client.images.generate.call_count, 2)
        self.assertEqual(client.images.generate.call_args.kwargs["model"], "gpt-image-2")
        self.http.assert_not_called()
        media, _ = self.catalog()
        self.assertEqual(media["default_provider"], "openai")

    def test_google_only_tenant_is_not_silently_used_in_easy_mode(self):
        with self.assertRaisesRegex(RuntimeError, "Provider is not configured"):
            self.execute(self.submit_image())
        self.http.assert_not_called()

    def test_google_generation_is_not_publicly_dispatchable(self):
        response = self.client.post("/v2/jobs", json={
            "capability": "image.generate", "input": {"advanced": {"provider": "google", "model": MODEL}},
        })
        self.assertEqual(response.status_code, 403)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()
