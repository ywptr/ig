import logging
import uuid

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Job
from app.jobs.provider import get_job_queue

from app.core.logging import job_trace_enabled

logger = logging.getLogger(__name__)

def create_job(
    capability: str,
    input_data: dict,
    tenant_id: str,
    user_id: str | None = None,
) -> Job:
    db = SessionLocal()

    try:
        job = Job(
            job_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            capability=capability,
            status="queued",
            input_json=input_data,
        )

        db.add(job)
        db.commit()
        db.refresh(job)

        if job_trace_enabled():
            logger.info(
                "job.created job_id=%s capability=%s tenant_id=%s user_id=%s",
                job.job_id,
                job.capability,
                job.tenant_id,
                job.user_id,
            )

        try:
            job_queue = get_job_queue()

            if job_trace_enabled():
                logger.info(
                    "job.enqueue.start job_id=%s provider=%s",
                    job.job_id,
                    type(job_queue).__name__,
                )

            job_queue.enqueue(job.job_id)

            if job_trace_enabled():
                logger.info(
                    "job.enqueue.complete job_id=%s",
                    job.job_id,
                )

        except Exception as exc:
            logger.exception(
                "Failed to enqueue job %s",
                job.job_id,
            )

            job.status = "failed"
            job.error_message = (
                f"Failed to enqueue job: {exc}"
            )

            db.commit()
            raise

        return job

    finally:
        db.close()


def get_job(
    job_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> Job | None:
    db = SessionLocal()

    try:
        statement = select(Job).where(
            Job.job_id == job_id
        )

        if user_id is not None:
            statement = statement.where(Job.user_id == user_id)

        if tenant_id is not None:
            statement = statement.where(Job.tenant_id == tenant_id)

        return db.scalar(statement)

    finally:
        db.close()