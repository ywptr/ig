"""Isolated continuation/queue tests; no Redis, Vercel or provider requests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from importlib.metadata import version
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock, get_ident
import unittest
from unittest.mock import Mock, create_autospec, patch

import anyio
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from tests.test_media_job_contracts import MediaJobTestCase
from app.db.models import Execution, Job
from app.jobs.continuation import (
    _claim_operation, _finalize_claim, continue_job, schedule_continuation,
)
from app.jobs.handlers import HANDLERS, RESUME_HANDLERS
from app.jobs.result import Completed, Pending
from app.jobs.service import create_job
from app.jobs.worker import execute_job


class ContinuationTests(MediaJobTestCase):
    def create_test_engine(self):
        directory = self.enterContext(TemporaryDirectory())
        return create_engine(
            f"sqlite:///{Path(directory) / 'continuations.sqlite'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )

    def setUp(self):
        super().setUp()
        self.enterContext(patch("app.jobs.worker.SessionLocal", self.sessions))
        self.initial = Mock(return_value=self.pending(0))
        self.poll = Mock(side_effect=self.fake_poll)
        self.enterContext(patch.dict(HANDLERS, {"test.async": self.initial}))
        self.enterContext(patch.dict(RESUME_HANDLERS, {"test.async": self.poll}))
        with self.sessions() as db:
            job = create_job(
                db, capability="test.async", tenant_id="tenant-one", user_id="user-one",
                input_data={"tenant_id": "forged", "user_id": "forged", "operation_id": "forged"},
            )
            self.job_id = job.job_id

    @staticmethod
    def pending(count):
        return Pending("fake", "fake-model", "fake-operation-1", {"poll_count": count})

    def fake_poll(self, operation, *, tenant_id, user_id):
        self.assertEqual((tenant_id, user_id), ("tenant-one", "user-one"))
        self.assertEqual(
            (operation.provider, operation.model, operation.operation_id),
            ("fake", "fake-model", "fake-operation-1"),
        )
        count = operation.metadata["poll_count"] + 1
        return self.pending(count) if count < 3 else Completed(metadata={"result": "finished"})

    def state(self, sessions=None):
        with (sessions or self.sessions)() as db:
            job = db.scalar(select(Job).where(Job.job_id == self.job_id))
            execution = db.scalar(select(Execution).where(Execution.job_id == self.job_id))
            return {
                "job_status": job.status, "job_completed_at": job.completed_at,
                "execution_status": execution.status, "execution_id": execution.execution_id,
                "execution_completed_at": execution.completed_at,
                "provider": execution.provider, "model": execution.model,
                "metadata": execution.metadata_json,
                "executions": db.scalar(select(func.count()).select_from(Execution)),
            }

    def token(self):
        return self.state()["metadata"]["continuation"]["id"]

    def start(self):
        execute_job(self.job_id)
        return self.token()

    def after_lease_expiry(self):
        expires = self.state()["metadata"]["continuation"]["lease_expires_at"]
        return patch(
            "app.jobs.continuation._utc_now",
            return_value=datetime.fromisoformat(expires) + timedelta(seconds=1),
        )

    def test_fake_lifecycle_submits_once_and_polls_same_operation_three_times(self):
        self.start()
        initial_state = self.state()
        self.assertEqual(initial_state["job_status"], "waiting")
        self.assertEqual(initial_state["execution_status"], "waiting")
        self.assertEqual(initial_state["metadata"]["provider_operation"]["metadata"], {"poll_count": 0})
        for count in (1, 2):
            old_token = self.token()
            continue_job(self.job_id, old_token)
            state = self.state()
            self.assertEqual((state["job_status"], state["execution_status"]), ("waiting", "waiting"))
            self.assertNotEqual(self.token(), old_token)
            self.assertEqual(set(state["metadata"]["continuation"]), {"id"})
            self.assertEqual(state["metadata"]["provider_operation"]["metadata"], {"poll_count": count})
            self.assertIsNone(state["job_completed_at"])
        continue_job(self.job_id, self.token())
        state = self.state()
        self.assertEqual((state["job_status"], state["execution_status"]), ("completed", "completed"))
        self.assertIsNotNone(state["job_completed_at"])
        self.assertIsNotNone(state["execution_completed_at"])
        self.assertEqual((state["provider"], state["model"]), ("fake", "fake-model"))
        self.assertEqual(state["metadata"]["result"], "finished")
        self.assertEqual(set(state["metadata"]["continuation"]), {"id"})
        self.assertEqual(state["execution_id"], initial_state["execution_id"])
        self.assertEqual(state["executions"], 1)
        self.initial.assert_called_once()
        self.assertEqual(self.poll.call_count, 3)
        self.assertEqual([call.args[0].metadata for call in self.poll.call_args_list], [
            {"poll_count": 0}, {"poll_count": 1}, {"poll_count": 2},
        ])
        continuation_calls = [call for call in self.queue.enqueue.call_args_list if call.kwargs]
        self.assertEqual(len(continuation_calls), 3)
        self.assertEqual(len({call.kwargs["continuation_id"] for call in continuation_calls}), 3)
        for call in continuation_calls:
            self.assertEqual(call.kwargs["kind"], "continue")
            self.assertEqual(call.kwargs["delay_seconds"], 30)

    def test_duplicate_old_token_cannot_poll_a_new_waiting_checkpoint(self):
        token = self.start()
        continue_job(self.job_id, token)
        state = self.state()
        continue_job(self.job_id, token)
        self.assertEqual(self.state(), state)
        self.poll.assert_called_once()
        self.initial.assert_called_once()

    def test_concurrent_deliveries_only_one_execution_claim_and_poll(self):
        token = self.start()
        self.assert_concurrent_claims_poll_once(token)

    def assert_concurrent_claims_poll_once(self, token):
        barrier, lock = Barrier(2), Lock()
        rowcounts = []

        def is_claim(statement):
            return (
                statement.startswith("UPDATE executions SET")
                and "JSON_EXTRACT" in statement
                and "metadata_json=" in statement.partition(" WHERE ")[0]
            )

        def before(connection, cursor, statement, parameters, context, executemany):
            if is_claim(statement):
                barrier.wait(timeout=10)

        def after(connection, cursor, statement, parameters, context, executemany):
            if is_claim(statement):
                with lock:
                    rowcounts.append(cursor.rowcount)

        event.listen(self.engine, "before_cursor_execute", before)
        event.listen(self.engine, "after_cursor_execute", after)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = [pool.submit(continue_job, self.job_id, token) for _ in range(2)]
                for attempt in attempts:
                    attempt.result(timeout=20)
        finally:
            event.remove(self.engine, "before_cursor_execute", before)
            event.remove(self.engine, "after_cursor_execute", after)
        self.assertEqual(sorted(rowcounts), [0, 1])
        self.poll.assert_called_once()
        self.assertEqual(self.state()["executions"], 1)

    def test_concurrent_expired_lease_reclaims_only_poll_once(self):
        token = self.start()
        abandoned = _claim_operation(self.job_id, token)
        with self.after_lease_expiry():
            self.assert_concurrent_claims_poll_once(token)
        self.assertFalse(_finalize_claim(abandoned, Completed()))

    def test_claim_is_committed_and_connection_released_before_resume_handler(self):
        token = self.start()

        def check_boundary(operation, *, tenant_id, user_id):
            # A committed claim is visible to a separate reader. No connection
            # from the claim phase is checked out during provider network I/O.
            self.assertEqual(self.engine.pool.checkedout(), 0)
            state = self.state()
            self.assertEqual((state["job_status"], state["execution_status"]), ("polling", "polling"))
            lease = state["metadata"]["continuation"]
            self.assertTrue(lease["claim_id"])
            self.assertEqual(lease["id"], token)
            self.assertIsNotNone(datetime.fromisoformat(lease["lease_expires_at"]).tzinfo)
            # A separate connection can even commit a write while the provider
            # handler runs: no claim-phase database transaction/lock remains.
            with self.sessions() as db:
                execution = db.scalar(select(Execution))
                execution.metadata_json = {**execution.metadata_json, "boundary_probe": True}
                db.commit()
            return self.fake_poll(operation, tenant_id=tenant_id, user_id=user_id)

        self.poll.side_effect = check_boundary
        continue_job(self.job_id, token)
        self.assertEqual(self.state()["execution_status"], "waiting")
        self.assertTrue(self.state()["metadata"]["boundary_probe"])

    def test_duplicate_inside_active_provider_call_does_not_poll_or_enqueue(self):
        token = self.start()

        def duplicate(operation, *, tenant_id, user_id):
            before = self.state()
            enqueue_count = self.queue.enqueue.call_count
            continue_job(self.job_id, token)
            self.assertEqual(self.state(), before)
            self.assertEqual(self.queue.enqueue.call_count, enqueue_count)
            return self.fake_poll(operation, tenant_id=tenant_id, user_id=user_id)

        self.poll.side_effect = duplicate
        continue_job(self.job_id, token)
        self.poll.assert_called_once()

    def test_only_current_claim_id_can_finalize_the_same_checkpoint(self):
        token = self.start()
        old = _claim_operation(self.job_id, token)
        with self.after_lease_expiry():
            current = _claim_operation(self.job_id, token)
        self.assertNotEqual(current.claim_id, old.claim_id)
        self.assertEqual(current.continuation_id, old.continuation_id)
        before = self.state()
        self.assertFalse(_finalize_claim(old, Completed(metadata={"stale": True})))
        self.assertEqual(self.state(), before)
        self.assertTrue(_finalize_claim(current, Completed(metadata={"current": True})))
        state = self.state()
        self.assertEqual(state["job_status"], "completed")
        self.assertTrue(state["metadata"]["current"])
        self.assertNotIn("stale", state["metadata"])
        self.assertEqual(state["metadata"]["continuation"], {"id": token})

    def test_stale_worker_cannot_overwrite_a_newer_checkpoint(self):
        token = self.start()
        old = _claim_operation(self.job_id, token)
        with self.after_lease_expiry():
            current = _claim_operation(self.job_id, token)
        self.assertTrue(_finalize_claim(current, self.pending(1)))
        state = self.state()
        self.assertNotEqual(self.token(), token)
        self.assertFalse(_finalize_claim(old, self.pending(99)))
        self.assertFalse(_finalize_claim(old, Completed(metadata={"stale": True})))
        self.assertEqual(self.state(), state)

    def test_finalization_rechecks_original_operation_identity(self):
        token = self.start()
        claim = _claim_operation(self.job_id, token)
        for field in ("provider", "model", "operation_id"):
            with self.subTest(field=field):
                with self.sessions() as db:
                    execution = db.scalar(select(Execution))
                    if field == "operation_id":
                        original = execution.metadata_json["provider_operation"][field]
                        execution.metadata_json = {
                            **execution.metadata_json,
                            "provider_operation": {
                                **execution.metadata_json["provider_operation"], field: "changed",
                            },
                        }
                    else:
                        original = getattr(execution, field)
                        setattr(execution, field, "changed")
                    db.commit()
                before = self.state()
                self.assertFalse(_finalize_claim(claim, Completed()))
                self.assertEqual(self.state(), before)
                with self.sessions() as db:
                    execution = db.scalar(select(Execution))
                    if field == "operation_id":
                        execution.metadata_json = {
                            **execution.metadata_json,
                            "provider_operation": {
                                **execution.metadata_json["provider_operation"], field: original,
                            },
                        }
                    else:
                        setattr(execution, field, original)
                    db.commit()

    def test_crash_during_status_call_keeps_lease_until_expiry(self):
        token = self.start()
        self.poll.side_effect = SystemExit("provider call interrupted")
        with self.assertRaises(SystemExit):
            continue_job(self.job_id, token)
        state = self.state()
        self.assertEqual(state["execution_status"], "polling")
        continue_job(self.job_id, token)
        self.poll.assert_called_once()
        self.assertEqual(self.state(), state)
        self.poll.side_effect = self.fake_poll
        with self.after_lease_expiry():
            continue_job(self.job_id, token)
        self.assertEqual(self.poll.call_count, 2)
        self.assertEqual(self.state()["execution_status"], "waiting")

    def test_duplicate_after_completion_never_polls(self):
        token = self.start()
        self.poll.return_value = Completed()
        self.poll.side_effect = None
        continue_job(self.job_id, token)
        before = self.state()
        continue_job(self.job_id, token)
        execute_job(self.job_id)
        self.assertEqual(self.state(), before)
        self.poll.assert_called_once()
        self.initial.assert_called_once()

    def test_crash_during_claim_rolls_back_to_recoverable_waiting(self):
        token = self.start()
        before = self.state()

        def crash(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE executions SET"):
                raise SystemExit("claim crash")

        event.listen(self.engine, "after_cursor_execute", crash)
        try:
            with self.assertRaises(SystemExit):
                continue_job(self.job_id, token)
        finally:
            event.remove(self.engine, "after_cursor_execute", crash)
        self.assertEqual(self.state(), before)
        self.poll.assert_not_called()
        continue_job(self.job_id, token)
        self.poll.assert_called_once()

    def test_crash_after_claim_before_poll_requires_expired_lease_to_recover(self):
        token = self.start()
        with patch("app.jobs.continuation.get_resume_handler", side_effect=SystemExit("pre-poll crash")):
            with self.assertRaises(SystemExit):
                continue_job(self.job_id, token)
        state = self.state()
        self.assertEqual((state["job_status"], state["execution_status"]), ("polling", "polling"))
        self.assertTrue(state["metadata"]["continuation"]["claim_id"])
        self.poll.assert_not_called()
        continue_job(self.job_id, token)
        self.poll.assert_not_called()
        self.assertFalse(schedule_continuation(self.job_id))
        self.assertEqual(self.state(), state)
        with self.after_lease_expiry():
            self.assertTrue(schedule_continuation(self.job_id))
            continue_job(self.job_id, token)
        self.poll.assert_called_once()
        self.assertEqual(self.state()["execution_status"], "waiting")
        self.assertEqual(set(self.state()["metadata"]["continuation"]), {"id"})

    def test_completed_poll_before_finalization_can_be_repeated_after_crash(self):
        token = self.start()
        self.poll.side_effect = None
        self.poll.return_value = Completed(metadata={"result": "finished"})
        with patch("app.jobs.continuation.apply_execution_result", side_effect=SystemExit("post-poll crash")):
            with self.assertRaises(SystemExit):
                continue_job(self.job_id, token)
        self.assertEqual(self.state()["execution_status"], "polling")
        continue_job(self.job_id, token)
        self.poll.assert_called_once()  # Active lease blocks the duplicate.
        with self.after_lease_expiry():
            continue_job(self.job_id, token)
        self.assertEqual(self.poll.call_count, 2)  # Honest at-least-once status reads.
        self.assertEqual(self.state()["job_status"], "completed")
        self.initial.assert_called_once()

    def test_pending_commit_then_scheduling_failure_recovers_on_old_redelivery(self):
        token = self.start()
        self.queue.enqueue.side_effect = RuntimeError("queue unavailable")
        with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
            continue_job(self.job_id, token)
        state = self.state()
        self.assertEqual(state["job_status"], "waiting")
        self.assertEqual(state["metadata"]["provider_operation"]["metadata"], {"poll_count": 1})
        self.queue.enqueue.side_effect = None
        continue_job(self.job_id, token)  # Repairs scheduling, does not repeat poll.
        self.poll.assert_called_once()
        self.assertEqual(self.queue.enqueue.call_args.kwargs["continuation_id"], self.token())
        self.assertEqual(self.state(), state)

    def test_initial_checkpoint_scheduling_failure_does_not_resubmit_generation(self):
        self.queue.enqueue.side_effect = RuntimeError("queue unavailable")
        with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
            execute_job(self.job_id)
        self.assertEqual(self.state()["job_status"], "waiting")
        self.queue.enqueue.side_effect = None
        execute_job(self.job_id)
        self.initial.assert_called_once()
        self.assertEqual(self.queue.enqueue.call_args.kwargs["continuation_id"], self.token())

    def test_lost_delivery_can_be_republished_from_fresh_persisted_state(self):
        token = self.start()
        fresh = create_engine(self.engine.url)
        try:
            with patch("app.jobs.continuation.SessionLocal", sessionmaker(bind=fresh)):
                self.assertTrue(schedule_continuation(self.job_id))
                continue_job(self.job_id, token)
        finally:
            fresh.dispose()
        self.poll.assert_called_once()
        self.initial.assert_called_once()

    def test_resume_handler_cannot_switch_external_operation(self):
        token = self.start()
        before = self.state()
        self.poll.side_effect = None
        self.poll.return_value = Pending("fake", "fake-model", "different-operation")
        with self.assertRaisesRegex(ValueError, "cannot replace"):
            continue_job(self.job_id, token)
        state = self.state()
        self.assertEqual(state["execution_status"], "polling")
        self.assertEqual(state["metadata"]["provider_operation"], before["metadata"]["provider_operation"])

    def test_continuation_rejects_execution_ownership_mismatch(self):
        token = self.start()
        with self.sessions() as db:
            db.scalar(select(Execution)).user_id = "user-two"
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            continue_job(self.job_id, token)
        self.poll.assert_not_called()
        self.assertEqual(self.state()["job_status"], "waiting")

    def test_continuation_cannot_run_a_queued_initial_handler(self):
        continue_job(self.job_id, "unrelated-token")
        self.initial.assert_not_called()
        self.poll.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job)).status, "queued")
            self.assertIsNone(db.scalar(select(Execution)))

    def test_public_jobs_cannot_dispatch_internal_continuations(self):
        response = self.client.post("/v2/jobs", json={
            "capability": "test.async", "kind": "continue",
            "input": {"job_id": self.job_id, "continuation_id": "forged"},
        })
        self.assertEqual(response.status_code, 403, response.text)
        self.initial.assert_not_called()
        self.poll.assert_not_called()


class QueueContinuationTests(unittest.TestCase):
    def test_pinned_vercel_delay_signature_and_distinct_delivery_identities(self):
        from vercel.queue import QueueClient
        from app.jobs.vercel_queue import VercelJobQueue

        self.assertEqual(version("vercel-queue"), "0.9.1")
        self.assertEqual(version("vercel"), "0.11.5")
        self.assertIn("delay", inspect.signature(QueueClient.send).parameters)
        client = create_autospec(QueueClient, instance=True)
        with patch("app.jobs.vercel_queue.get_queue_client", return_value=client):
            queue = VercelJobQueue()
            queue.enqueue("job")
            queue.enqueue("job", kind="continue", continuation_id="checkpoint-one", delay_seconds=30)
            queue.enqueue("job", kind="continue", continuation_id="checkpoint-two", delay_seconds=30)
            queue.enqueue("job", kind="continue", continuation_id="checkpoint-two", delay_seconds=30)
        self.assertEqual(client.send.await_args_list[0].kwargs["idempotency_key"], "job")
        identities = []
        for call in client.send.await_args_list[1:]:
            self.assertEqual(call.args[0], "ig-jobs")
            self.assertEqual(call.args[1]["kind"], "continue")
            self.assertEqual(call.kwargs["delay"], 30)
            identities.append(call.kwargs["idempotency_key"])
        self.assertEqual(len(set(identities)), 3)

    def test_rq_uses_installed_enqueue_in_without_redis_calls(self):
        from rq import Queue
        from app.jobs.rq_queue import RQJobQueue

        redis_queue = create_autospec(Queue, instance=True)
        with patch.dict("os.environ", {"REDIS_URL": "redis://test.invalid"}):
            with patch("app.jobs.rq_queue.Redis.from_url"), patch("app.jobs.rq_queue.Queue", return_value=redis_queue):
                queue = RQJobQueue()
                queue.enqueue("job")
                queue.enqueue("job", kind="continue", continuation_id="checkpoint", delay_seconds=30)
        redis_queue.enqueue.assert_called_once_with(execute_job, "job", job_timeout=600)
        call = redis_queue.enqueue_in.call_args
        self.assertEqual(call.args, (timedelta(seconds=30), continue_job, "job", "checkpoint"))
        self.assertEqual(call.kwargs["retry"].max, 3)

    def test_vercel_subscriber_dispatches_in_thread_without_operation_ids_from_message(self):
        from app.jobs.vercel_subscriber import process_job

        caller_thread = get_ident()
        worker_threads = []
        handler = Mock(side_effect=lambda *args: worker_threads.append(get_ident()))
        with patch("app.jobs.vercel_subscriber.continue_job", handler), patch("app.jobs.vercel_subscriber.execute_job") as initial:
            anyio.run(process_job, {
                "job_id": "job", "kind": "continue", "continuation_id": "checkpoint",
                "operation_id": "untrusted-and-ignored",
            })
            initial.assert_not_called()
        handler.assert_called_once_with("job", "checkpoint")
        self.assertNotEqual(worker_threads, [caller_thread])


if __name__ == "__main__":
    unittest.main()
