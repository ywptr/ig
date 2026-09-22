import uuid

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Job
from app.jobs.queue import JobQueue
from app.jobs.rq_queue import RQJobQueue


job_queue: JobQueue = RQJobQueue()


def create_job(
    capability: str,
    input_data: dict,
    user_id: str | None = None,
) -> Job:
    db = SessionLocal()

    try:
        job = Job(
            job_id=str(uuid.uuid4()),
            user_id=user_id,
            capability=capability,
            status="queued",
            input_json=input_data,
        )

        db.add(job)
        db.commit()
        db.refresh(job)

        try:
            job_queue.enqueue(job.job_id)

        except Exception as exc:
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
    user_id: str | None = None,
) -> Job | None:
    db = SessionLocal()

    try:
        statement = select(Job).where(
            Job.job_id == job_id
        )

        if user_id is not None:
            statement = statement.where(Job.user_id == user_id)

        return db.scalar(statement)

    finally:
        db.close()