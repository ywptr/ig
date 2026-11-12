import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select

from tests.test_media_job_contracts import MediaJobTestCase
from tests.video_support import BASE_URL, HTTPResponse, MP4, TASK_ID, TEMP_VIDEO_URL, TEST_KEY, operation_response
from app.artifacts.service import create_artifact
from app.db.models import Artifact, Execution, ImageRequest, Job
from app.jobs.continuation import _claim_operation, _finalize_claim, continue_job
from app.jobs.result import Pending
from app.jobs.worker import execute_job
from app.media.providers.alibaba.video import AlibabaWanVideoProvider
from app.media.providers.registry import create_video_provider
from app.media.providers.video import VideoProviderConfigurationError
from app.media.video_tasks import resume_video_generation
from app.tenancy.provider_configs import set_provider_config


class VideoTestCase(MediaJobTestCase):
    def create_test_engine(self):
        directory = self.enterContext(TemporaryDirectory())
        return create_engine(
            f"sqlite:///{Path(directory) / 'video.sqlite'}",
            connect_args={"check_same_thread": False},
        )

    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict("os.environ", {"TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode()}))
        self.configure()
        self.enterContext(patch("app.jobs.worker.SessionLocal", self.sessions))
        self.enterContext(patch("app.media.video_tasks.SessionLocal", self.sessions))
        self.http = self.enterContext(patch("app.media.providers.alibaba.video.requests.request"))
        self.http.side_effect = AssertionError("HTTP must be explicitly configured by the test")
        self.download = self.enterContext(patch("app.media.providers.alibaba.video.requests.get"))
        self.store = Mock(spec=["put_bytes", "iter_bytes"])
        self.store.iter_bytes.return_value = [MP4]
        self.enterContext(patch("app.artifacts.previews.artifact_store", self.store))
        self.store.put_bytes.return_value = "vercel-blob:///artifacts/video.mp4"
        self.enterContext(patch("app.artifacts.service.artifact_store", self.store))
        self.references = Mock(spec=["iter_bytes"])
        self.references.iter_bytes.return_value = [b"png"]
        self.enterContext(patch("app.media.video_service.artifact_store", self.references))

    def configure(self, config=None, credentials=None, enabled=True):
        with self.sessions() as db:
            set_provider_config(
                db, tenant_id="tenant-one", provider="alibaba", enabled=enabled,
                credentials=credentials if credentials is not None else {"api_key": TEST_KEY},
                config=config if config is not None else {"base_url": BASE_URL, "default_models": {"video": "wan3.0-video"}},
            )

    def submit_video(self, **fields):
        return self.client.post("/v2/media/generations", json={
            "media_type": "video", "intent": {"description": "A slow sunrise"},
            "output": {"duration_seconds": 5, "aspect_ratio": "9:16", "resolution": "720p", "audio": False},
            **fields,
        })

    def current_job(self):
        with self.sessions() as db:
            job = db.scalar(select(Job))
            return job.job_id, job.input_json

    def current_execution(self):
        with self.sessions() as db:
            execution = db.scalar(select(Execution))
            return execution.execution_id, execution.metadata_json

    def start_video(self, **fields):
        response = self.submit_video(**fields)
        self.assertEqual(response.status_code, 202, response.text)
        self.http.side_effect = None
        self.http.return_value = operation_response()
        job_id = response.json()["job_id"]
        execute_job(job_id)
        execution_id, metadata = self.current_execution()
        return job_id, execution_id, metadata["continuation"]["id"]

    def image_reference(self, *, tenant_id="tenant-one", user_id="user-one", media_type="image", mime_type="image/png"):
        with self.sessions() as db:
            artifact = create_artifact(
                db, tenant_id=tenant_id, user_id=user_id, media_type=media_type,
                mime_type=mime_type, storage_uri="file:///private/reference.png", size_bytes=3,
            )
            db.commit()
            return artifact.artifact_id


class GenericVideoTests(VideoTestCase):
    def test_text_video_creates_one_trusted_job_and_no_image_projection(self):
        response = self.submit_video()
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["media_type"], "video")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["request_id"], body["job_id"])
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)), 1)
            self.assertIsNone(db.scalar(select(ImageRequest)))
            job = db.scalar(select(Job))
            self.assertEqual((job.tenant_id, job.user_id), ("tenant-one", "user-one"))
            self.assertEqual(job.capability, "video.generate")
            self.assertEqual(job.input_json["request"]["output"]["aspect_ratio"], "9:16")
        self.queue.enqueue.assert_called_once_with(body["job_id"])
        self.http.assert_not_called()
        self.assertEqual(self.client.get(f"/v2/jobs/{body['job_id']}").status_code, 200)

    def test_video_generation_is_internal_only(self):
        response = self.client.post("/v2/jobs", json={"capability": "video.generate", "input": {}})
        self.assertEqual(response.status_code, 403)
        self.queue.enqueue.assert_not_called()

    def test_unsupported_combinations_fail_before_enqueue(self):
        cases = [
            {"output": {"count": 2}}, {"strategy": "compare"}, {"strategy": "variations"},
            {"output": {"layout": {"type": "panel_grid", "panel_count": 4}}},
            {"output": {"style": "anime"}}, {"output": {"quality": "high"}},
            {"output": {"duration_seconds": 1}}, {"output": {"duration_seconds": 31}},
            {"output": {"duration_seconds": 2.5}}, {"output": {"resolution": "720P"}},
            {"output": {"audio": "false"}}, {"output": {"aspect_ratio": "2:3"}},
            {"references": [{"artifact_id": "x", "role": "outfit"}]},
            {"references": [{"artifact_id": "x", "role": "first_frame"}] * 2},
            {"interface_level": "hard", "advanced": {"parameters": {"seed": 1}}},
            {"parameters": {"resolution": "720P"}},
            {"output": {"parameters": {"duration": 5}}},
            {"intent": {"description": " ", "media": []}},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                response = self.submit_video(**fields)
                self.assertIn(response.status_code, (400, 422), response.text)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_video_portrait_aspect_ratio_is_accepted_by_media_route(self):
        response = self.submit_video(output={"aspect_ratio": "9:16"})
        self.assertEqual(response.status_code, 202, response.text)
        job_id, input_data = self.current_job()
        self.assertEqual(job_id, response.json()["job_id"])
        self.assertEqual(input_data["request"]["output"]["aspect_ratio"], "9:16")
        self.queue.enqueue.assert_called_once_with(job_id)
        self.http.assert_not_called()

    def test_video_rejects_image_only_ratios_in_media_validation(self):
        for ratio in ("2:3", "4:5"):
            with self.subTest(ratio=ratio):
                response = self.submit_video(output={"aspect_ratio": ratio})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], {
                    "code": "INVALID_MEDIA_REQUEST",
                    "message": f"Unsupported video aspect ratio: {ratio}",
                })
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_image_rejects_video_only_ratio_before_creating_request_or_job(self):
        response = self.client.post("/v2/media/generations", json={
            "media_type": "image", "intent": {"description": "image"},
            "output": {"aspect_ratio": "9:16"},
        })
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], {
            "code": "INVALID_MEDIA_REQUEST",
            "message": "Unsupported image aspect ratio: 9:16",
        })
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Job)))
            self.assertIsNone(db.scalar(select(ImageRequest)))
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_existing_image_aspect_ratios_remain_accepted(self):
        # Task 9 resolves image selection before enqueue, so these image-only
        # assertions need an available image provider in the video fixture.
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                                credentials={"api_key": "test-only"}, config={"default_model": "gpt-image-2"})
        for ratio in ("2:3", "4:5", "1:1", "16:9"):
            with self.subTest(ratio=ratio):
                response = self.client.post("/v2/media/generations", json={
                    "media_type": "image", "intent": {"description": "image"},
                    "output": {"aspect_ratio": ratio},
                })
                self.assertEqual(response.status_code, 202, response.text)
        self.http.assert_not_called()

    def test_unknown_ratios_remain_schema_errors_for_both_media_types(self):
        for media_type in ("image", "video"):
            with self.subTest(media_type=media_type):
                response = self.client.post("/v2/media/generations", json={
                    "media_type": media_type, "intent": {"description": "test"},
                    "output": {"aspect_ratio": "3:2"},
                })
                self.assertEqual(response.status_code, 422, response.text)
        self.queue.enqueue.assert_not_called()

    def test_image_reference_roles_are_not_expanded(self):
        response = self.client.post("/v2/media/generations", json={
            "media_type": "image", "intent": {"description": "image"},
            "references": [{"artifact_id": "x", "role": "first_frame"}],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.queue.enqueue.assert_not_called()

    def test_first_frame_checks_tenant_user_media_and_mime_before_enqueue(self):
        for values in (
            {"tenant_id": "tenant-two"}, {"user_id": "user-two"},
            {"media_type": "video"}, {"mime_type": "image/gif"},
        ):
            with self.subTest(values=values):
                artifact_id = self.image_reference(**values)
                response = self.submit_video(references=[{"artifact_id": artifact_id, "role": "first_frame"}])
                self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_first_frame_bytes_are_resolved_privately_inside_execution(self):
        artifact_id = self.image_reference()
        self.start_video(references=[{"artifact_id": artifact_id, "role": "first_frame"}])
        payload = self.http.call_args.kwargs["json"]
        self.assertEqual(payload["input"]["media"][0]["url"], "data:image/png;base64,cG5n")
        self.assertNotIn("file:///", json.dumps(payload))
        self.references.iter_bytes.assert_called_once()

    def test_reference_access_is_rechecked_at_worker_boundary(self):
        artifact_id = self.image_reference()
        response = self.submit_video(references=[{"artifact_id": artifact_id, "role": "first_frame"}])
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            db.get(Artifact, artifact_id).user_id = "user-two"
            db.commit()
        with self.assertRaisesRegex(ValueError, "not accessible"):
            execute_job(response.json()["job_id"])
        self.http.assert_not_called()

    def test_generic_layers_do_not_contain_provider_api_mechanics(self):
        root = Path(__file__).resolve().parents[1]
        paths = [
            "app/media/schemas.py", "app/media/service.py", "app/media/video_service.py",
            "app/media/video_tasks.py", "app/media/providers/video.py", "app/jobs/worker.py",
            "app/jobs/continuation.py", "app/db/models/job.py", "app/db/models/artifact.py",
        ]
        for path in paths:
            source = (root / path).read_text()
            for forbidden in ("DashScope", "wan3.0", "video-synthesis", "task_status", '"SUCCEEDED"', '"720P"'):
                self.assertNotIn(forbidden, source, f"Provider mechanics leaked into {path}")
        provider_root = root / "app/media/providers"
        for path in provider_root.rglob("*.py"):
            self.assertNotIn("app.jobs", path.read_text(), str(path))


class VideoSelectionTests(VideoTestCase):
    def test_easy_and_medium_reject_explicit_selection(self):
        for level in ("easy", "medium"):
            for advanced in ({"provider": "alibaba"}, {"model": "wan3.0-video"}, {"provider": "alibaba", "model": "wan3.0-video"}):
                with self.subTest(level=level, advanced=advanced):
                    response = self.submit_video(interface_level=level, advanced=advanced)
                    self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()

    def test_hard_supported_provider_model_selection_succeeds(self):
        response = self.submit_video(interface_level="hard", advanced={"provider": "alibaba", "model": "wan3.0-video"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.current_job()[1]["selection"], {"provider": "alibaba", "model": "wan3.0-video"})
        self.http.assert_not_called()

    def test_hard_without_selection_uses_normal_resolution(self):
        for advanced in (None, {}, {"provider": None, "model": None}):
            with self.subTest(advanced=advanced):
                response = self.submit_video(interface_level="hard", advanced=advanced)
                self.assertEqual(response.status_code, 202, response.text)
        self.http.assert_not_called()

    def test_unknown_provider_or_model_rejected_before_enqueue(self):
        for advanced in ({"provider": "unknown"}, {"provider": "alibaba", "model": "unknown"}, {"model": "unknown"}):
            response = self.submit_video(interface_level="hard", advanced=advanced)
            self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_model_priority_explicit_then_video_default_then_adapter_default(self):
        self.configure(config={"base_url": BASE_URL, "default_models": {"video": "tenant-video"}, "default_model": "image-model"})
        with patch.object(AlibabaWanVideoProvider, "MODELS", frozenset({"wan3.0-video", "tenant-video"})):
            explicit = self.submit_video(interface_level="hard", advanced={"model": "wan3.0-video"})
            self.assertEqual(explicit.status_code, 202, explicit.text)
            with self.sessions() as db:
                job = db.scalar(select(Job).where(Job.job_id == explicit.json()["job_id"]))
                self.assertEqual(job.input_json["selection"]["model"], "wan3.0-video")
            default = self.submit_video()
            with self.sessions() as db:
                job = db.scalar(select(Job).where(Job.job_id == default.json()["job_id"]))
                self.assertEqual(job.input_json["selection"]["model"], "tenant-video")
        self.configure(config={"base_url": BASE_URL, "default_model": "image-model"})
        fallback = self.submit_video()
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.job_id == fallback.json()["job_id"]))
            self.assertEqual(job.input_json["selection"]["model"], "wan3.0-video")

    def test_missing_credentials_base_url_and_disabled_provider_fail_safely(self):
        for config, credentials, enabled in (
            ({"base_url": BASE_URL}, {}, True), ({}, {"api_key": TEST_KEY}, True),
            ({"base_url": BASE_URL}, {"api_key": TEST_KEY}, False),
        ):
            self.configure(config=config, credentials=credentials, enabled=enabled)
            response = self.submit_video()
            self.assertEqual(response.status_code, 400, response.text)
            self.assertNotIn(TEST_KEY, response.text)
        self.queue.enqueue.assert_not_called()

    def test_factory_constructs_adapter_without_http(self):
        adapter = create_video_provider("alibaba", credentials={"api_key": TEST_KEY}, config={"base_url": BASE_URL})
        self.assertIsInstance(adapter, AlibabaWanVideoProvider)
        self.assertEqual(adapter.model, "wan3.0-video")
        with self.assertRaises(VideoProviderConfigurationError):
            create_video_provider("alibaba", credentials={}, config={"base_url": BASE_URL})
        self.http.assert_not_called()


class VideoLifecycleTests(VideoTestCase):
    def test_http_to_job_pending_poll_and_one_private_video_artifact(self):
        job_id, execution_id, token = self.start_video()
        for native_state in ("PENDING", "RUNNING"):
            self.http.return_value = operation_response(native_state)
            continue_job(job_id, token)
            _, metadata = self.current_execution()
            token = metadata["continuation"]["id"]
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(Job)).status, "waiting")
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = HTTPResponse()

        def store_without_transaction(**kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            self.assertEqual(kwargs["data"], MP4)
            return "vercel-blob:///artifacts/video.mp4"

        self.store.put_bytes.side_effect = store_without_transaction
        continue_job(job_id, token)
        calls = self.http.call_count
        continue_job(job_id, token)
        execute_job(job_id)
        self.assertEqual(self.http.call_count, calls)
        self.assertEqual(sum(call.args[0] == "POST" for call in self.http.call_args_list), 1)
        self.assertTrue(all(call.args[1].endswith(f"/tasks/{TASK_ID}") for call in self.http.call_args_list[1:]))
        self.store.put_bytes.assert_called_once()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(ImageRequest)))
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)
            artifact = db.scalar(select(Artifact))
            execution = db.get(Execution, execution_id)
            self.assertEqual(execution.status, "completed")
            self.assertEqual(db.scalar(select(Job)).status, "completed")
            self.assertEqual(execution.metadata_json["artifact_id"], artifact.artifact_id)
            self.assertEqual((artifact.media_type, artifact.mime_type), ("video", "video/mp4"))
            self.assertTrue(artifact.filename.endswith(".mp4"))
            self.assertEqual(artifact.size_bytes, len(MP4))
            self.assertEqual((artifact.tenant_id, artifact.user_id), ("tenant-one", "user-one"))
            self.assertEqual(artifact.metadata_json["provider"], "alibaba")
            self.assertEqual(artifact.metadata_json["model"], "wan3.0-video")
            self.assertTrue(artifact.storage_uri.startswith("vercel-blob:"))
            persisted = json.dumps([artifact.metadata_json, execution.metadata_json])
            for secret in (TEST_KEY, TEMP_VIDEO_URL, "temporary-secret", "task_status"):
                self.assertNotIn(secret, persisted)

    def test_provider_terminal_failure_finalizes_failed_without_replacement_submission(self):
        for native_state in ("FAILED", "CANCELED", "UNKNOWN"):
            # Each subtest uses a fresh logical job; no retries submit another task.
            with self.subTest(native_state=native_state):
                response = self.submit_video()
                job_id = response.json()["job_id"]
                self.http.side_effect = None
                self.http.return_value = operation_response()
                execute_job(job_id)
                with self.sessions() as db:
                    execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
                    token = execution.metadata_json["continuation"]["id"]
                self.http.return_value = operation_response(native_state, message=TEST_KEY)
                continue_job(job_id, token)
                calls = self.http.call_count
                continue_job(job_id, token)
                self.assertEqual(self.http.call_count, calls)
                with self.sessions() as db:
                    execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
                    self.assertEqual(execution.status, "failed")
                    self.assertEqual(db.scalar(select(Job).where(Job.job_id == job_id)).status, "failed")
                    self.assertNotIn("claim_id", execution.metadata_json["continuation"])
                    self.assertNotIn(TEST_KEY, execution.error_message)
        self.store.put_bytes.assert_not_called()
        self.download.assert_not_called()

    def test_stale_completion_cannot_create_a_second_artifact(self):
        job_id, execution_id, token = self.start_video()
        old = _claim_operation(job_id, token)
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = HTTPResponse()
        result = resume_video_generation(old.operation, tenant_id=old.tenant_id, user_id=old.user_id)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Artifact)))  # Handler only downloads.
            expires = db.get(Execution, execution_id).metadata_json["continuation"]["lease_expires_at"]
        with patch("app.jobs.continuation._utc_now", return_value=datetime.fromisoformat(expires) + timedelta(seconds=1)):
            current = _claim_operation(job_id, token)
        self.assertTrue(_finalize_claim(current, result))
        self.assertFalse(_finalize_claim(old, result))
        self.store.put_bytes.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)), 1)

    def test_claim_is_rechecked_after_storage_upload(self):
        job_id, execution_id, token = self.start_video()
        old = _claim_operation(job_id, token)
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = HTTPResponse()
        result = resume_video_generation(old.operation, tenant_id=old.tenant_id, user_id=old.user_id)
        with self.sessions() as db:
            expires = db.get(Execution, execution_id).metadata_json["continuation"]["lease_expires_at"]

        def replace_claim_during_upload(**kwargs):
            with patch("app.jobs.continuation._utc_now", return_value=datetime.fromisoformat(expires) + timedelta(seconds=1)):
                newer = _claim_operation(job_id, token)
            self.assertTrue(_finalize_claim(newer, Pending(newer.operation.provider, newer.operation.model, newer.operation.operation_id)))
            return "vercel-blob:///orphaned-stale-output.mp4"

        self.store.put_bytes.side_effect = replace_claim_during_upload
        self.assertFalse(_finalize_claim(old, result))
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Artifact)))
            self.assertEqual(db.scalar(select(Job)).status, "waiting")
