import os
from datetime import timedelta
from uuid import uuid4

from redis import Redis
from rq import Queue, Retry
import logging

from app.core.logging import job_trace_enabled

from app.jobs.worker import execute_job
from app.jobs.continuation import continue_job
from app.jobs.queue import JobKind, validate_delivery

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
        function = execute_job if kind == "initial" else continue_job
        args = (job_id,) if kind == "initial" else (job_id, continuation_id)
        options = {"job_timeout": 600}
        if kind == "continue":
            options.update(job_id=f"ig-continue-{uuid4()}", retry=Retry(max=3, interval=30))
        if delay_seconds:
            self.queue.enqueue_in(timedelta(seconds=delay_seconds), function, *args, **options)
        else:
            self.queue.enqueue(function, *args, **options)
        if job_trace_enabled():
            logger.info(
                "rq.enqueue.complete job_id=%s queue=%s",
                job_id,
                self.queue.name,
            )
