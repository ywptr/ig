from uuid import uuid4
from app.core.time import utc_now

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Execution
from app.jobs.result import Completed, HandlerResult, Pending

def create_execution(
    db: Session,
    *,
    job_id: str,
    tenant_id: str,
    user_id: str,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict | None = None,
) -> Execution:
    attempt = db.scalar(
        select(func.count(Execution.execution_id))
        .where(Execution.job_id == job_id)
    ) or 0

    execution = Execution(
        execution_id=str(uuid4()),
        job_id=job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        status="running",
        attempt=attempt + 1,
        provider=provider,
        model=model,
        started_at=utc_now(),
        metadata_json=metadata,
    )

    db.add(execution)
    db.flush()

    return execution


def complete_execution(
    db: Session,
    execution: Execution,
) -> None:
    execution.status = "completed"
    execution.completed_at = utc_now()


def fail_execution(
    db: Session,
    execution: Execution,
    *,
    error_message: str,
) -> None:
    execution.status = "failed"
    execution.error_message = error_message
    execution.completed_at = utc_now()


def wait_execution(
    db: Session,
    execution: Execution,
) -> None:
    execution.status = "waiting"
    execution.completed_at = None


def apply_execution_result(
    execution: Execution,
    result: HandlerResult,
) -> None:
    if isinstance(result, Completed):
        metadata = result.metadata
    elif isinstance(result, Pending):
        metadata = {
            "provider_operation": {
                "operation_id": result.operation_id,
                "metadata": result.operation_metadata,
            },
            # Rotate on every committed checkpoint. Stale deliveries cannot
            # claim the next poll, even if the job is waiting again.
            "continuation": {"id": str(uuid4())},
        }
    else:
        raise TypeError("Handler must return Completed or Pending")

    execution.provider = result.provider
    execution.model = result.model

    existing_metadata = (
        execution.metadata_json or {}
    )

    execution.metadata_json = {
        **existing_metadata,
        **metadata,
    }
