from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.db.database import SessionLocal
from sqlalchemy import select
from app.db.models import Job, User
from app.jobs.handlers import is_supported
from app.jobs.service import create_job, get_job
from app.auth.dependencies import get_current_user

router = APIRouter()


class JobRequestPayload(BaseModel):
    capability: str
    input: dict[str, Any] = Field(
        default_factory=dict
    )


def job_to_dict(job):
    return {
        "job_id": job.job_id,
        "capability": job.capability,
        "status": job.status,
        "input": job.input_json,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": (
            job.started_at.isoformat()
            if job.started_at
            else None
        ),
        "completed_at": (
            job.completed_at.isoformat()
            if job.completed_at
            else None
        ),
    }


@router.post("/jobs", status_code=202)
def submit_job(
    request: JobRequestPayload,
    current_user: User = Depends(get_current_user),
):
    if not is_supported(request.capability):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported capability: "
                f"{request.capability}"
            ),
        )

    job = create_job(
        capability=request.capability,
        input_data=request.input,
        user_id=current_user.user_id,
    )

    return job_to_dict(job)


@router.get("/jobs/{job_id}")
def read_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    job = get_job(
        job_id,
        user_id=current_user.user_id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    return job_to_dict(job)

@router.get("/jobs")
def list_jobs(
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        statement = (
            select(Job)
            .where(
                Job.user_id
                == current_user.user_id
            )
            .order_by(Job.created_at.desc())
            .limit(100)
        )

        jobs = db.scalars(statement).all()

        return [
            {
                "job_id": job.job_id,
                "capability": job.capability,
                "status": job.status,
                "error_message": job.error_message,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }
            for job in jobs
        ]

    finally:
        db.close()