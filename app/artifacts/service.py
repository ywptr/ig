from uuid import uuid4
from app.artifacts.orphans import track_write
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import Artifact
from app.artifacts.storage.provider import artifact_store
from app.artifacts.types import ArtifactPayload
from app.artifacts.media_metadata import inspect_media
from app.artifacts.schemas import ArtifactProvenance
from app.artifacts.lineage import record_generation
from app.artifacts.previews import generate_preview_for_artifact


def stage_media_result(payload: ArtifactPayload) -> tuple[str, str]:
    """Upload bounded output outside a database transaction."""
    if not re.fullmatch(r"[a-z0-9]{1,10}", payload.extension) or not payload.data:
        raise ValueError("Invalid artifact output")
    filename = f"{uuid4()}.{payload.extension}"
    return artifact_store.put_bytes(filename=filename, data=payload.data), filename


def persist_media_result(
    db: Session, *, payload: ArtifactPayload, storage_uri: str, filename: str,
    tenant_id: str, user_id: str, provider: str, model: str,
    job_id: str | None = None, execution_id: str | None = None, inputs=(), media_properties: dict | None = None,
) -> Artifact:
    """Persist a staged result in the caller's fenced completion transaction."""
    artifact = create_artifact(
        db, tenant_id=tenant_id, user_id=user_id, media_type=payload.media_type,
        mime_type=payload.mime_type, storage_uri=storage_uri, filename=filename,
        size_bytes=len(payload.data),
        metadata={**payload.metadata, "provider": provider, "model": model},
        provenance=ArtifactProvenance.GENERATED, media_properties=media_properties,
    )
    record_generation(db, artifact, inputs=inputs, job_id=job_id, execution_id=execution_id)
    return artifact


def create_artifact(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    media_type: str,
    storage_uri: str,
    mime_type: str | None = None,
    filename: str | None = None,
    size_bytes: int | None = None,
    metadata: dict | None = None,
    parent_artifact_id: str | None = None,
    provenance: ArtifactProvenance = ArtifactProvenance.UNKNOWN,
    media_properties: dict | None = None,
) -> Artifact:
    artifact = Artifact(
        artifact_id=str(uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        media_type=media_type,
        mime_type=mime_type,
        storage_uri=storage_uri,
        filename=filename,
        size_bytes=size_bytes,
        metadata_json=metadata,
        parent_artifact_id=parent_artifact_id,
        provenance=provenance,
        **{key: value for key, value in (media_properties or {}).items()
           if key in {"width", "height", "duration_seconds", "fps", "sha256"}},
    )

    db.add(artifact)
    db.flush()

    return artifact


def get_artifact(
    artifact_id: str,
    *,
    tenant_id: str,
    user_id: str,
) -> Artifact | None:
    db = SessionLocal()

    try:
        statement = select(Artifact).where(
            Artifact.artifact_id == artifact_id,
            Artifact.user_id == user_id,
            Artifact.tenant_id == tenant_id,
        )

        return db.scalar(statement)

    finally:
        db.close()


def list_artifacts(
    *,
    user_id: str,
    tenant_id: str,
    limit: int = 100,
) -> list[Artifact]:
    db = SessionLocal()

    try:
        statement = (
            select(Artifact)
            .where(
                Artifact.user_id == user_id,
                Artifact.tenant_id == tenant_id
            )
            .order_by(Artifact.created_at.desc(), Artifact.artifact_id.desc())
            .limit(limit)
        )

        return list(db.scalars(statement).all())

    finally:
        db.close()

def create_uploaded_artifact(
    *,
    tenant_id: str,
    user_id: str,
    data: bytes,
    mime_type: str,
    filename: str,
    origin: str,
) -> Artifact:
    db = SessionLocal()

    try:
        # The store allocates an immutable key; filename remains display metadata.
        storage_uri = artifact_store.put_bytes(
            filename=filename,
            data=data,
        )

        track_write(db, storage_uri, artifact_store, sessions=SessionLocal)
        artifact = create_artifact(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            media_type="image",
            provenance=ArtifactProvenance.UPLOADED,
            media_properties=inspect_media(data, "image"),
            storage_uri=storage_uri,
            mime_type=mime_type,
            filename=filename,
            size_bytes=len(data),
            metadata={
                "origin": origin,
                "ingest_method": "upload",
            },
        )

        db.commit()
        generate_preview_for_artifact(db, artifact)
        db.refresh(artifact)

        return artifact

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
