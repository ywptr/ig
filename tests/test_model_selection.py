from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.core.time import utc_now
from app.db.models import Job, MediaWorkflow
from app.jobs.continuation import continue_job
from app.jobs.worker import execute_job
from app.tenancy.provider_configs import set_provider_config
from app.workflows import service
from ig_core.providers.kling.video import MODEL
from tests import test_video_generation as fixtures
from tests.video_support import HTTPResponse

Kling = {"provider": "kling", "model": MODEL}
Alibaba = {"provider": "alibaba", "model": "wan3.0-video"}


class ModelSelectionTests(fixtures.VideoTestCase):
    def kling(self, *, allow=True):
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="kling", enabled=True,
                                credentials={"api_key": "test-only"},
                                config={} if allow else {"enabled_models": {"video": []}})

    def preview(self, policy=None, **fields):
        response = self.client.post("/v2/media/selection", json={
            "media_type": "video", "intent": {"description": "Slow pan"}, "output": {},
            **({"selection_policy": policy} if policy is not None else {}), **fields,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def codes(self, result, candidate):
        return [reason["code"] for item in result["evaluations"] if item["candidate"] == candidate
                for reason in item["reasons"]]

    def test_auto_default_and_deterministic_model_fallback(self):
        self.kling()
        self.assertEqual(self.preview()["selected"], Alibaba)
        specialized = self.preview(output={"resolution": "4k", "aspect_ratio": "21:9"})
        self.assertEqual(specialized["selected"], Kling)
        self.assertTrue(specialized["fallback_used"])
        self.assertEqual(specialized["rejected"][0]["candidate"], Alibaba)
        self.configure(enabled=False)
        self.assertEqual(self.preview()["selected"], Kling)
        self.http.assert_not_called()

    def test_preferred_order_and_fallback(self):
        policy = {"mode": "PREFERRED", "candidates": [{"model": MODEL}, Alibaba]}
        result = self.preview(policy)
        self.assertEqual(result["selected"], Alibaba)
        self.assertEqual(result["rejected"][0]["reasons"][0]["code"], "PROVIDER_NOT_CONFIGURED")
        self.assertFalse(result["fallback_used"])
        self.kling()
        self.assertEqual(self.preview(policy)["selected"], Kling)
        self.kling(allow=False)
        policy["candidates"].pop()
        declined = self.preview(policy)
        self.assertIsNone(declined["selected"])
        self.assertEqual(declined["policy"]["candidates"], [{"provider": None, "model": MODEL}])
        self.assertIn("MODEL_DISABLED", self.codes(declined, Kling))
        result = self.preview({**policy, "allow_fallback": True})
        self.assertEqual(result["selected"], Alibaba)
        self.assertTrue(result["fallback_used"])
        self.http.assert_not_called()

    def test_locked_never_substitutes_and_reports_constraints(self):
        policy = {"mode": "LOCKED", "candidates": [Kling]}
        self.assertIsNone(self.preview(policy)["selected"])
        self.kling()
        self.assertEqual(self.preview(policy)["selected"], Kling)
        for output, code in (
            ({"aspect_ratio": "1:2"}, "ASPECT_RATIO_UNSUPPORTED"),
            ({"duration_seconds": 1}, "DURATION_UNSUPPORTED"),
            ({"audio": False}, "NATIVE_AUDIO_REQUIRED"),
        ):
            with self.subTest(output=output):
                result = self.preview(policy, output=output)
                self.assertIsNone(result["selected"])
                self.assertIn(code, self.codes(result, Kling))
        result = self.preview(policy, references=[{"artifact_id": "audio", "role": "voice_track"}])
        self.assertIn("OPERATION_UNSUPPORTED", self.codes(result, Kling))
        frame = self.image_reference()
        result = self.preview(policy, references=[{"artifact_id": frame, "role": "first_frame"}] * 2)
        self.assertIn("REFERENCE_COUNT_UNSUPPORTED", self.codes(result, Kling))
        self.user_id = "user-two"
        result = self.preview(policy, references=[{"artifact_id": frame, "role": "first_frame"}])
        self.assertIn("REFERENCE_INCOMPATIBLE", self.codes(result, Kling))

    def test_job_and_poll_keep_accepted_model_after_disable(self):
        self.kling()
        policy = {"mode": "LOCKED", "candidates": [Kling]}
        response = self.submit_video(selection_policy=policy, output={})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        job_input = self.client.get(f"/v2/jobs/{job_id}").json()["input"]
        self.assertEqual(job_input["selection"], Kling)
        self.assertEqual(job_input["selection_audit"]["policy"]["mode"], "LOCKED")
        self.kling(allow=False)
        unavailable = self.submit_video(selection_policy=policy, output={})
        self.assertEqual(unavailable.status_code, 400, unavailable.text)
        self.assertEqual(unavailable.json()["detail"]["code"], "NO_ELIGIBLE_MODEL")
        self.http.side_effect = None
        self.http.return_value = HTTPResponse({"code": 0, "data": {"id": "987", "status": "submitted"}})
        execute_job(job_id)
        execute_job(job_id)
        self.http.assert_called_once()
        _, execution = self.current_execution()
        self.http.return_value = HTTPResponse({"code": 0, "data": [{"id": "987", "status": "processing"}]})
        continue_job(job_id, execution["continuation"]["id"])
        self.assertEqual(self.http.call_count, 2)
        self.assertIn("987", str(self.http.call_args))
        self.assertEqual(self.current_job()[1]["selection"], Kling)

    def test_workflow_modes_and_retry_keep_accepted_selection(self):
        self.kling()
        for policy, expected in (
            ({"mode": "AUTO"}, Alibaba),
            ({"mode": "PREFERRED", "candidates": [Kling, Alibaba]}, Kling),
            ({"mode": "LOCKED", "candidates": [Kling]}, Kling),
        ):
            with self.subTest(mode=policy["mode"]), patch.object(service, "SessionLocal", self.sessions), \
                    patch.object(service, "get_job_queue", return_value=self.queue):
                response = self.client.post("/v2/workflows", json={
                    "preset": "single_generation", "operation": "text_to_video",
                    "prompt": "Pan left", "selection_policy": policy,
                })
                self.assertEqual(response.status_code, 202, response.text)
                workflow_id = response.json()["workflow_id"]
                service.advance_workflow(workflow_id)
                view = self.client.get(f"/v2/workflows/{workflow_id}").json()["steps"][0]
                self.assertEqual(view["selection_resolution"]["selected"], expected)
                self.assertEqual(view["selection_resolution"]["policy"]["mode"], policy["mode"])
                if policy["mode"] != "PREFERRED":
                    continue
                with self.sessions() as db:
                    row = db.get(MediaWorkflow, workflow_id)
                    job = db.scalar(select(Job).where(Job.job_id == view["job_ids"][0]))
                    job.status, job.error_message = "failed", "VIDEO_PROVIDER_FAILED"
                    row.next_check_at = utc_now() - timedelta(seconds=1)
                    db.commit()
                service.advance_workflow(workflow_id)
                self.kling(allow=False)
                response = self.client.post(f"/v2/workflows/{workflow_id}/retry")
                self.assertEqual(response.status_code, 202, response.text)
                service.advance_workflow(workflow_id)
                view = self.client.get(f"/v2/workflows/{workflow_id}").json()["steps"][0]
                self.assertEqual(len(view["job_ids"]), 2, view)
                self.assertEqual(view["selection_resolution"]["policy"]["mode"], "PREFERRED")
                with self.sessions() as db:
                    self.assertEqual(db.scalar(select(Job).where(Job.job_id == view["job_ids"][1])).input_json["selection"], Kling)
                self.kling()

    def test_openai_catalog_supports_image_and_speech_with_shared_contract(self):
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                                credentials={"api_key": "test-only"}, config={})
        specs = (("image", {}), ("audio", {"operation": "text_to_speech", "output": {"voice": "marin"}}))
        for medium, fields in specs:
            with self.subTest(media=medium):
                assessment = self.preview(media_type=medium, **fields)
                selected = assessment["selected"]
                self.assertEqual(selected["provider"], "openai")
                response = self.client.post("/v2/media/generations", json={
                    "media_type": medium, "intent": {"description": "Hello"}, **fields,
                    "selection_policy": {"mode": "LOCKED", "candidates": [selected]},
                })
                self.assertEqual(response.status_code, 202, response.text)
        self.http.assert_not_called()
