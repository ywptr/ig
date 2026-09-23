from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import Artifact


def create_artifact(
    db: Session,
    *,
    user_id: str,
    media_type: str,
    storage_uri: str,
    mime_type: str | None = None,
    filename: str | None = None,
    size_bytes: int | None = None,
    metadata: dict | None = None,
    parent_artifact_id: str | None = None,
) -> Artifact:
    artifact = Artifact(
        artifact_id=str(uuid4()),
        user_id=user_id,
        media_type=media_type,
        mime_type=mime_type,
        storage_uri=storage_uri,
        filename=filename,
        size_bytes=size_bytes,
        metadata_json=metadata,
        parent_artifact_id=parent_artifact_id,
    )

    db.add(artifact)
    db.flush()

    return artifact


def get_artifact(
    artifact_id: str,
    *,
    user_id: str,
) -> Artifact | None:
    db = SessionLocal()

    try:
        statement = select(Artifact).where(
            Artifact.artifact_id == artifact_id,
            Artifact.user_id == user_id,
        )

        return db.scalar(statement)

    finally:
        db.close()


def list_artifacts(
    *,
    user_id: str,
    limit: int = 100,
) -> list[Artifact]:
    db = SessionLocal()

    try:
        statement = (
            select(Artifact)
            .where(Artifact.user_id == user_id)
            .order_by(Artifact.created_at.desc())
            .limit(limit)
        )

        return list(db.scalars(statement).all())

    finally:
        db.close()