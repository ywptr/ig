from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pathlib import Path
from typing import Literal

from app.artifacts.service import (
    get_artifact,
    list_artifacts,
    create_uploaded_artifact,
)
from app.db.models import Artifact
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_authenticated_tenant_context,
)

from app.artifacts.storage.provider import (
    artifact_store,
)

router = APIRouter(
    prefix="/artifacts",
    tags=["artifacts"],
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

SUPPORTED_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

def validate_image_content(
    data: bytes,
    mime_type: str,
) -> None:
    if mime_type == "image/png":
        valid = data.startswith(
            b"\x89PNG\r\n\x1a\n"
        )

    elif mime_type == "image/jpeg":
        valid = data.startswith(
            b"\xff\xd8\xff"
        )

    elif mime_type == "image/webp":
        valid = (
            len(data) >= 12
            and data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
        )

    else:
        valid = False

    if not valid:
        raise HTTPException(
            status_code=400,
            detail="Uploaded content does not match its image type",
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
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    artifacts = list_artifacts(
        user_id=context.membership.user_id,
        tenant_id=context.tenant.tenant_id,
    )

    return [
        artifact_to_dict(artifact)
        for artifact in artifacts
    ]

@router.post(
    "/uploads",
    status_code=201,
)
async def upload_artifact(
    file: UploadFile = File(...),
    origin: Literal[
        "reference",
        "external_generation",
    ] = Form("reference"),
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    mime_type = (
        file.content_type
        or ""
    ).lower()

    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported image type. "
                "Supported types: PNG, JPEG, WebP"
            ),
        )

    data = await file.read(
        MAX_UPLOAD_BYTES + 1
    )

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty",
        )

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Uploaded file exceeds 10 MB limit",
        )

    validate_image_content(
        data,
        mime_type,
    )

    extension = SUPPORTED_IMAGE_TYPES[
        mime_type
    ]

    original_name = Path(
        file.filename or ""
    ).name

    if original_name:
        filename = original_name
    else:
        filename = (
            f"upload.{extension}"
        )

    artifact = create_uploaded_artifact(
        tenant_id=context.tenant.tenant_id,
        user_id=context.membership.user_id,
        data=data,
        mime_type=mime_type,
        filename=filename,
        origin=origin,
    )

    return artifact_to_dict(
        artifact
    )

@router.get("/{artifact_id}")
def read_artifact(
    artifact_id: str,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    artifact = get_artifact(
        artifact_id,
        user_id=context.membership.user_id,
        tenant_id=context.tenant.tenant_id,
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
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    artifact = get_artifact(
        artifact_id,
        user_id=context.membership.user_id,
        tenant_id=context.tenant.tenant_id,
    )

    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail="Artifact not found",
        )

    try:
        if not artifact_store.exists(
            artifact.storage_uri
        ):
            raise HTTPException(
                status_code=404,
                detail=(
                    "Artifact content is missing"
                ),
            )

        content = (
            artifact_store.iter_bytes(
                artifact.storage_uri
            )
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=501,
            detail=str(exc),
        )

    return StreamingResponse(
        content,
        media_type=(
            artifact.mime_type
            or "application/octet-stream"
        ),
        headers={
            "Content-Disposition": (
                f'inline; filename="'
                f'{artifact.filename or artifact.artifact_id}'
                f'"'
            )
        },
    )