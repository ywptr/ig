"""Real producer flows and codecs; providers mocked, no external calls."""
from dataclasses import replace
from unittest.mock import patch

from sqlalchemy import func, select

from tests import test_media_job_contracts as contracts
from tests import test_video_generation as video

from app.artifacts import previews
from app.db.models import Artifact, ArtifactPreview, Job
from app.jobs.worker import execute_job
from app.jobs.continuation import continue_job
from tests.test_artifact_storage import install_storage
from tests.test_artifact_previews import encoded_image, encoded_video
from tests.video_support import HTTPResponse, TEMP_VIDEO_URL, operation_response


class EagerImageTests(contracts.ImageExecutionTestCase):
    def setUp(self):
        super().setUp()
        self.storage = install_storage(self)
        self.enterContext(patch("app.images.tasks.artifact_store", self.storage))
        self.data = encoded_image((800, 500))
        self.provider.generate.return_value = replace(self.provider.generate.return_value,
            data=self.data, size_bytes=len(self.data))

    def generate(self):
        response = self.submit_image()
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            job_id = db.scalar(select(Job)).job_id
        execute_job(job_id)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job).where(Job.job_id == job_id)).status, "completed")
            artifact = db.scalar(select(Artifact))
            return job_id, artifact.artifact_id, artifact.storage_uri

    def test_generated_image_ready_before_list_and_get_without_processing(self):
        with patch("app.artifacts.previews.subprocess.Popen", side_effect=AssertionError("Pillow must run inline")):
            job_id, artifact_id, uri = self.generate()
        with self.sessions() as db:
            self.assertEqual(db.get(ArtifactPreview, (artifact_id, "thumbnail", 1)).status, "ready")
        with patch("app.artifacts.previews.ensure_preview", side_effect=AssertionError("GET must only read")), patch(
            "app.artifacts.previews.process_source", side_effect=AssertionError("No list processing")):
            listing = self.client.get("/v2/artifacts").json()
            self.assertEqual(listing[0]["preview"]["status"], "ready")
            self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/preview").status_code, 200)
            execute_job(job_id)
            with self.sessions() as db:
                previews.generate_preview_for_artifact(db, db.get(Artifact, artifact_id))
        self.provider.generate.assert_called_once()
        self.assertEqual(b"".join(self.storage.iter_bytes(uri)), self.data)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactPreview)), 1)

    def test_image_preview_failure_preserves_generation_and_sanitizes_logs(self):
        with patch("app.artifacts.previews.process_source", side_effect=OSError("https://private/token=SECRET")), self.assertLogs(
            "app.artifacts.previews", level="INFO") as logs:
            _, artifact_id, uri = self.generate()
        text = " ".join(logs.output)
        for value in (artifact_id, "media_type=image", "version=1", "stage=decode", "exception_class=OSError"):
            self.assertIn(value, text)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("https://private", text)
        with self.sessions() as db:
            self.assertEqual(db.get(ArtifactPreview, (artifact_id, "thumbnail", 1)).status, "failed")
        self.assertEqual(b"".join(self.storage.iter_bytes(uri)), self.data)
        with patch("app.artifacts.previews.ensure_preview", side_effect=AssertionError("Cached failure")):
            self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/preview").status_code, 422)

    def test_uploaded_image_ready_before_response_and_original_unchanged(self):
        response = self.client.post("/v2/artifacts/uploads", files={"file": ("image.png", self.data, "image/png")})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["preview"]["status"], "ready")
        artifact_id = response.json()["artifact_id"]
        with patch("app.artifacts.previews.process_source", side_effect=AssertionError("Already ready")):
            self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/preview").status_code, 200)
        self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/content").content, self.data)

    def test_upload_failure_preserves_artifact_and_selected_retry_is_idempotent(self):
        with patch("app.artifacts.previews.process_source", side_effect=ValueError("secret")):
            response = self.client.post("/v2/artifacts/uploads", files={"file": ("image.png", self.data, "image/png")})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["preview"]["status"], "failed")
        artifact_id = response.json()["artifact_id"]
        with self.sessions() as db:
            artifact = db.get(Artifact, artifact_id)
            self.assertEqual(previews.retry_failed_preview(db, artifact).status, "ready")
            with self.assertRaises(ValueError):
                previews.retry_failed_preview(db, artifact)
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactPreview)), 1)
        self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/content").content, self.data)

    def test_retry_changes_only_explicitly_selected_failed_preview(self):
        with patch("app.artifacts.previews.process_source", side_effect=ValueError("secret")):
            ids = [self.client.post("/v2/artifacts/uploads", files={
                "file": ("image.png", self.data, "image/png")}).json()["artifact_id"] for _ in range(2)]
        with self.sessions() as db:
            self.assertEqual(previews.retry_failed_preview(db, db.get(Artifact, ids[0])).status, "ready")
            self.assertEqual(db.get(ArtifactPreview, (ids[1], "thumbnail", 1)).status, "failed")
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactPreview)), 2)

    def test_preview_transaction_failure_does_not_discard_original(self):
        with patch("app.artifacts.previews.ensure_preview", side_effect=RuntimeError("private")):
            _, artifact_id, _ = self.generate()
        with self.sessions() as db:
            self.assertEqual(db.get(ArtifactPreview, (artifact_id, "thumbnail", 1)).status, "failed")


class EagerVideoTests(video.VideoTestCase):
    def setUp(self):
        super().setUp()
        self.storage = install_storage(self)
        self.data = encoded_video(self.storage.base_dir.parent / "video.mp4", 8)

    def finish(self):
        job_id, execution_id, token = self.start_video()
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.return_value = HTTPResponse(chunks=[self.data])
        continue_job(job_id, token)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job).where(Job.job_id == job_id)).status, "completed")
            artifact = db.scalar(select(Artifact))
            return job_id, token, artifact.artifact_id, artifact.storage_uri

    def test_video_ready_before_library_and_redelivery_does_not_process_again(self):
        job_id, token, artifact_id, uri = self.finish()
        with self.sessions() as db:
            row = db.get(ArtifactPreview, (artifact_id, "contact_sheet", 1))
            self.assertEqual((row.status, row.frame_count), ("ready", 3))
        calls = self.http.call_count
        with patch("app.artifacts.previews.process_source", side_effect=AssertionError("No redelivery/list processing")):
            continue_job(job_id, token)
            execute_job(job_id)
            self.assertEqual(self.client.get("/v2/artifacts").json()[0]["preview"]["status"], "ready")
            self.assertEqual(self.client.get(f"/v2/artifacts/{artifact_id}/preview").status_code, 200)
        self.assertEqual(self.http.call_count, calls)
        self.assertEqual(b"".join(self.storage.iter_bytes(uri)), self.data)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactPreview)), 1)

    def test_video_preview_failure_preserves_success(self):
        with patch("app.artifacts.previews.process_source", side_effect=ValueError("secret")):
            _, _, artifact_id, uri = self.finish()
        with self.sessions() as db:
            self.assertEqual(db.get(ArtifactPreview, (artifact_id, "contact_sheet", 1)).status, "failed")
        self.assertEqual(b"".join(self.storage.iter_bytes(uri)), self.data)
