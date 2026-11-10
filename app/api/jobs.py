from typing import Any
from dataclasses import asdict
from app.core.time import utc_iso

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session


from sqlalchemy import select
from app.db.models import Job, Execution
from app.auth.dependencies import get_db
from app.jobs.handlers import is_publicly_submittable, is_supported
from app.jobs.service import create_job, get_job, cancel_job, CancellationConflict
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
        "cancel_requested_at": utc_iso(job.cancel_requested_at),
        "canceled_at": utc_iso(job.canceled_at),
        "created_at": utc_iso(job.created_at),
        "started_at": (
            utc_iso(job.started_at)
            if job.started_at
            else None
        ),
        "completed_at": (
            utc_iso(job.completed_at)
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

    if not is_publicly_submittable(request.capability):
        raise HTTPException(
            status_code=403,
            detail=(
                "Capability is internal and cannot be submitted directly. "
                "Use /v2/media/generations for media generation."
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
        job_id=job_id,
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

    return [{key: value for key, value in job_to_dict(job).items() if key != "input"} for job in jobs]


@router.post("/jobs/{job_id}/cancel")
def request_job_cancellation(
    job_id: str,
    context: TenantContext = Depends(get_authenticated_tenant_context),
    db: Session = Depends(get_db),
):
    try:
        result = cancel_job(db, job_id=job_id, tenant_id=context.tenant.tenant_id,
                            user_id=context.membership.user_id)
    except CancellationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job, transport = result
    return {**job_to_dict(job), "cancellation": {
        "queue": asdict(transport), "provider_cancel_supported": False,
    }}

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
                Execution.tenant_id == context.tenant.tenant_id,
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
                    utc_iso(execution.started_at)
                    if execution.started_at
                    else None,
                "completed_at":
                    utc_iso(execution.completed_at)
                    if execution.completed_at
                    else None,
                "error_message":
                    execution.error_message,
            }
            for execution in executions
        ]
