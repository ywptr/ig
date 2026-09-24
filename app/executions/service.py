from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Execution
from app.jobs.result import HandlerResult

def create_execution(
    db: Session,
    *,
    job_id: str,
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
        user_id=user_id,
        status="running",
        attempt=attempt + 1,
        provider=provider,
        model=model,
        started_at=datetime.utcnow(),
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
    execution.completed_at = datetime.utcnow()


def fail_execution(
    db: Session,
    execution: Execution,
    *,
    error_message: str,
) -> None:
    execution.status = "failed"
    execution.error_message = error_message
    execution.completed_at = datetime.utcnow()

def apply_execution_result(
    execution: Execution,
    result: HandlerResult,
) -> None:
    execution.provider = result.provider
    execution.model = result.model

    existing_metadata = (
        execution.metadata_json or {}
    )

    execution.metadata_json = {
        **existing_metadata,
        **result.metadata,
    }