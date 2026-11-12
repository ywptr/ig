from typing import Any
from functools import partial

import anyio

from vercel.queue import subscribe

from app.jobs.worker import execute_job
from app.jobs.continuation import continue_job
from app.jobs.queue import validate_delivery

import logging

from app.core.logging import configure_logging

configure_logging()

logger = logging.getLogger(__name__)


@subscribe(
    topic="ig-jobs",
    consumer_group="ig-worker",
    max_attempts=3,
)
async def process_job(
    message: dict[str, Any],
) -> None:

    job_id = str(message["job_id"])

    logger.info(
        "queue.job.execute.start job_id=%s",
        job_id,
    )

    kind = message.get("kind", "initial")
    continuation_id = message.get("continuation_id")
    validate_delivery(kind, continuation_id, 0)
    dispatch = (
        partial(continue_job, job_id, continuation_id)
        if kind == "continue" else partial(execute_job, job_id)
    )
    # Keep lease renewal/event-loop work alive and permit the sync queue adapter
    # to use anyio.run() when scheduling the next delivery.
    await anyio.to_thread.run_sync(dispatch)

    logger.info(
        "queue.job.execute.complete job_id=%s",
        job_id,
    )
