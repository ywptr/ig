from datetime import datetime
from app.artifacts.retention import unavailable
from app.core.time import utc_iso
from app.artifacts.schemas import ArtifactProvenance
from app.artifacts.lineage import generations_for, inputs_for
from app.artifacts.library import artifact_page, InvalidCursor
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile, Request, Query
from starlette.concurrency import run_in_threadpool
from fastapi.responses import Response, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth.dependencies import get_db
from app.artifacts import previews
from app.artifacts.preview_policy import VERSION, MAX_PREVIEW_BYTES
from fastapi.responses import StreamingResponse
from pathlib import Path
from typing import Literal

from app.artifacts.service import (
    get_artifact,
    list_artifacts,
    create_uploaded_artifact,
)
from app.db.models import Artifact, ArtifactPreview
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
    preview: dict | None = None,
    generation: dict | None = None,
) -> dict:
    return {
        "artifact_id": artifact.artifact_id,
        "content_status": "removed" if artifact.content_removed_at else "removing" if artifact.cleanup_started_at else "active",
        "content_available": not unavailable(artifact),
        "content_removed_at": utc_iso(artifact.content_removed_at),
        "content_removal_reason": artifact.content_removal_reason,
        "provenance": artifact.provenance,
        "media_properties": {key: getattr(artifact, key) for key in ("width", "height", "duration_seconds", "fps")},
        "sha256": artifact.sha256,
        "generation": generation,
        "preview": preview if preview is not None else previews.preview_metadata(artifact),
        "user_id": artifact.user_id,
        "media_type": artifact.media_type,
        "mime_type": artifact.mime_type,
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
    db: Session = Depends(get_db),
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    artifacts = list_artifacts(
        user_id=context.membership.user_id,
        tenant_id=context.tenant.tenant_id,
    )

    metadata = previews.metadata_for_artifacts(db, artifacts)
    generation = generations_for(db, artifacts)
    return [artifact_to_dict(artifact, metadata[artifact.artifact_id], generation.get(artifact.artifact_id)) for artifact in artifacts]

@router.get("/page")
def read_artifact_page(
    page_size: int = Query(30, ge=1, le=100), cursor: str | None = Query(None, max_length=1024),
    media_type: Literal["image", "video", "audio", "text"] | None = None,
    provenance: ArtifactProvenance | None = None, q: str | None = Query(None, max_length=256),
    created_after: datetime | None = None, created_before: datetime | None = None,
    order: Literal["asc", "desc"] = "desc", db: Session = Depends(get_db),
    context: TenantContext = Depends(get_authenticated_tenant_context),
):
    try:
        artifacts, cursor, has_more = artifact_page(db, tenant_id=context.tenant.tenant_id,
            user_id=context.membership.user_id, page_size=page_size, cursor=cursor,
            media_type=media_type, provenance=provenance, q=q, created_after=created_after,
            created_before=created_before, order=order)
    except InvalidCursor:
        raise HTTPException(400, detail="Invalid artifact cursor") from None
    metadata, generation = previews.metadata_for_artifacts(db, artifacts), generations_for(db, artifacts)
    return {"items": [artifact_to_dict(row, metadata[row.artifact_id], generation.get(row.artifact_id)) for row in artifacts],
            "next_cursor": cursor, "has_more": has_more}


@router.post(
    "/uploads",
    status_code=201,
)
async def upload_artifact(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
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

    artifact = await run_in_threadpool(create_uploaded_artifact,
        tenant_id=context.tenant.tenant_id,
        user_id=context.membership.user_id,
        data=data,
        mime_type=mime_type,
        filename=filename,
        origin=origin,
    )

    return artifact_to_dict(
        artifact, previews.metadata_for_artifacts(db, [artifact])[artifact.artifact_id]
    )

@router.get("/{artifact_id}")
def read_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
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

    return {**artifact_to_dict(artifact, previews.metadata_for_artifacts(db, [artifact])[artifact.artifact_id],
                              generations_for(db, [artifact]).get(artifact.artifact_id)),
            "inputs": inputs_for(db, artifact)}


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

    if unavailable(artifact):
        raise HTTPException(410, detail={"code": "ARTIFACT_CONTENT_REMOVED", "reason": artifact.content_removal_reason},
                            headers={"Cache-Control": "private, no-store"})
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

@router.get("/{artifact_id}/preview")
def read_artifact_preview(
    artifact_id: str, request: Request,
    v: int = Query(default=VERSION, ge=1),
    context: TenantContext = Depends(get_authenticated_tenant_context),
    db: Session = Depends(get_db),
):
    artifact = db.scalar(select(Artifact).where(
        Artifact.artifact_id == artifact_id, Artifact.tenant_id == context.tenant.tenant_id,
        Artifact.user_id == context.membership.user_id,
    ))
    if artifact is None:
        raise HTTPException(404, detail="Artifact not found")
    private = {"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"}
    if unavailable(artifact):
        raise HTTPException(410, detail={"code": "ARTIFACT_CONTENT_REMOVED", "reason": artifact.content_removal_reason}, headers=private)
    kind = previews.kind_for(artifact.media_type, artifact.mime_type)
    if kind is None:
        return JSONResponse(previews.preview_metadata(artifact), status_code=415, headers=private)
    if v == VERSION:
        row = db.get(ArtifactPreview, (artifact_id, kind, v))
        # Compatibility for historical missing previews and expired claims only.
        # New producers persist a terminal preview before returning to callers.
        if row is None or row.status == "processing":
            row = previews.ensure_preview(db, artifact)
    else:
        # Previously generated versions remain readable after a policy upgrade.
        # Only the current policy is allowed to start new decoding work.
        row = db.get(ArtifactPreview, (artifact_id, kind, v))
        if row is None or row.status != "ready":
            return JSONResponse({"status": "unsupported", "code": "PREVIEW_VERSION_UNSUPPORTED"}, status_code=404, headers=private)
    metadata = previews.preview_metadata(artifact, row)
    if row is None or row.status == "processing":
        return JSONResponse({**metadata, "status": "processing"}, status_code=202,
                            headers={**private, "Retry-After": "3"})
    if row.status != "ready":
        return JSONResponse(metadata, status_code=422, headers=private)
    etag = f'"{row.etag}"'
    headers = {"Cache-Control": "private, no-cache", "Vary": "Cookie, Authorization", "ETag": etag}
    validators = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
    if etag in validators or "*" in validators:
        return Response(status_code=304, headers=headers)
    locator = row.storage_uri
    db.rollback()  # Do not hold DB transactions during storage access.
    try:
        chunks, total = [], 0
        for chunk in previews.artifact_store.iter_bytes(locator):
            total += len(chunk)
            if total > MAX_PREVIEW_BYTES:
                raise ValueError
            chunks.append(chunk)
        if not total:
            raise ValueError
    except Exception:
        return JSONResponse({"status": "failed", "code": "PREVIEW_CONTENT_UNAVAILABLE"}, status_code=404, headers=private)
    return Response(b"".join(chunks), media_type="image/webp", headers=headers)
