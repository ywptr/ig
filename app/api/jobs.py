from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session


from sqlalchemy import select
from app.db.models import Job, Execution
from app.auth.dependencies import get_db
from app.jobs.handlers import is_supported
from app.jobs.service import create_job, get_job
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_authenticated_tenant_context,
)

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
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
    db: Session = Depends(get_db),
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
        db,
        capability=request.capability,
        input_data=request.input,
        tenant_id=context.tenant.tenant_id,
        user_id=context.membership.user_id,
    )

    return job_to_dict(job)


@router.get("/jobs/{job_id}")
def read_job(
    job_id: str,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
    db: Session = Depends(get_db),
):
    job = get_job(
        db,
        job_id,
        tenant_id=context.tenant.tenant_id,
        user_id=context.membership.user_id,
    )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    return job_to_dict(job)

@router.get("/jobs")
def list_jobs(
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
    db: Session = Depends(get_db),
):
    statement = (
        select(Job)
        .where(
            Job.tenant_id
            == context.tenant.tenant_id,
            Job.user_id
            == context.membership.user_id,
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

@router.get("/jobs/{job_id}/executions")
def list_job_executions(
    job_id: str,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
    db: Session = Depends(get_db),
):
        job = db.scalar(
            select(Job).where(
                Job.job_id == job_id,
                Job.tenant_id
                == context.tenant.tenant_id,
                Job.user_id
                == context.membership.user_id,
            )
        )

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Job not found",
            )

        statement = (
            select(Execution)
            .where(
                Execution.job_id == job_id,
                Execution.user_id
                == context.membership.user_id,
            )
            .order_by(
                Execution.attempt.asc()
            )
        )

        executions = db.scalars(
            statement
        ).all()

        return [
            {
                "execution_id":
                    execution.execution_id,
                "status":
                    execution.status,
                "attempt":
                    execution.attempt,
                "provider":
                    execution.provider,
                "model":
                    execution.model,
                "metadata":
                    execution.metadata_json,
                "started_at":
                    execution.started_at.isoformat()
                    if execution.started_at
                    else None,
                "completed_at":
                    execution.completed_at.isoformat()
                    if execution.completed_at
                    else None,
                "error_message":
                    execution.error_message,
            }
            for execution in executions
        ]
