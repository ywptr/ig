import logging

from app.images.service import submit_image_generation
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ImageRequest, User
from app.services.openai_images import OpenAIImageService
from app.auth.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

image_service = OpenAIImageService()


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
    current_user: User = Depends(get_current_user),
):
    image_record = submit_image_generation(
        prompt=request.prompt,
        user_id=current_user.user_id
    )

    return image_to_dict(image_record)


@router.get("/images")
def list_images(
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        statement = (
            select(ImageRequest)
            .where(ImageRequest.user_id == current_user.user_id)
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
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id,
            ImageRequest.user_id == current_user.user_id
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
    current_user: User = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id,
            ImageRequest.user_id == current_user.user_id
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

        if not image.filename:
            raise HTTPException(
                status_code=404,
                detail="Image file not found",
            )

        filepath = image_service.output_dir / image.filename

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