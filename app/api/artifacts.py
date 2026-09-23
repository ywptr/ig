from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.artifacts.service import (
    get_artifact,
    list_artifacts,
)
from app.auth.dependencies import get_current_user
from app.db.models import Artifact, User


router = APIRouter(
    prefix="/artifacts",
    tags=["artifacts"],
)


def artifact_to_dict(
    artifact: Artifact,
) -> dict:
    return {
        "artifact_id": artifact.artifact_id,
        "user_id": artifact.user_id,
        "media_type": artifact.media_type,
        "mime_type": artifact.mime_type,
        "storage_uri": artifact.storage_uri,
        "filename": artifact.filename,
        "size_bytes": artifact.size_bytes,
        "metadata": artifact.metadata_json,
        "parent_artifact_id": (
            artifact.parent_artifact_id
        ),
        "created_at": (
            artifact.created_at.isoformat()
        ),
    }


@router.get("")
def read_artifacts(
    current_user: User = Depends(
        get_current_user
    ),
):
    artifacts = list_artifacts(
        user_id=current_user.user_id
    )

    return [
        artifact_to_dict(artifact)
        for artifact in artifacts
    ]


@router.get("/{artifact_id}")
def read_artifact(
    artifact_id: str,
    current_user: User = Depends(
        get_current_user
    ),
):
    artifact = get_artifact(
        artifact_id,
        user_id=current_user.user_id,
    )

    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail="Artifact not found",
        )

    return artifact_to_dict(artifact)


@router.get("/{artifact_id}/content")
def read_artifact_content(
    artifact_id: str,
    current_user: User = Depends(
        get_current_user
    ),
):
    artifact = get_artifact(
        artifact_id,
        user_id=current_user.user_id,
    )

    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail="Artifact not found",
        )

    uri = urlparse(artifact.storage_uri)

    if uri.scheme != "file":
        raise HTTPException(
            status_code=501,
            detail=(
                "Artifact storage provider "
                "is not supported yet"
            ),
        )

    filepath = Path(uri.path)

    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail="Artifact content is missing",
        )

    return FileResponse(
        path=filepath,
        media_type=(
            artifact.mime_type
            or "application/octet-stream"
        ),
        filename=artifact.filename,
    )