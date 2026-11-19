import logging
import os

from ig_queue import Delivery, RQConfig, RQRetry
from ig_queue.backends.rq import RQBackend

from app.core.logging import job_trace_enabled
from app.jobs.continuation import continue_job
from app.jobs.queue import JobKind, QueueCancellation, job_delivery
from app.jobs.worker import execute_job

logger = logging.getLogger(__name__)


def _worker_invocation(delivery: Delivery):
    # These entrypoints and arguments are also used by deliveries queued before extraction.
    if delivery.kind == "continue":
        return continue_job, (delivery.work_id, delivery.continuation_token)
    return execute_job, (delivery.work_id,)


class RQJobQueue:
    def __init__(self):
        self.backend = RQBackend(
            RQConfig(
                redis_url=os.environ["REDIS_URL"],
                queue_name="ig",
                job_timeout_seconds=600,
                continuation_retry=RQRetry(max_retries=3, interval_seconds=30),
                tracking_prefix="ig:deliveries:",
            ),
            invocation=_worker_invocation,
        )

    @property
    def redis(self):
        return self.backend.redis

    @property
    def queue(self):
        return self.backend.queue

    def enqueue(
        self, job_id: str, *, kind: JobKind = "initial",
        continuation_id: str | None = None, delay_seconds: int = 0,
    ) -> None:
        delivery = job_delivery(job_id, kind, continuation_id)
        if job_trace_enabled():
            logger.info("rq.enqueue.start job_id=%s queue=ig", job_id)
        self.backend.schedule(delivery, delay_seconds=delay_seconds)
        if job_trace_enabled():
            logger.info("rq.enqueue.complete job_id=%s queue=ig", job_id)

    def cancel(self, job_id: str) -> QueueCancellation:
        result = self.backend.cancel(job_id)
        return QueueCancellation(
            self.backend.capabilities.retract_pending, result.removed,
            "Best-effort pending-delivery removal; legacy/untracked deliveries are fenced by IG state. No provider call was canceled.",
        )
