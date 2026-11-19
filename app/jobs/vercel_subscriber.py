import logging
from collections.abc import Mapping
from typing import Any

from ig_queue import Delivery
from ig_queue.backends.vercel import dispatch_vercel
from vercel.queue import subscribe

from app.core.logging import configure_logging
from app.jobs.continuation import continue_job
from app.jobs.worker import execute_job

configure_logging()
logger = logging.getLogger(__name__)


def _decode_job(message: Mapping[str, object]) -> Delivery:
    job_id = str(message["job_id"])
    # The legacy payload does not include its publish key. IG uses the durable locator.
    return Delivery(job_id, job_id, message.get("kind", "initial"), message.get("continuation_id"))


def _dispatch_job(delivery: Delivery) -> None:
    if delivery.kind == "continue":
        continue_job(delivery.work_id, delivery.continuation_token)
    else:
        execute_job(delivery.work_id)


@subscribe(
    topic="ig-jobs",
    consumer_group="ig-worker",
    max_attempts=3,
)
async def process_job(message: dict[str, Any]) -> None:
    logger.info("queue.job.execute.start job_id=%s", message["job_id"])
    await dispatch_vercel(message, _dispatch_job, decode=_decode_job)
    logger.info("queue.job.execute.complete job_id=%s", message["job_id"])
