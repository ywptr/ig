"""Standalone consumer contract tests. All SDK I/O is faked."""
import asyncio
import json
import subprocess
import sys
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from rq import Queue
from vercel.queue import QueueClient

from ig_queue import (
    CancelStatus, Delivery, DeliveryReceipt, RQConfig, RQRetry, VercelQueueConfig,
    create_queue_backend,
)

WORK_ID = "order:ec41"
RECORDS = {WORK_ID: {"checkpoint": "scan-2", "state": "waiting"}}
RECEIVED = []


def process_order(delivery: Delivery) -> None:
    record = RECORDS[delivery.work_id]
    if record["checkpoint"] == delivery.continuation_token:
        RECEIVED.append(delivery)


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.rq_queue = create_autospec(Queue, instance=True)
        self.rq_queue.enqueue.return_value = SimpleNamespace(id="rq-immediate")
        self.rq_queue.enqueue_in.return_value = SimpleNamespace(id="rq-delayed")
        self.redis = self.enterContext(patch("ig_queue.backends.rq.Redis.from_url"))
        self.rq_constructor = self.enterContext(
            patch("ig_queue.backends.rq.Queue", return_value=self.rq_queue),
        )
        self.vercel_client = create_autospec(QueueClient, instance=True)
        self.vercel_client.send.return_value = "vc-id"
        self.vercel_constructor = self.enterContext(
            patch("ig_queue.backends.vercel.QueueClient", return_value=self.vercel_client),
        )
        self.rq = create_queue_backend(RQConfig(
            redis_url="redis://example.invalid/4",
            queue_name="orders",
            handler=f"{__name__}:process_order",
            job_timeout_seconds=90,
            continuation_retry=RQRetry(4, 15),
        ))
        self.vercel = create_queue_backend(VercelQueueConfig(
            topic="orders",
            base_url="https://queue.example.invalid",
            oidc_token="fake-oidc-token",
            region="sfo1",
        ))

    def test_same_envelope_is_published_and_delivered_by_both_backends(self):
        from ig_queue.backends.vercel import dispatch_vercel

        RECEIVED.clear()
        delivery = Delivery.continuation(WORK_ID, "scan-2")
        for backend, transport_id in ((self.rq, "rq-delayed"), (self.vercel, "vc-id")):
            self.assertTrue(backend.capabilities.delayed_delivery)
            self.assertFalse(backend.capabilities.terminate_running)
            self.assertEqual(
                backend.schedule(delivery, delay_seconds=12.5),
                DeliveryReceipt(WORK_ID, delivery.delivery_id, transport_id),
            )

        rq_call = self.rq_queue.enqueue_in.call_args
        delay, function, handler_path, payload = rq_call.args
        self.assertEqual(delay, timedelta(seconds=13))
        self.assertEqual(rq_call.kwargs["job_timeout"], 90)
        self.assertEqual(rq_call.kwargs["retry"].max, 4)
        self.assertEqual(rq_call.kwargs["retry"].intervals, [15])
        function(handler_path, payload)

        self.vercel_client.send.assert_awaited_once_with(
            "orders", payload, idempotency_key=delivery.delivery_id, delay=13,
        )
        asyncio.run(dispatch_vercel(payload, process_order))
        self.assertEqual(RECEIVED, [delivery, delivery])
        self.assertEqual(RECORDS[WORK_ID]["state"], "waiting")

    def test_initial_enqueue_and_explicit_settings(self):
        delivery = Delivery.initial(WORK_ID)
        self.assertEqual(self.rq.enqueue(delivery), DeliveryReceipt(WORK_ID, WORK_ID, "rq-immediate"))
        self.assertEqual(self.vercel.enqueue(delivery), DeliveryReceipt(WORK_ID, WORK_ID, "vc-id"))
        self.redis.assert_called_once_with("redis://example.invalid/4")
        self.rq_constructor.assert_called_once_with("orders", connection=self.redis.return_value)
        self.vercel_constructor.assert_called_once_with(
            base_url="https://queue.example.invalid", token="fake-oidc-token", region="sfo1",
        )
        self.rq_queue.enqueue.assert_called_once()
        self.assertEqual(self.rq_queue.enqueue.call_args.args[-1], delivery.to_message())
        self.assertEqual(self.rq_queue.enqueue.call_args.kwargs, {"job_timeout": 90})
        self.vercel_client.send.assert_awaited_once_with(
            "orders", delivery.to_message(), idempotency_key=WORK_ID, delay=0,
        )

    def test_recovery_publication_assigns_fresh_keys(self):
        first, second = Delivery.recovery(WORK_ID), Delivery.recovery(WORK_ID)
        self.assertNotEqual(first.delivery_id, second.delivery_id)
        for delivery in (first, second):
            self.vercel.enqueue(delivery)
        calls = self.vercel_client.send.await_args_list
        self.assertEqual([call.kwargs["idempotency_key"] for call in calls],
                         [first.delivery_id, second.delivery_id])
        self.assertEqual([call.args[1]["work_id"] for call in calls], [WORK_ID, WORK_ID])

    def test_vercel_receipt_can_lack_a_transport_id(self):
        self.vercel_client.send.return_value = None
        self.assertEqual(self.vercel.enqueue(Delivery.initial(WORK_ID)),
                         DeliveryReceipt(WORK_ID, WORK_ID, None))

    def test_capabilities_and_unsupported_cancellation(self):
        self.assertTrue(self.rq.capabilities.retract_pending)
        self.assertTrue(self.rq.capabilities.track_by_work_id)
        self.assertFalse(self.rq.capabilities.deduplicate_delivery_id)
        self.assertFalse(self.vercel.capabilities.retract_pending)
        self.assertFalse(self.vercel.capabilities.track_by_work_id)
        self.assertTrue(self.vercel.capabilities.deduplicate_delivery_id)
        self.assertEqual(self.vercel.cancel(WORK_ID).status, CancelStatus.UNSUPPORTED)
        self.vercel_constructor.assert_not_called()

    def test_rq_cancellation_status_and_count(self):
        from rq.job import JobStatus

        redis = self.redis.return_value
        redis.smembers.return_value = {b"rq-id"}
        with patch("ig_queue.backends.rq.Job.fetch") as fetch:
            job = fetch.return_value
            for job_status, expected, removed in (
                (JobStatus.QUEUED, CancelStatus.RETRACTED, 1),
                (JobStatus.SCHEDULED, CancelStatus.RETRACTED, 1),
                (JobStatus.STARTED, CancelStatus.ALREADY_RUNNING, 0),
                (JobStatus.FINISHED, CancelStatus.NOT_FOUND, 0),
            ):
                with self.subTest(job_status=job_status):
                    job.get_status.return_value = job_status
                    job.cancel.reset_mock()
                    result = self.rq.cancel(WORK_ID)
                    redis.smembers.assert_called_with("orders:deliveries:" + WORK_ID)
                    self.assertEqual((result.status, result.removed), (expected, removed))
                    self.assertEqual(job.cancel.call_count, removed)

    def test_invalid_delay_is_rejected_by_both_backends(self):
        for backend in (self.rq, self.vercel):
            for delay in (-1, float("nan"), float("inf")):
                with self.subTest(backend=backend, delay=delay), self.assertRaises(ValueError):
                    backend.schedule(Delivery.initial(WORK_ID), delay_seconds=delay)
        self.rq_queue.enqueue_in.assert_not_called()
        self.vercel_client.send.assert_not_called()


class EnvelopeTests(unittest.TestCase):
    def test_subscriber_surfaces_handler_failure_for_transport_retry(self):
        from ig_queue.backends.vercel import dispatch_vercel

        def fail(delivery):
            raise RuntimeError(delivery.work_id)

        with self.assertRaisesRegex(RuntimeError, WORK_ID):
            asyncio.run(dispatch_vercel(Delivery.initial(WORK_ID).to_message(), fail))

    def test_round_trip(self):
        for delivery in (
            Delivery.initial(WORK_ID), Delivery.recovery(WORK_ID),
            Delivery.continuation(WORK_ID, "scan-2"),
        ):
            payload = json.loads(json.dumps(delivery.to_message()))
            self.assertEqual(Delivery.from_message(payload), delivery)

    def test_malformed_messages_are_rejected_before_dispatch(self):
        from ig_queue.backends.vercel import dispatch_vercel

        valid = Delivery.initial(WORK_ID).to_message()
        for payload in (
            [], {}, {**valid, "work_id": 13}, {**valid, "delivery_id": ""},
            {**valid, "kind": "unexpected"}, {**valid, "kind": "continue"},
            {**valid, "continuation_token": "unexpected"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                asyncio.run(dispatch_vercel(payload, self.fail))

    def test_generic_imports_do_not_load_backends_or_application(self):
        subprocess.run(
            [sys.executable, "-I", "-c", """
import sys
import ig_queue
roots = {name.partition('.')[0] for name in sys.modules}
assert not roots & {'redis', 'rq', 'vercel', 'anyio', 'app', 'fastapi', 'sqlalchemy', 'ig_core'}
"""],
            check=True,
        )
