import logging

from app.images.service import submit_image_generation
from fastapi import APIRouter, HTTPException, Depends
from pathlib import Path
# FileResponse supports legacy local artifacts.
# StreamingResponse supports provider-backed artifact content.
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ImageRequest
# 20261001
# Removed import of OpenAIImageService to avoid runtime import errors in environments where OpenAIImageService is not yet initialized.
# The service is now initialized at the module level to ensure it is available when the API routes are defined.
# from app.services.openai_images import OpenAIImageService
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_authenticated_tenant_context,
)
from app.artifacts.service import get_artifact
from app.artifacts.storage.provider import artifact_store

router = APIRouter()
logger = logging.getLogger(__name__)

# image_service = OpenAIImageService()
LEGACY_OUTPUT_DIR = Path("/app/output")

class ImageRequestPayload(BaseModel):
    prompt: str


def image_to_dict(image: ImageRequest):
    return {
        "request_id": image.request_id,
        "created_at": image.created_at.isoformat(),
        "prompt": image.prompt,
        "model": image.model,
        "status": image.status,
        "filename": image.filename,
        "mime_type": image.mime_type,
        "size_bytes": image.size_bytes,
        "generation_time_ms": image.generation_time_ms,
    }

@router.post("/images/generations", status_code=202)
def generate_image(
    request: ImageRequestPayload,
    context: TenantContext = Depends(get_authenticated_tenant_context),
):
    image_record = submit_image_generation(
        prompt=request.prompt,
        tenant_id=context.tenant.tenant_id,
        user_id=context.membership.user_id,
    )

    return image_to_dict(image_record)


@router.get("/images")
def list_images(
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    db = SessionLocal()

    try:
        statement = (
            select(ImageRequest)
            .where(
                ImageRequest.user_id == context.membership.user_id,
            )
            .order_by(ImageRequest.created_at.desc())
        )

        images = db.scalars(statement).all()

        return [
            image_to_dict(image)
            for image in images
        ]

    finally:
        db.close()


@router.get("/images/{request_id}")
def get_image(
    request_id: str,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id,
            ImageRequest.user_id == context.membership.user_id,
        )

        image = db.scalar(statement)

        if image is None:
            raise HTTPException(
                status_code=404,
                detail="Image request not found",
            )

        return image_to_dict(image)

    finally:
        db.close()


@router.get("/images/{request_id}/content")
def get_image_content(
    request_id: str,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id,
            ImageRequest.user_id == context.membership.user_id
        )

        image = db.scalar(statement)

        if image is None:
            raise HTTPException(
                status_code=404,
                detail="Image request not found",
            )

        if image.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"Image is not available; status={image.status}",
            )

        if image.artifact_id:
            artifact = get_artifact(
                image.artifact_id,
                user_id=context.membership.user_id,
            )

            if artifact is None:
                raise HTTPException(
                    status_code=404,
                    detail="Artifact not found",
                )

            if not artifact_store.exists(
                artifact.storage_uri
            ):
                raise HTTPException(
                    status_code=404,
                    detail="Artifact content is missing",
                )

            content = artifact_store.iter_bytes(
                artifact.storage_uri
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
                        f'{artifact.filename or image.request_id}'
                        f'"'
                    )
                },
            )

        # Legacy compatibility: serve from local storage if no artifact is present in blob storage
        if not image.filename:
            raise HTTPException(
                status_code=404,
                detail="Image file not found",
            )

        #filepath = image_service.output_dir / image.filename
        filepath = LEGACY_OUTPUT_DIR / image.filename

        if not filepath.exists():
            raise HTTPException(
                status_code=404,
                detail="Image file is missing",
            )

        return FileResponse(
            path=filepath,
            media_type=image.mime_type or "image/png",
            filename=image.filename,
        )

    finally:
        db.close()