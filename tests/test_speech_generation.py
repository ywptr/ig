from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4
import wave

from cryptography.fernet import Fernet
from sqlalchemy import select

from tests import test_media_job_contracts as fixtures
from app.core.time import utc_now
from app.artifacts.retention import eligible
from app.db.models import Artifact, ArtifactGeneration, DriveConnection, Execution, Job, MediaWorkflow, TenantSettings, User
from app.jobs.queue import QueueCancellation
from app.jobs.worker import execute_job
from app.tenancy.provider_configs import set_provider_config
from app.workflows import service
from ig_core.providers.openai.speech import MODEL
from ig_storage import LocalConfig, LocalStore

BODY = {
    "media_type": "audio", "operation": "text_to_speech", "intent": {"description": "Hello."},
    "output": {"voice": "marin", "output_format": "wav"},
}


class SpeechGenerationTests(fixtures.MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.queue = Mock(spec=["enqueue", "cancel"])
        self.queue.cancel.return_value = QueueCancellation(False, 0, "Database fence")
        for target in ("app.jobs.service", "app.workflows.service"):
            self.enterContext(patch(target + ".get_job_queue", return_value=self.queue))
        for target in ("app.jobs.worker", "app.media.speech", "app.workflows.service", "app.artifacts.service"):
            self.enterContext(patch(target + ".SessionLocal", self.sessions))
        self.enterContext(patch.dict("os.environ", {"TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode()}))
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                                credentials={"api_key": "test-only"}, config={})
        self.store = LocalStore(LocalConfig(Path(self.enterContext(TemporaryDirectory()))))
        for target in ("app.jobs.worker.artifact_store", "app.api.artifacts.artifact_store"):
            self.enterContext(patch(target, self.store))
        data = BytesIO()
        with wave.open(data, "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(1600))
        self.audio = data.getvalue()
        self.provider = self.enterContext(patch("ig_core.providers.openai.speech.requests.post"))
        response = self.provider.return_value.__enter__.return_value
        response.status_code = 200
        response.headers = {"Content-Type": "audio/wav"}
        response.iter_content.return_value = [self.audio]

    def submit(self, body=BODY):
        response = self.client.post("/v2/media/generations", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["job_id"]

    def test_persists_and_serves_audio_once_with_lineage(self):
        job_id = self.submit()
        execute_job(job_id)
        execute_job(job_id)
        self.provider.assert_called_once()
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.job_id == job_id))
            execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
            artifact = db.scalar(select(Artifact))
            self.assertEqual((job.status, execution.status), ("completed", "completed"))
            self.assertEqual((execution.provider, execution.model), ("openai", MODEL))
            self.assertEqual((artifact.tenant_id, artifact.user_id), ("tenant-one", "user-one"))
            self.assertEqual((artifact.media_type, artifact.mime_type, artifact.size_bytes), ("audio", "audio/wav", len(self.audio)))
            self.assertAlmostEqual(artifact.duration_seconds, 0.1)
            self.assertEqual(artifact.metadata_json["sample_rate"], 8000)
            self.assertEqual(artifact.provenance.value, "generated")
            self.assertEqual(db.get(ArtifactGeneration, artifact.artifact_id).job_id, job_id)
            artifact_id = artifact.artifact_id
        url = f"/v2/artifacts/{artifact_id}"
        metadata = self.client.get(url).json()
        self.assertNotIn("storage_uri", metadata)
        self.assertEqual(metadata["preview"]["status"], "unsupported")
        content = self.client.get(url + "/content")
        self.assertEqual(content.content, self.audio)
        self.assertEqual(content.headers["content-type"], "audio/wav")
        self.user_id = "user-two"
        self.assertEqual(self.client.get(url + "/content").status_code, 404)

    def test_drive_writer_and_existing_retention_policy(self):
        connection = str(uuid4())
        with self.sessions() as db:
            db.get(User, "user-one").artifact_storage_preference = "google_drive"
            db.add(DriveConnection(connection_id=connection, user_id="user-one", account_id="test",
                                   encrypted_refresh_token="test-only", folder_id="folder"))
            db.add(TenantSettings(tenant_id="tenant-one", google_drive_enabled=True,
                                  generated_artifact_retention_enabled=True, generated_artifact_retention_days=1))
            db.commit()
        job_id = self.submit()
        locator = f"gdrive://{connection}/file"
        with patch("app.artifacts.storage.google_drive.GoogleDriveArtifactStore.put_bytes", autospec=True,
                   return_value=locator) as put:
            execute_job(job_id)
        writer = put.call_args.args[0]
        self.assertEqual((writer.connection_id, writer.tenant_id, writer.user_id), (connection, "tenant-one", "user-one"))
        self.assertEqual(put.call_args.kwargs["data"], self.audio)
        with self.sessions() as db:
            artifact = db.scalar(select(Artifact))
            self.assertEqual(artifact.storage_uri, locator)
            later = utc_now() + timedelta(days=40)
            self.assertFalse(eligible(db, artifact, later))
            settings = db.get(TenantSettings, "tenant-one")
            settings.external_generated_retention_enabled = True
            settings.external_generated_retention_days = 1
            db.commit()
            self.assertTrue(eligible(db, artifact, later))

    def test_queue_entry_points_share_speech_worker(self):
        from app.jobs.queue import job_delivery
        from app.jobs.rq_queue import _worker_invocation
        from app.jobs.vercel_queue import _legacy_message
        from app.jobs.vercel_subscriber import _decode_job, _dispatch_job

        receipt = job_delivery(self.submit(), "initial", None)
        worker, args = _worker_invocation(receipt)
        worker(*args)
        _dispatch_job(_decode_job(_legacy_message(receipt)))
        self.provider.assert_called_once()

    def test_canceled_queued_job_never_calls_provider(self):
        job = self.submit()
        self.assertEqual(self.client.post(f"/v2/jobs/{job}/cancel").status_code, 200)
        execute_job(job)
        self.provider.assert_not_called()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Artifact)))

    def test_one_step_workflow_executes_and_video_stays_unavailable(self):
        presets = self.client.get("/v2/workflows/presets").json()
        voice, video = presets[1]["steps"]
        self.assertTrue(voice["available"])
        self.assertFalse(video["available"])
        self.assertFalse(presets[1]["available"])
        response = self.client.post("/v2/workflows", json={
            "preset": "single_generation", "operation": "text_to_speech", "prompt": "Hello.",
            "speech": {"voice": "cedar", "output_format": "wav"},
        })
        self.assertEqual(response.status_code, 202, response.text)
        workflow_id = response.json()["workflow_id"]
        execute_job(service.WORK_PREFIX + workflow_id)
        with self.sessions() as db:
            row = db.get(MediaWorkflow, workflow_id)
            job_id = row.steps["generation"]["job_ids"][0]
            row.next_check_at = utc_now() - timedelta(seconds=1)
            db.commit()
        execute_job(job_id)
        execute_job(service.WORK_PREFIX + workflow_id)
        workflow = self.client.get(f"/v2/workflows/{workflow_id}").json()
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(workflow["steps"][0]["speech"]["voice"], "cedar")
        self.assertEqual(workflow["steps"][0]["outputs"][0]["media_type"], "audio")

    def test_invalid_inputs_and_model_governance(self):
        for output in ({"voice": "unknown"}, {"output_format": "m4a"}):
            response = self.client.post("/v2/media/generations", json={**BODY, "output": output})
            self.assertEqual(response.status_code, 400, response.text)
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                                config={"enabled_models": {"audio": []}})
        self.assertEqual(self.client.post("/v2/media/generations", json=BODY).status_code, 400)
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Job)))
        self.provider.assert_not_called()

    def test_workflow_can_dispatch_a_different_registered_speech_adapter(self):
        from dataclasses import replace

        from app.media.providers.registry import SPEECH_PROVIDERS
        from ig_core.artifacts.types import ArtifactPayload
        from ig_core.media.catalog import ProviderMetadata
        from ig_core.media.speech import VoiceSelection
        from ig_core.providers.openai.speech import OpenAISpeechProvider

        fixture_audio = self.audio
        original = OpenAISpeechProvider.CAPABILITIES.models[0]
        synthetic_voice = "opaque:character-42"
        metadata = replace(original, id="synthetic", display_name="Test voice", lifecycle=type(original.lifecycle)(),
                           speech=original.speech.model_copy(update={
                               "voices": (VoiceSelection(id=synthetic_voice, label="Test voice"),),
                               "default_voice": synthetic_voice, "default_format": "wav",
                           }))

        class TestSpeech:
            DEFAULT_MODEL = "synthetic"
            CAPABILITIES = ProviderMetadata("Test Speech", (metadata,))

            @classmethod
            def configuration_is_valid(cls, config):
                return True

            @classmethod
            def from_config(cls, *, credentials, config, model):
                return cls()

            def synthesize(self, request):
                return ArtifactPayload("audio", "audio/wav", "wav", fixture_audio, {"voice": request.voice})

        with patch.dict(SPEECH_PROVIDERS, {"synthetic": TestSpeech}):
            with self.sessions() as db:
                set_provider_config(db, tenant_id="tenant-one", provider="synthetic", enabled=True,
                                    credentials={"api_key": "test-only"}, config={})
            response = self.client.post("/v2/workflows", json={
                "preset": "single_generation", "operation": "text_to_speech", "prompt": "Hello.",
                "selection": {"provider": "synthetic", "model": "synthetic"},
            })
            self.assertEqual(response.status_code, 202, response.text)
            workflow_id = response.json()["workflow_id"]
            execute_job(service.WORK_PREFIX + workflow_id)
            with self.sessions() as db:
                job_id = db.get(MediaWorkflow, workflow_id).steps["generation"]["job_ids"][0]
            execute_job(job_id)
        with self.sessions() as db:
            artifact = db.scalar(select(Artifact))
            self.assertEqual(artifact.metadata_json["provider"], "synthetic")
            self.assertEqual(artifact.metadata_json["voice"], synthetic_voice)
        self.provider.assert_not_called()
