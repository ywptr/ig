"""Crash/recovery integration over real DB state and mocked provider boundaries."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch, create_autospec

from sqlalchemy import func, select
from tests import test_video_generation as video
from tests.video_support import operation_response, HTTPResponse, MP4, TASK_ID, TEST_KEY, TEMP_VIDEO_URL
from app.core.time import utc_now, utc_iso
from app.db.models import Artifact, Execution, Job
from app.jobs.worker import execute_job
from app.jobs.continuation import _claim_operation, _finalize_claim, continue_job, schedule_continuation
from app.jobs.video_recovery import recover_video_jobs
from app.jobs.video_lifecycle import begin_submission, checkpoint_submission
from app.jobs.cancellation import JobCancelled
from app.executions.context import execution_scope
from app.jobs.result import Pending, Completed
from app.artifacts.types import ArtifactPayload
from app.media.capability_schemas import IntegerRange
from app.media.providers.alibaba.video import AlibabaWanVideoProvider


class VideoResiliencyTests(video.VideoTestCase):
    def setUp(self):
        super().setUp()
        self.http.side_effect = None
        self.http.return_value = operation_response()

    def state(self, job_id):
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.job_id == job_id))
            execution = db.scalar(select(Execution).where(Execution.job_id == job_id).order_by(Execution.attempt.desc()))
            return job.status, execution.status, deepcopy(execution.metadata_json)

    def recover(self, **options):
        with self.sessions() as db:
            return recover_video_jobs(db, queue=self.queue, **options)

    def crash_prepared(self):
        job_id = self.submit_video().json()["job_id"]
        with patch("app.jobs.worker.get_handler", side_effect=BaseException("simulated process death")):
            with self.assertRaises(BaseException): execute_job(job_id)
        with self.sessions() as db:
            execution_id = db.scalar(select(Execution.execution_id).where(Execution.job_id == job_id))
        return job_id, execution_id

    def test_prepared_crash_can_retry_but_old_owner_cannot_submit(self):
        job_id, old_id = self.crash_prepared()
        result = self.recover(now=utc_now() + timedelta(seconds=301))
        self.assertEqual(result["reset"], 1)
        self.assertEqual(self.state(job_id)[:2], ("queued", "failed"))
        self.queue.enqueue.assert_called_with(job_id, kind="recover")
        with execution_scope(job_id, old_id), self.assertRaises(JobCancelled):
            begin_submission(self.sessions)
        execute_job(job_id)
        self.assertEqual(self.state(job_id)[:2], ("waiting", "waiting"))
        self.assertEqual(sum(c.args[0] == "POST" for c in self.http.call_args_list), 1)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Execution)), 2)

    def test_post_submission_crash_is_uncertain_never_resubmitted(self):
        job_id = self.submit_video().json()["job_id"]
        with patch("app.jobs.video_lifecycle.checkpoint_submission", side_effect=BaseException("crash after paid submit")):
            with self.assertRaises(BaseException): execute_job(job_id)
        self.assertEqual(self.state(job_id)[2]["video_lifecycle"]["phase"], "submitting")
        result = self.recover(now=utc_now() + timedelta(seconds=301))
        self.assertEqual(result["uncertain"], 1)
        execute_job(job_id)
        self.assertEqual(self.http.call_count, 1)
        response = self.client.get(f"/v2/jobs/{job_id}").json()
        self.assertEqual(response["error_code"], "VIDEO_SUBMISSION_UNCERTAIN")
        self.assertEqual(response["status"], "failed")

    def test_late_operation_is_preserved_without_reviving_failed_job(self):
        job_id, execution_id = self.crash_prepared()
        with execution_scope(job_id, execution_id): begin_submission(self.sessions)
        self.recover(now=utc_now() + timedelta(seconds=301))
        with execution_scope(job_id, execution_id):
            checkpoint_submission(self.sessions, Pending("alibaba", "wan3.0-video", TASK_ID))
        self.assertEqual(self.state(job_id)[:2], ("failed", "failed"))
        self.assertEqual(self.state(job_id)[2]["provider_operation"]["operation_id"], TASK_ID)
        self.assertFalse(schedule_continuation(job_id))
        self.http.assert_not_called()

    def test_lost_initial_enqueue_and_recovery_queue_identity(self):
        response = self.submit_video()
        job_id = response.json()["job_id"]
        with self.sessions() as db:
            db.scalar(select(Job).where(Job.job_id == job_id)).created_at = utc_now() - timedelta(minutes=10)
            db.commit()
        result = self.recover()
        self.assertEqual(result["requeued"], 1)
        self.queue.enqueue.assert_called_with(job_id, kind="recover")
        self.http.assert_not_called()
        execute_job(job_id)
        execute_job(job_id)
        self.assertEqual(self.http.call_count, 1)

    def test_checkpoint_committed_before_return_and_lost_dispatch_recovers(self):
        job_id = self.submit_video().json()["job_id"]
        with patch("app.jobs.worker.schedule_continuation", side_effect=RuntimeError("lost dispatch")):
            with self.assertRaises(RuntimeError): execute_job(job_id)
        self.assertEqual(self.state(job_id)[:2], ("waiting", "waiting"))
        result = self.recover()
        self.assertEqual(result["requeued"], 1)
        token = self.state(job_id)[2]["continuation"]["id"]
        continue_job(job_id, token)
        self.assertEqual(sum(c.args[0] == "POST" for c in self.http.call_args_list), 1)
        self.assertEqual(sum(c.args[0] == "GET" for c in self.http.call_args_list), 1)

    def test_checkpoint_retries_only_database_publication(self):
        import app.media.video_tasks as tasks
        actual = self.sessions
        calls = 0
        # First session loads provider/references; fail the first two checkpoint sessions.
        def sessions():
            nonlocal calls
            calls += 1
            if calls in (3, 4): raise RuntimeError("database transient")
            return actual()
        with patch.object(tasks, "SessionLocal", side_effect=sessions):
            job_id = self.submit_video().json()["job_id"]
            execute_job(job_id)
        self.assertEqual(self.state(job_id)[0], "waiting")
        self.assertEqual(self.http.call_count, 1)

    def test_active_poll_lease_is_skipped_then_expired_lease_recovers(self):
        job_id, execution_id, token = self.start_video()
        claim = _claim_operation(job_id, token)
        self.assertIsNotNone(claim)
        self.assertEqual(self.recover()["requeued"], 0)
        with self.sessions() as db:
            expiry = db.get(Execution, execution_id).metadata_json["continuation"]["lease_expires_at"]
        from app.jobs.video_lifecycle import instant
        future = instant(expiry) + timedelta(seconds=1)
        self.assertEqual(self.recover(now=future)["requeued"], 1)
        with patch("app.jobs.continuation._utc_now", return_value=future):
            continue_job(job_id, token)
        self.assertEqual(self.state(job_id)[2]["video_lifecycle"]["poll_count"], 2)
        self.assertFalse(_finalize_claim(claim, Pending("alibaba", "wan3.0-video", TASK_ID)))

    def test_poll_interval_is_durable_and_early_delivery_does_not_poll(self):
        with patch("app.jobs.video_lifecycle.poll_interval", return_value=30):
            job_id, execution_id, token = self.start_video()
        self.http.reset_mock()
        continue_job(job_id, token)
        self.http.assert_not_called()
        self.assertEqual(self.state(job_id)[0], "waiting")
        self.assertEqual(self.recover()["requeued"], 0)
        self.assertGreater(self.queue.enqueue.call_args.kwargs["delay_seconds"], 0)
        with patch("app.jobs.continuation._utc_now", return_value=utc_now() + timedelta(seconds=31)):
            continue_job(job_id, token)
        self.assertEqual(self.http.call_count, 1)

    def test_absolute_timeout_preserves_operation_and_is_terminal_in_apis(self):
        job_id, execution_id, token = self.start_video()
        future = utc_now() + timedelta(hours=3)
        result = self.recover(now=future)
        self.assertEqual(result["timed_out"], 1)
        metadata = self.state(job_id)[2]
        self.assertEqual(metadata["provider_operation"]["operation_id"], TASK_ID)
        self.assertEqual(metadata["error_code"], "VIDEO_PENDING_TIMEOUT")
        self.assertNotIn("claim_id", metadata["continuation"])
        self.http.reset_mock()
        continue_job(job_id, token)
        execute_job(job_id)
        self.http.assert_not_called()
        for path in (f"/v2/jobs/{job_id}", f"/v2/executions/{execution_id}"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["error_code"], "VIDEO_PENDING_TIMEOUT")
            self.assertEqual(response.json()["status"], "failed")
            self.assertNotIn(TEST_KEY, response.text)

    def test_timeout_is_checked_before_poll_and_after_download(self):
        job_id, execution_id, token = self.start_video()
        with patch("app.jobs.continuation._utc_now", return_value=utc_now() + timedelta(hours=3)):
            continue_job(job_id, token)
        self.assertEqual(self.http.call_count, 1)  # Initial submit only.
        job_id = self.submit_video().json()["job_id"]
        execute_job(job_id)
        with self.sessions() as db:
            execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
            token = execution.metadata_json["continuation"]["id"]
        def download(*args, **kwargs):
            with self.sessions() as db:
                execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
                metadata = deepcopy(execution.metadata_json)
                metadata["video_lifecycle"]["deadline_at"] = utc_iso(utc_now() - timedelta(seconds=1))
                execution.metadata_json = metadata
                db.commit()
            return HTTPResponse()
        self.http.return_value = operation_response("SUCCEEDED", video_url=TEMP_VIDEO_URL)
        self.download.side_effect = download
        continue_job(job_id, token)
        self.assertEqual(self.state(job_id)[2]["error_code"], "VIDEO_PENDING_TIMEOUT")
        self.store.put_bytes.assert_not_called()
        with self.sessions() as db: self.assertIsNone(db.scalar(select(Artifact)))

    def test_deadline_during_storage_cannot_publish_artifact(self):
        job_id, execution_id, token = self.start_video()
        claim = _claim_operation(job_id, token)
        def stage(payload):
            with self.sessions() as db:
                execution = db.get(Execution, execution_id)
                metadata = deepcopy(execution.metadata_json)
                metadata["video_lifecycle"]["deadline_at"] = utc_iso(utc_now() - timedelta(seconds=1))
                execution.metadata_json = metadata
                db.commit()
            return "file:///staged-unpublished", "display.mp4"
        with patch("app.jobs.continuation.stage_media_result", side_effect=stage):
            self.assertFalse(_finalize_claim(claim, Completed(artifact=ArtifactPayload("video", "video/mp4", "mp4", MP4, {}))))
        with self.sessions() as db: self.assertIsNone(db.scalar(select(Artifact)))
        self.assertEqual(self.state(job_id)[2]["error_code"], "VIDEO_PENDING_TIMEOUT")

    def test_recovery_converges_cancel_requested_and_never_resurrects(self):
        job_id, execution_id, token = self.start_video()
        _claim_operation(job_id, token)
        self.client.post(f"/v2/jobs/{job_id}/cancel")
        self.http.reset_mock()
        result = self.recover()
        self.assertEqual(result["canceled"], 1)
        self.assertEqual(self.state(job_id)[:2], ("canceled", "canceled"))
        continue_job(job_id, token)
        self.assertEqual(self.recover()["processed"], 0)
        self.http.assert_not_called()
        self.assertEqual(self.state(job_id)[2]["provider_operation"]["operation_id"], TASK_ID)

    def test_recovery_dry_run_bounds_and_repeated_run(self):
        ids = [self.start_video()[0] for _ in range(5)]
        # VideoTestCase.current_execution selects the first row, but only IDs
        # matter to this recovery test; every real submit creates its checkpoint.
        self.queue.reset_mock()
        result = self.recover(batch_size=2, max_items=3, dry_run=True)
        self.assertEqual(result["batches_processed"], 2)
        self.assertTrue(result["limit_reached"])
        self.assertTrue(result["remaining_work_possible"])
        self.queue.enqueue.assert_not_called()
        result = self.recover(batch_size=2, max_items=3)
        self.assertEqual(result["requeued"], 3)
        self.assertEqual(self.recover()["requeued"], 2)
        self.assertEqual(self.recover()["requeued"], 0)
        self.assertTrue(all(self.state(job)[0] == "waiting" for job in ids))

    def test_dispatch_failure_isolated_and_retryable(self):
        for _ in range(3): self.start_video()
        self.queue.enqueue.side_effect = [RuntimeError("queue down"), None, None]
        result = self.recover(batch_size=1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["requeued"], 3)
        self.queue.enqueue.side_effect = None
        self.assertEqual(self.recover(now=utc_now() + timedelta(seconds=61))["requeued"], 3)

    def test_unknown_running_state_is_never_resubmitted(self):
        job_id, execution_id = self.crash_prepared()
        with self.sessions() as db:
            execution = db.get(Execution, execution_id)
            execution.metadata_json = {"capability": "video.generate"}
            execution.started_at = utc_now() - timedelta(minutes=10)
            db.commit()
        self.assertEqual(self.recover()["uncertain"], 1)
        self.http.assert_not_called()

    def test_missing_polling_lease_with_checkpoint_is_repaired(self):
        job_id, execution_id, token = self.start_video()
        _claim_operation(job_id, token)
        with self.sessions() as db:
            execution = db.get(Execution, execution_id)
            metadata = deepcopy(execution.metadata_json)
            metadata["continuation"] = {}
            execution.metadata_json = metadata
            db.commit()
        self.assertEqual(self.recover()["requeued"], 1)
        new_token = self.state(job_id)[2]["continuation"]["id"]
        continue_job(job_id, new_token)
        self.assertEqual(self.state(job_id)[0], "waiting")
        self.assertEqual(sum(c.args[0] == "POST" for c in self.http.call_args_list), 1)

    def test_recovery_route_is_operator_only(self):
        path = "/v2/internal/video/recover"
        with patch.dict("os.environ", {"CRON_SECRET": "operator-only"}):
            for headers in ({}, {"Authorization": "Bearer wrong"}):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer operator-only"}).status_code, 200)

    def test_lifecycle_logs_exclude_provider_ids_prompts_credentials_and_urls(self):
        with self.assertLogs("app.jobs.video_lifecycle", level="INFO") as logs:
            job_id, execution_id, token = self.start_video()
            continue_job(job_id, token)
        text = " ".join(logs.output)
        self.assertIn(job_id, text)
        self.assertIn(execution_id, text)
        for private in (TEST_KEY, TASK_ID, TEMP_VIDEO_URL, "A slow sunrise", "file:///private"):
            self.assertNotIn(private, text)


    def test_inconsistent_checkpoint_fails_closed_and_does_not_block_next_job(self):
        first, execution_id, _ = self.start_video()
        second = self.start_video()[0]
        with self.sessions() as db:
            execution = db.get(Execution, execution_id)
            metadata = deepcopy(execution.metadata_json)
            metadata["provider_operation"] = {}
            execution.metadata_json = metadata
            db.commit()
        result = self.recover(batch_size=1)
        self.assertEqual((result["invalid"], result["requeued"]), (1, 1))
        self.assertEqual(self.state(first)[2]["error_code"], "VIDEO_CHECKPOINT_INVALID")
        self.assertEqual(self.state(second)[0], "waiting")

    def test_non_dictionary_operation_metadata_is_terminal_invalid(self):
        job_id, execution_id, _ = self.start_video()
        with self.sessions() as db:
            execution = db.get(Execution, execution_id)
            metadata = deepcopy(execution.metadata_json)
            metadata["provider_operation"]["metadata"] = None
            execution.metadata_json = metadata
            db.commit()
        self.assertEqual(self.recover()["invalid"], 1)
        self.assertEqual(self.state(job_id)[2]["error_code"], "VIDEO_CHECKPOINT_INVALID")

    def test_deadline_snapshot_is_not_extended_by_poll_or_config_changes(self):
        with patch.dict("os.environ", {"VIDEO_MAX_PENDING_SECONDS": "60"}):
            job_id, execution_id, token = self.start_video()
        before = self.state(job_id)[2]["video_lifecycle"]["deadline_at"]
        with patch.dict("os.environ", {"VIDEO_MAX_PENDING_SECONDS": "7200"}):
            continue_job(job_id, token)
        after = self.state(job_id)[2]["video_lifecycle"]["deadline_at"]
        self.assertEqual(before, after)
        self.assertEqual(self.recover(now=utc_now() + timedelta(seconds=61))["timed_out"], 1)

    def test_transient_poll_failure_keeps_checkpoint_and_deadline(self):
        job_id, execution_id, token = self.start_video()
        deadline = self.state(job_id)[2]["video_lifecycle"]["deadline_at"]
        self.http.side_effect = RuntimeError("transient status transport")
        with self.assertRaises(RuntimeError): continue_job(job_id, token)
        state = self.state(job_id)
        self.assertEqual(state[:2], ("polling", "polling"))
        self.assertEqual(state[2]["provider_operation"]["operation_id"], TASK_ID)
        self.assertEqual(state[2]["video_lifecycle"]["deadline_at"], deadline)
        self.assertEqual(state[2]["video_lifecycle"]["poll_count"], 1)

    def test_late_submit_after_recovery_cancellation_preserves_operation_only(self):
        job_id, execution_id = self.crash_prepared()
        with execution_scope(job_id, execution_id): begin_submission(self.sessions)
        self.client.post(f"/v2/jobs/{job_id}/cancel")
        self.assertEqual(self.recover()["canceled"], 1)
        with execution_scope(job_id, execution_id):
            checkpoint_submission(self.sessions, Pending("alibaba", "wan3.0-video", TASK_ID))
        self.assertEqual(self.state(job_id)[:2], ("canceled", "canceled"))
        self.assertEqual(self.state(job_id)[2]["provider_operation"]["operation_id"], TASK_ID)
        self.http.assert_not_called()

    def test_explicit_recovery_bounds_and_dry_run_snapshot(self):
        job_id, _, _ = self.start_video()
        before = self.state(job_id)
        self.recover(dry_run=True)
        self.assertEqual(self.state(job_id), before)
        for values in ({"batch_size": 0}, {"max_items": 0}, {"max_seconds": 0}, {"max_items": 10001}):
            with self.assertRaises(ValueError): self.recover(**values)


class VideoCapabilityExecutionTests(video.VideoTestCase):
    def declare(self, *, controls=None, technical=None):
        declaration = AlibabaWanVideoProvider.CAPABILITIES
        model = declaration.models[0]
        model = replace(model, capabilities=model.capabilities.model_copy(update=controls or {}),
                        technical=model.technical.model_copy(update=technical or {}))
        return patch.object(AlibabaWanVideoProvider, "CAPABILITIES", replace(declaration, models=(model,)))

    def test_changed_declaration_drives_submission_and_adapter_defense(self):
        with self.declare(controls={"aspect_ratios": ["4:3"], "resolutions": ["2160p"],
                                    "duration_seconds": IntegerRange(min=1, max=60)}):
            job_id, _, _ = self.start_video(output={"duration_seconds": 45, "resolution": "2160p", "aspect_ratio": "4:3"})
        payload = self.http.call_args.kwargs["json"]["parameters"]
        self.assertEqual((payload["duration"], payload["resolution"], payload["ratio"]), (45, "2160P", "4:3"))

    def test_operation_controls_and_prompt_limits_reject_before_enqueue(self):
        cases = [({}, {"operations": ("image_to_video",)}, {}),
                 ({"audio": False}, {}, {"output": {"audio": True}}),
                 ({"text_to_media": False}, {}, {}),
                 ({}, {"max_prompt_characters": 3}, {}),
                 ({"duration_seconds": IntegerRange(min=7, max=10)}, {}, {})]
        for controls, technical, fields in cases:
            with self.declare(controls=controls, technical=technical):
                self.assertEqual(self.submit_video(**fields).status_code, 400)
        self.queue.enqueue.assert_not_called()
        self.http.assert_not_called()

    def test_image_to_video_and_reference_size_mime_roles_come_from_declaration(self):
        reference = self.image_reference()
        fields = {"references": [{"artifact_id": reference}]}
        with self.declare(technical={"operations": ("text_to_video",)}):
            self.assertEqual(self.submit_video(**fields).status_code, 400)
        slot = AlibabaWanVideoProvider.CAPABILITIES.models[0].technical.reference_inputs[0]
        for changed in (slot.model_copy(update={"max_bytes": 2}),
                        slot.model_copy(update={"mime_types": ("image/jpeg",)}),
                        slot.model_copy(update={"roles": ("character",)})):
            with self.declare(technical={"reference_inputs": (changed,)}):
                self.assertEqual(self.submit_video(**fields).status_code, 400)
        self.queue.enqueue.assert_not_called()

    def test_worker_revalidates_changed_capabilities_before_provider_submission(self):
        response = self.submit_video()
        with self.declare(technical={"operations": ("image_to_video",)}):
            with self.assertRaises(ValueError): execute_job(response.json()["job_id"])
        self.http.assert_not_called()


class RecoveryDeliveryTests(unittest.TestCase):
    def test_vercel_recovery_has_fresh_transport_identity(self):
        from vercel.queue import QueueClient
        from app.jobs.vercel_queue import VercelJobQueue
        client = create_autospec(QueueClient, instance=True)
        with patch("app.jobs.vercel_queue.get_queue_client", return_value=client):
            queue = VercelJobQueue()
            queue.enqueue("job")
            queue.enqueue("job", kind="recover")
            queue.enqueue("job", kind="recover")
        calls = client.send.await_args_list
        self.assertEqual(calls[0].kwargs["idempotency_key"], "job")
        self.assertEqual(calls[1].args[1], {"job_id": "job", "kind": "recover"})
        self.assertNotEqual(calls[1].kwargs["idempotency_key"], calls[2].kwargs["idempotency_key"])

    def test_rq_recovery_dispatches_initial_worker_without_checkpoint_arg(self):
        from rq import Queue
        from app.jobs.rq_queue import RQJobQueue
        backend = create_autospec(Queue, instance=True)
        with patch.dict("os.environ", {"REDIS_URL": "redis://test.invalid"}), \
             patch("app.jobs.rq_queue.Redis.from_url"), patch("app.jobs.rq_queue.Queue", return_value=backend):
            RQJobQueue().enqueue("job", kind="recover")
        backend.enqueue.assert_called_once_with(execute_job, "job", job_timeout=600)
