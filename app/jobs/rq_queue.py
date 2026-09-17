import os

from redis import Redis
from rq import Queue

from app.jobs.worker import execute_job


class RQJobQueue:
    def __init__(self):
        redis_url = os.environ["REDIS_URL"]

        self.redis = Redis.from_url(redis_url)

        self.queue = Queue(
            "ig",
            connection=self.redis,
        )

    def enqueue(self, job_id: str) -> None:
        self.queue.enqueue(
            execute_job,
            job_id,
            job_timeout=600,
        )