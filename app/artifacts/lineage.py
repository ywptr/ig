"""Atomic relational producer/input recording with ownership validation."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import TypeAdapter

from app.artifacts.schemas import LineageRole
from app.db.models import Artifact, ArtifactGeneration, ArtifactInput, Execution, Job

ROLE = TypeAdapter(LineageRole)


def record_generation(db: Session, artifact: Artifact, *, inputs, job_id=None, execution_id=None):
    if job_id is not None or execution_id is not None:
        execution = db.scalar(select(Execution).join(Job, Job.job_id == Execution.job_id).where(
            Job.job_id == job_id, Execution.execution_id == execution_id,
            Job.tenant_id == artifact.tenant_id, Job.user_id == artifact.user_id,
            Execution.tenant_id == artifact.tenant_id, Execution.user_id == artifact.user_id,
        ))
        if execution is None:
            raise ValueError("Artifact producer ownership mismatch")
        db.add(ArtifactGeneration(artifact_id=artifact.artifact_id, job_id=job_id, execution_id=execution_id))
    ids = [item["artifact_id"] if isinstance(item, dict) else item.artifact_id for item in inputs]
    sources = {row.artifact_id: row for row in db.scalars(select(Artifact).where(
        Artifact.artifact_id.in_(ids), Artifact.tenant_id == artifact.tenant_id, Artifact.user_id == artifact.user_id,
    ))} if ids else {}
    for position, item in enumerate(inputs):
        values = item if isinstance(item, dict) else vars(item)
        source = values["artifact_id"]
        if source not in sources or source == artifact.artifact_id:
            raise ValueError("Artifact input is not accessible")
        role = ROLE.validate_python(values.get("role", "previous_output"))
        strength = values.get("strength")
        if strength is not None and strength not in {"loose", "normal", "strong"}:
            raise ValueError("Invalid reference strength")
        db.add(ArtifactInput(output_artifact_id=artifact.artifact_id, position=position,
            input_artifact_id=source, relation="reference", role=role, strength=strength))
    db.flush()


def generations_for(db: Session, artifacts: list[Artifact]) -> dict:
    if not artifacts:
        return {}
    # A corrupted historical producer must not reveal another owner's IDs.
    rows = db.scalars(select(ArtifactGeneration).join(Artifact).join(Job, Job.job_id == ArtifactGeneration.job_id)
        .join(Execution, Execution.execution_id == ArtifactGeneration.execution_id).where(
            Artifact.artifact_id.in_([row.artifact_id for row in artifacts]),
            Job.tenant_id == Artifact.tenant_id, Job.user_id == Artifact.user_id,
            Execution.tenant_id == Artifact.tenant_id, Execution.user_id == Artifact.user_id,
            Execution.job_id == Job.job_id,
        )).all()
    return {row.artifact_id: {"job_id": row.job_id, "execution_id": row.execution_id} for row in rows}


def inputs_for(db: Session, artifact: Artifact) -> list[dict]:
    rows = db.scalars(select(ArtifactInput).join(Artifact, Artifact.artifact_id == ArtifactInput.input_artifact_id).where(
        ArtifactInput.output_artifact_id == artifact.artifact_id,
        Artifact.tenant_id == artifact.tenant_id, Artifact.user_id == artifact.user_id,
    ).order_by(ArtifactInput.position)).all()
    return [{"artifact_id": row.input_artifact_id, "position": row.position, "relation": row.relation,
             "role": row.role, "strength": row.strength} for row in rows]
