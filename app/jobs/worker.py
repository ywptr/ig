from datetime import datetime

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Job
from app.jobs.handlers import get_handler
from app.executions.service import (
    apply_execution_result,
    complete_execution,
    create_execution,
    fail_execution,
)

def execute_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    execution = None

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

        execution = create_execution(
            db,
            job_id=job.job_id,
            user_id=job.user_id,
            metadata={
                "capability": job.capability,
            },
        )
        # Persist the fact that this attempt has
        # actually started before dispatching work
        # to the capability handler.
        db.commit()

        handler = get_handler(job.capability)

        result =handler(job.input_json)

        if result is not None:
            apply_execution_result(
                execution,
                result,
            )

        complete_execution(
            db,
            execution,
        )

        job.status = "completed"
        job.completed_at = datetime.utcnow()

        db.commit()

    except Exception as exc:
        if execution is not None:
            fail_execution(
                db,
                execution,
                error_message=str(exc),
            )
        
        if job is not None:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()

            db.commit()

        if job is not None or execution is not None:
            db.commit()

        raise

    finally:
        db.close()