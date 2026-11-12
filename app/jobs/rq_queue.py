import os
from datetime import timedelta
from uuid import uuid4

from redis import Redis, WatchError
from rq import Queue, Retry
from rq.job import Job as RQJob
from rq.exceptions import NoSuchJobError, InvalidJobOperation
import logging

from app.core.logging import job_trace_enabled

from app.jobs.worker import execute_job
from app.jobs.continuation import continue_job
from app.jobs.queue import JobKind, validate_delivery, QueueCancellation

logger = logging.getLogger(__name__)

class RQJobQueue:
    def __init__(self):
        redis_url = os.environ["REDIS_URL"]

        self.redis = Redis.from_url(redis_url)

        self.queue = Queue(
            "ig",
            connection=self.redis,
        )

    def enqueue(
        self, job_id: str, *, kind: JobKind = "initial",
        continuation_id: str | None = None, delay_seconds: int = 0,
    ) -> None:
        validate_delivery(kind, continuation_id, delay_seconds)
        if job_trace_enabled():
            logger.info(
                "rq.enqueue.start job_id=%s queue=%s",
                job_id,
                self.queue.name,
            )
        function = continue_job if kind == "continue" else execute_job
        args = (job_id, continuation_id) if kind == "continue" else (job_id,)
        options = {"job_timeout": 600}
        if kind == "continue":
            options.update(job_id=f"ig-continue-{uuid4()}", retry=Retry(max=3, interval=30))
        if delay_seconds:
            delivery = self.queue.enqueue_in(timedelta(seconds=delay_seconds), function, *args, **options)
        else:
            delivery = self.queue.enqueue(function, *args, **options)
        # Adapter-owned transport references; IG job IDs remain queue-neutral.
        if isinstance(delivery.id, str):
            key = f"ig:deliveries:{job_id}"
            with self.redis.pipeline() as pipe:
                pipe.sadd(key, delivery.id)
                pipe.expire(key, 7 * 24 * 3600)
                pipe.execute()
        if job_trace_enabled():
            logger.info(
                "rq.enqueue.complete job_id=%s queue=%s",
                job_id,
                self.queue.name,
            )

    def cancel(self, job_id: str) -> QueueCancellation:
        removed = 0
        for delivery_id in self.redis.smembers(f"ig:deliveries:{job_id}"):
            if isinstance(delivery_id, bytes):
                delivery_id = delivery_id.decode("utf-8")
            try:
                delivery = RQJob.fetch(delivery_id, connection=self.redis)
                # Watch the job hash so reservation/start cannot race our
                # status read and make us label a running transport canceled.
                for _ in range(3):
                    with self.redis.pipeline() as pipe:
                        try:
                            pipe.watch(delivery.key)
                            if delivery.get_status(refresh=True) not in {"queued", "scheduled", "deferred"}:
                                pipe.unwatch()
                                break
                            pipe.multi()
                            delivery.cancel(pipeline=pipe)
                            pipe.execute()
                            removed += 1
                            break
                        except WatchError:
                            continue
            except (NoSuchJobError, InvalidJobOperation):
                continue
        return QueueCancellation(True, removed,
            "Best-effort pending-delivery removal; legacy/untracked deliveries are fenced by IG state. No provider call was canceled.")
