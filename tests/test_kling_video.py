from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from tests import test_video_generation as fixtures
from tests.test_artifact_previews import encoded_image, encoded_video
from tests.video_support import HTTPResponse
from app.artifacts.storage.local import LocalArtifactStore
from app.core.time import utc_now
from app.db.models import Artifact, ArtifactGeneration, ArtifactPreview, DriveConnection, Execution, Job, MediaWorkflow, TenantSettings, User
from app.jobs.continuation import _claim_operation, _finalize_claim, continue_job
from app.jobs.queue import QueueCancellation
from app.jobs.video_recovery import recover_video_jobs
from app.jobs.worker import execute_job
from app.media.video_tasks import resume_video_generation
from app.tenancy.provider_configs import set_provider_config
from app.workflows import service
from ig_core.providers.kling.video import MODEL
from ig_core.providers.video import VideoProviderRejected

TASK = "987654321"
RESULT = "https://p2-kling.klingai.com/sample.mp4?signature=temporary"
SELECTION = {"provider": "kling", "model": MODEL}


class KlingIntegrationTests(fixtures.VideoTestCase):
    def configure_kling(self, *, enabled=True, config=None):
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="kling", enabled=enabled,
                                credentials={"api_key": "test-only"}, config=config or {})

    def configure_result(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        video = encoded_video(directory / "sample.mp4", 2)
        store = LocalArtifactStore(directory / "objects")
        for module in ("app.artifacts.service", "app.artifacts.previews"):
            self.enterContext(patch(module + ".artifact_store", store))
        self.download.return_value = HTTPResponse(chunks=[video])
        self.references.iter_bytes.return_value = [encoded_image(size=(320, 320))]
        return store, video

    def test_discovery_routing_and_governance(self):
        def catalog():
            video = next(media for media in self.client.get("/v2/media/capabilities").json()["media_types"]
                         if media["id"] == "video")
            return {provider["id"]: provider for provider in video["providers"]}

        self.assertFalse(catalog()["kling"]["available"])
        self.configure_kling()
        status = catalog()
        self.assertTrue(status["alibaba"]["available"])
        self.assertTrue(status["kling"]["available"])
        self.assertTrue(status["kling"]["models"][0]["capabilities"]["native_audio_required"])
        ali = self.submit_video().json()["job_id"]
        kling = self.submit_video(output={"aspect_ratio": "21:9", "resolution": "4k"}).json()["job_id"]
        with self.sessions() as db:
            jobs = {row.job_id: row.input_json for row in db.scalars(select(Job))}
        self.assertEqual(jobs[ali]["selection"]["provider"], "alibaba")
        self.assertFalse(jobs[ali]["request"]["output"]["audio"])
        self.assertEqual(jobs[kling]["selection"], SELECTION)
        self.assertTrue(jobs[kling]["request"]["output"]["audio"])
        self.assertEqual(self.submit_video(interface_level="hard", advanced=SELECTION).status_code, 400)
        voice = next(preset for preset in self.client.get("/v2/workflows/presets").json()
                     if preset["id"] == "voice_then_video")
        self.assertFalse(voice["steps"][1]["available"])
        self.assertFalse(voice["available"])
        self.configure_kling(config={"enabled_models": {"video": []}})
        self.assertFalse(catalog()["kling"]["available"])
        self.assertEqual(self.submit_video(interface_level="hard", advanced=SELECTION, output={}).status_code, 400)
        self.configure_kling(enabled=False)
        self.assertFalse(catalog()["kling"]["available"])
        self.http.assert_not_called()

    def prepare_operation(self, job_id):
        self.http.side_effect = None
        self.http.return_value = HTTPResponse({"code": 0, "data": {"id": TASK, "status": "submitted"}})
        execute_job(job_id)
        with self.sessions() as db:
            item = db.scalar(select(Execution).where(Execution.job_id == job_id))
            self.assertEqual(item.status, "waiting")
            self.assertEqual(item.metadata_json["provider_operation"]["operation_id"], TASK)
            return item.metadata_json["continuation"]["id"]

    def provider_success(self):
        self.http.return_value = HTTPResponse({"code": 0, "data": [{
            "id": TASK, "status": "succeeded", "outputs": [{"type": "video", "url": RESULT}],
        }]})

    def test_workflow_executes_with_recovery_artifact_preview_and_lineage(self):
        self.configure_kling()
        store, video = self.configure_result()
        self.enterContext(patch("app.artifacts.service.SessionLocal", self.sessions))
        image_id = self.image_reference()
        response = self.client.post("/v2/workflows", json={
            "preset": "single_generation", "operation": "reference_to_video", "prompt": "A quiet street",
            "image_artifact_id": image_id,
        })
        self.assertEqual(response.status_code, 202, response.text)
        workflow_id = response.json()["workflow_id"]
        with patch.object(service, "SessionLocal", self.sessions), patch.object(service, "get_job_queue", return_value=self.queue):
            execute_job(service.WORK_PREFIX + workflow_id)
            with self.sessions() as db:
                row = db.get(MediaWorkflow, workflow_id)
                job_id = row.steps["generation"]["job_ids"][0]
                row.next_check_at = utc_now() - timedelta(seconds=1)
                db.commit()
            token = self.prepare_operation(job_id)
            with self.sessions() as db:
                result = recover_video_jobs(db, queue=self.queue, now=utc_now() + timedelta(seconds=1))
            self.assertEqual(result["requeued"], 1)
            self.queue.enqueue.assert_any_call(job_id, kind="continue", continuation_id=token)
            self.provider_success()
            from app.jobs.queue import job_delivery
            from app.jobs.rq_queue import _worker_invocation
            from app.jobs.vercel_queue import _legacy_message
            from app.jobs.vercel_subscriber import _decode_job, _dispatch_job

            delivery = job_delivery(job_id, "continue", token)
            worker, arguments = _worker_invocation(delivery)
            worker(*arguments)
            _dispatch_job(_decode_job(_legacy_message(delivery)))
            execute_job(service.WORK_PREFIX + workflow_id)
        result = self.client.get(f"/v2/workflows/{workflow_id}").json()
        self.assertEqual(result["status"], "completed")
        artifact_id = result["steps"][0]["outputs"][0]["artifact_id"]
        with self.sessions() as db:
            artifact = db.get(Artifact, artifact_id)
            self.assertEqual((artifact.tenant_id, artifact.user_id, artifact.media_type),
                             ("tenant-one", "user-one", "video"))
            self.assertEqual(artifact.metadata_json["provider"], "kling")
            self.assertEqual(b"".join(store.iter_bytes(artifact.storage_uri)), video)
            self.assertEqual(db.get(ArtifactGeneration, artifact_id).job_id, job_id)
            preview = db.scalar(select(ArtifactPreview).where(ArtifactPreview.artifact_id == artifact_id))
            self.assertEqual(preview.status, "ready")
        details = self.client.get(f"/v2/artifacts/{artifact_id}").json()
        self.assertEqual(details["inputs"][0]["artifact_id"], image_id)
        self.assertNotIn("signature", str(details))
        self.assertEqual([call.args[0] for call in self.http.call_args_list], ["POST", "GET"])

    def test_late_success_does_not_publish_after_cancel(self):
        self.configure_kling()
        self.configure_result()
        job_id = self.submit_video(interface_level="hard", advanced=SELECTION, output={}).json()["job_id"]
        token = self.prepare_operation(job_id)
        claim = _claim_operation(job_id, token)
        self.assertIsNotNone(claim)
        self.provider_success()
        result = resume_video_generation(claim.operation, tenant_id=claim.tenant_id, user_id=claim.user_id)
        with patch("app.jobs.service.get_job_queue") as queue:
            queue.return_value.cancel.return_value = QueueCancellation(False, 0, "Database fence")
            self.assertEqual(self.client.post(f"/v2/jobs/{job_id}/cancel").status_code, 200)
        self.assertFalse(_finalize_claim(claim, result))
        self.assertEqual(self.client.get(f"/v2/jobs/{job_id}").json()["status"], "canceled")
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Artifact)))

    def test_drive_result_uses_owner_writer(self):
        self.configure_kling()
        self.configure_result()
        connection = str(uuid4())
        with self.sessions() as db:
            db.get(User, "user-one").artifact_storage_preference = "google_drive"
            db.add(DriveConnection(connection_id=connection, user_id="user-one", account_id="test",
                                   encrypted_refresh_token="test-only", folder_id="folder"))
            db.add(TenantSettings(tenant_id="tenant-one", google_drive_enabled=True))
            db.commit()
        job_id = self.submit_video(interface_level="hard", advanced=SELECTION, output={}).json()["job_id"]
        token = self.prepare_operation(job_id)
        self.provider_success()
        locator = f"gdrive://{connection}/sample"
        with patch("app.artifacts.storage.google_drive.GoogleDriveArtifactStore.put_bytes",
                   autospec=True, return_value=locator) as put, patch("app.jobs.continuation.generate_preview_for_artifact"):
            continue_job(job_id, token)
        self.assertEqual(put.call_args.args[0].user_id, "user-one")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Artifact)).storage_uri, locator)

    def test_definite_provider_error_is_sanitized_and_exposed_as_a_code(self):
        self.configure_kling()
        job_id = self.submit_video(interface_level="hard", advanced=SELECTION, output={}).json()["job_id"]
        self.http.side_effect = None
        self.http.return_value = HTTPResponse({"code": 1303, "message": "raw provider text"}, status=429)
        with self.assertRaises(VideoProviderRejected):
            execute_job(job_id)
        result = self.client.get(f"/v2/jobs/{job_id}").json()
        self.assertEqual(result["error_code"], "VIDEO_PROVIDER_RATE_LIMIT")
        self.assertNotIn("raw provider text", result["error_message"])
