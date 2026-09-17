from datetime import datetime

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Job
from app.jobs.handlers import get_handler


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    job = None

    try:
        statement = select(Job).where(
            Job.job_id == job_id
        )

        job = db.scalar(statement)

        if job is None:
            raise RuntimeError(
                f"Job not found: {job_id}"
            )

        job.status = "running"
        job.started_at = datetime.utcnow()

        db.commit()

        handler = get_handler(job.capability)

        handler(job.input_json)

        job.status = "completed"
        job.completed_at = datetime.utcnow()

        db.commit()

    except Exception as exc:
        if job is not None:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()

            db.commit()

        raise

    finally:
        db.close()