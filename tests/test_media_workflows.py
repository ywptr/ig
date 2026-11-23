from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4
import wave

from sqlalchemy import select

from tests import test_media_job_contracts as fixtures
from app.artifacts.lineage import inputs_for, record_generation
from app.core.time import utc_now
from app.db.models import Artifact, Job, MediaWorkflow
from app.executions.context import current_execution
from app.jobs.handlers import HANDLERS
from app.jobs.service import create_job
from app.jobs.worker import execute_job
from app.workflows import service
from ig_core.execution.result import Completed
from ig_storage import LocalConfig, LocalStore

SINGLE = {"preset": "single_generation", "operation": "text_to_image", "prompt": "Paint a cloud"}
VOICE = {"preset": "voice_then_video", "prompt": "A wide shot", "dialogue": "Hello", "image_artifact_id": "frame"}
CHOICE = {"provider": "testing", "model": "test", "label": "Test only"}
VOICE_CHOICE = {**CHOICE, "speech": {
    "voices": [{"id": "test-voice", "label": "Test voice"}], "default_voice": "test-voice",
    "formats": [{"id": "wav", "mime_type": "audio/wav"}], "default_format": "wav",
    "max_text_characters": 4096,
}}


class WorkflowTests(fixtures.MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.queue = Mock(spec=["enqueue", "cancel"])
        self.enterContext(patch("app.workflows.service.get_job_queue", return_value=self.queue))
        self.enterContext(patch("app.workflows.service.SessionLocal", self.sessions))
        self.enterContext(patch("app.jobs.worker.SessionLocal", self.sessions))
        self.enterContext(patch("app.workflows.presets.model_choices", return_value={
            "text_to_image": [CHOICE], "text_to_speech": [VOICE_CHOICE], "image_audio_to_video": [CHOICE],
        }))
        self.enterContext(patch("app.workflows.service.submit_step", side_effect=self.create_test_job))
        self.enterContext(patch.dict(HANDLERS, {"test.workflow": self.fake_adapter}))
        local = Path(self.enterContext(TemporaryDirectory()))
        self.store = LocalStore(LocalConfig(local))
        self.results = []
        self.fail_video = False
        with self.sessions() as db:
            db.add(Artifact(artifact_id="frame", tenant_id="tenant-one", user_id="user-one",
                            media_type="image", mime_type="image/png", storage_uri="local:frame"))
            db.commit()

    def create_test_job(self, db, row, step, references):
        return create_job(db, "test.workflow", {"media_type": step.output, "references": references},
                          row.tenant_id, row.user_id, defer_dispatch=True).job_id

    def fake_adapter(self, payload, *, tenant_id, user_id):
        # The test handler is registered only while a fixture owns the workers.
        kind = payload["media_type"]
        self.results.append(kind)
        if self.fail_video and kind == "video":
            raise ValueError("Test adapter failed")
        if kind == "audio":
            data = BytesIO()
            with wave.open(data, "wb") as output:
                output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                output.writeframes(bytes(160))
            self.audio = data.getvalue()
            locator = self.store.put_bytes(filename="voice.wav", data=self.audio)
        else:
            locator = f"local:{kind}"
        identity = current_execution()
        with self.sessions() as db:
            artifact = Artifact(artifact_id=str(uuid4()), tenant_id=tenant_id, user_id=user_id, media_type=kind,
                                storage_uri=locator, mime_type={"audio": "audio/wav", "image": "image/png",
                                                                        "video": "video/mp4"}[kind])
            db.add(artifact)
            db.flush()
            record_generation(db, artifact, job_id=identity.job_id, execution_id=identity.execution_id,
                              inputs=payload["references"])
            db.commit()
        return Completed()

    def submit(self, data=SINGLE, key=None):
        response = self.client.post("/v2/workflows", json=data,
                                    headers={"Idempotency-Key": key} if key else {})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["workflow_id"]

    def inspect(self, workflow_id):
        response = self.client.get(f"/v2/workflows/{workflow_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def tick(self, workflow_id):
        with self.sessions() as db:
            row = db.get(MediaWorkflow, workflow_id)
            if row.next_check_at:
                row.next_check_at = utc_now() - timedelta(seconds=1)
                db.commit()
        execute_job(service.WORK_PREFIX + workflow_id)
        execute_job(service.WORK_PREFIX + workflow_id)
        return self.inspect(workflow_id)

    def test_one_step_and_duplicate_delivery(self):
        workflow = self.submit(key="same")
        self.assertEqual(workflow, self.submit(key="same"))
        self.assertEqual(self.client.post("/v2/workflows", json={**SINGLE, "prompt": "Other"},
                                          headers={"Idempotency-Key": "same"}).status_code, 409)
        job = self.tick(workflow)["steps"][0]["job_ids"][0]
        execute_job(job)
        execute_job(job)
        view = self.tick(workflow)
        self.assertEqual(view["status"], "completed")
        self.assertEqual(view["steps"][0]["job_ids"], [job])
        self.assertEqual(self.results, ["image"])
        self.assertEqual(len(view["steps"][0]["outputs"]), 1)

    def test_chaining_failure_and_retry_preserve_lineage(self):
        workflow = self.submit(VOICE)
        view = self.tick(workflow)
        voice = view["steps"][0]["job_ids"][0]
        self.assertEqual(view["steps"][1]["job_ids"], [])
        execute_job(voice)
        self.assertEqual(self.audio[:4], b"RIFF")
        view = self.tick(workflow)
        audio_id = view["steps"][0]["outputs"][0]["artifact_id"]
        with self.sessions() as db:
            locator = db.get(Artifact, audio_id).storage_uri
        with wave.open(BytesIO(b"".join(self.store.iter_bytes(locator))), "rb") as audio:
            self.assertEqual(audio.getnframes(), 80)
        video = view["steps"][1]["job_ids"][0]
        self.fail_video = True
        with self.assertRaisesRegex(ValueError, "Test adapter failed"):
            execute_job(video)
        view = self.tick(workflow)
        self.assertEqual(view["status"], "failed")
        self.assertEqual(self.client.post(f"/v2/workflows/{workflow}/retry").status_code, 202)
        view = self.tick(workflow)
        retry = view["steps"][1]["job_ids"][-1]
        self.assertEqual(view["steps"][1]["job_ids"], [video, retry])
        self.fail_video = False
        execute_job(retry)
        view = self.tick(workflow)
        self.assertEqual(view["status"], "completed")
        self.assertEqual(self.results, ["audio", "video", "video"])
        self.assertEqual(view["steps"][0]["outputs"][0]["artifact_id"], audio_id)
        with self.sessions() as db:
            artifact = db.get(Artifact, view["steps"][1]["outputs"][0]["artifact_id"])
            self.assertEqual({row["role"]: row["artifact_id"] for row in inputs_for(db, artifact)},
                             {"first_frame": "frame", "voice_track": audio_id})

    def test_failed_voice_blocks_video(self):
        workflow = self.submit(VOICE)
        voice = self.tick(workflow)["steps"][0]["job_ids"][0]
        with patch.dict(HANDLERS, {"test.workflow": Mock(side_effect=ValueError("voice failed"))}):
            with self.assertRaises(ValueError):
                execute_job(voice)
        view = self.tick(workflow)
        self.assertEqual(view["status"], "failed")
        self.assertEqual(view["steps"][1]["status"], "blocked")
        self.assertEqual(view["steps"][1]["job_ids"], [])

    def test_cancel_before_first_step_and_after_completed_output(self):
        workflow = self.submit(VOICE)
        self.assertEqual(self.client.post(f"/v2/workflows/{workflow}/cancel").status_code, 202)
        view = self.tick(workflow)
        self.assertEqual(view["status"], "canceled")
        self.assertEqual([step["job_ids"] for step in view["steps"]], [[], []])
        workflow = self.submit(VOICE)
        job = self.tick(workflow)["steps"][0]["job_ids"][0]
        execute_job(job)
        self.client.post(f"/v2/workflows/{workflow}/cancel")
        view = self.tick(workflow)
        self.assertEqual(view["status"], "canceled")
        self.assertEqual(view["steps"][0]["status"], "completed")
        self.assertEqual(len(view["steps"][0]["outputs"]), 1)
        self.assertEqual(view["steps"][1]["job_ids"], [])

    def test_cancel_active_job_and_prevent_future_steps(self):
        workflow = self.submit(VOICE)
        job_id = self.tick(workflow)["steps"][0]["job_ids"][0]
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.job_id == job_id))
            job.status, job.started_at = "running", utc_now()
            db.commit()
        self.assertEqual(self.client.post(f"/v2/workflows/{workflow}/cancel").status_code, 202)
        self.assertEqual(self.tick(workflow)["status"], "cancel_requested")
        with self.sessions() as db:
            service.acknowledge_cancellation(db, job_id)
            db.commit()
        view = self.tick(workflow)
        self.assertEqual(view["status"], "canceled")
        self.assertEqual(view["steps"][1]["job_ids"], [])

    def test_owner_scope_and_delegation(self):
        workflow = self.submit()
        self.user_id = "user-two"
        path = f"/v2/workflows/{workflow}"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(path + "/cancel").status_code, 404)
        self.assertEqual(self.client.post(path + "/retry").status_code, 404)
        self.user_id = "user-one"
        self.assertEqual(self.client.get("http://other.test" + path).status_code, 404)
        from app.auth.principal import RequestPrincipal
        from app.tenancy.dependencies import get_authenticated_tenant_context
        fixtures.app.dependency_overrides[get_authenticated_tenant_context] = lambda: RequestPrincipal(
            tenant=None, caller_type="service")
        self.assertEqual(self.client.get(path).status_code, 403)

    def test_recovery_and_delayed_wakes(self):
        workflow = self.submit()
        with self.sessions() as db:
            self.assertEqual(service.recover_workflows(db), {"scanned": 1, "published": 1})
        view = self.tick(workflow)
        job_id = view["steps"][0]["job_ids"][0]
        self.queue.enqueue.assert_any_call("workflow:" + workflow, kind="recover", delay_seconds=30)
        self.queue.reset_mock()
        self.tick(workflow)
        self.queue.enqueue.assert_any_call(job_id, kind="recover")
        with self.sessions() as db:
            self.assertEqual(len(list(db.scalars(select(Job)))), 1)

    def test_unavailable_preset(self):
        with patch("app.workflows.presets.model_choices", return_value={
            op: [] for op in ("text_to_image", "text_to_video", "image_to_image", "image_to_video",
                             "text_to_speech", "image_audio_to_video", "reference_to_video")
        }):
            catalog = self.client.get("/v2/workflows/presets").json()
            self.assertFalse(catalog[1]["available"])
            self.assertIn("text_to_speech", catalog[1]["reason"])
            self.assertEqual(self.client.post("/v2/workflows", json=VOICE).status_code, 400)


class WorkflowMediaAdmissionTests(fixtures.MediaJobTestCase):
    def test_catalog_and_real_job_admission(self):
        from cryptography.fernet import Fernet
        from app.tenancy.provider_configs import set_provider_config

        with patch.dict("os.environ", {"TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            with self.sessions() as db:
                set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                                    credentials={"api_key": "test-only"}, config={"default_model": "gpt-image-2"})
            with patch.object(service, "get_job_queue", return_value=self.queue), patch.object(
                service, "SessionLocal", self.sessions
            ):
                catalog = self.client.get("/v2/workflows/presets").json()
                self.assertTrue(catalog[0]["available"])
                self.assertFalse(catalog[1]["available"])
                response = self.client.post("/v2/workflows", json=SINGLE)
                self.assertEqual(response.status_code, 202, response.text)
                workflow_id = response.json()["workflow_id"]
                execute_job(service.WORK_PREFIX + workflow_id)
                with self.sessions() as db:
                    row = db.get(MediaWorkflow, workflow_id)
                    job = db.scalar(select(Job).where(Job.job_id == row.steps["generation"]["job_ids"][0]))
                    self.assertEqual(job.capability, "image.generate")
                    self.assertEqual(job.input_json["selection"]["model"], "gpt-image-2")


class WorkflowDeliveryTests(TestCase):
    def test_rq_and_vercel_use_existing_worker_entry_point(self):
        from app.jobs.queue import job_delivery
        from app.jobs.rq_queue import _worker_invocation
        from app.jobs.vercel_queue import _legacy_message
        from app.jobs.vercel_subscriber import _decode_job, _dispatch_job

        locator = service.WORK_PREFIX + str(uuid4())
        first = job_delivery(locator, "recover", None)
        second = job_delivery(locator, "recover", None)
        self.assertNotEqual(first.delivery_id, second.delivery_id)
        handler, args = _worker_invocation(first)
        self.assertIs(handler, execute_job)
        with patch.object(service, "advance_workflow") as advance:
            handler(*args)
            _dispatch_job(_decode_job(_legacy_message(first)))
        self.assertEqual([call.args for call in advance.call_args_list], [(locator.removeprefix(service.WORK_PREFIX),)] * 2)
