from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.db.database import SessionLocal
from app.db.models import Execution, User


router = APIRouter(
    prefix="/executions",
    tags=["executions"],
)


def execution_to_dict(
    execution: Execution,
) -> dict:
    return {
        "execution_id": execution.execution_id,
        "job_id": execution.job_id,
        "user_id": execution.user_id,
        "status": execution.status,
        "attempt": execution.attempt,
        "provider": execution.provider,
        "model": execution.model,
        "metadata": execution.metadata_json,
        "started_at": (
            execution.started_at.isoformat()
            if execution.started_at
            else None
        ),
        "completed_at": (
            execution.completed_at.isoformat()
            if execution.completed_at
            else None
        ),
        "error_message": execution.error_message,
        "created_at": execution.created_at.isoformat(),
    }


@router.get("")
def list_executions(
    current_user: User = Depends(
        get_current_user
    ),
):
    db = SessionLocal()

    try:
        statement = (
            select(Execution)
            .where(
                Execution.user_id
                == current_user.user_id
            )
            .order_by(
                Execution.created_at.desc()
            )
            .limit(100)
        )

        executions = db.scalars(
            statement
        ).all()

        return [
            execution_to_dict(execution)
            for execution in executions
        ]

    finally:
        db.close()


@router.get("/{execution_id}")
def get_execution(
    execution_id: str,
    current_user: User = Depends(
        get_current_user
    ),
):
    db = SessionLocal()

    try:
        statement = select(Execution).where(
            Execution.execution_id
            == execution_id,
            Execution.user_id
            == current_user.user_id,
        )

        execution = db.scalar(statement)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail="Execution not found",
            )

        return execution_to_dict(
            execution
        )

    finally:
        db.close()