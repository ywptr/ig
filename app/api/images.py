import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.services.openai_images import OpenAIImageService


router = APIRouter()

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


def process_image_generation(request_id: str, prompt: str):
    started = time.perf_counter()

    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id
        )

        image_record = db.scalar(statement)

        if image_record is None:
            return

        try:
            result = image_service.generate(prompt)

            generation_time_ms = int(
                (time.perf_counter() - started) * 1000
            )

            image_record.status = "completed"
            image_record.filename = result["filename"]
            image_record.mime_type = "image/png"
            image_record.size_bytes = result["size_bytes"]
            image_record.generation_time_ms = generation_time_ms

            db.commit()

        except Exception:
            generation_time_ms = int(
                (time.perf_counter() - started) * 1000
            )

            image_record.status = "failed"
            image_record.generation_time_ms = generation_time_ms

            db.commit()

            print(
                f"Image generation failed: {request_id}",
                flush=True,
            )

    finally:
        db.close()


@router.post("/images/generations", status_code=202)
def generate_image(
    request: ImageRequestPayload,
    background_tasks: BackgroundTasks,
):
    request_id = str(uuid.uuid4())

    db = SessionLocal()

    try:
        image_record = ImageRequest(
            request_id=request_id,
            prompt=request.prompt,
            model="gpt-image-2",
            status="generating",
        )

        db.add(image_record)
        db.commit()
        db.refresh(image_record)

        background_tasks.add_task(
            process_image_generation,
            request_id,
            request.prompt,
        )

        return image_to_dict(image_record)

    finally:
        db.close()


@router.get("/images")
def list_images():
    db = SessionLocal()

    try:
        statement = (
            select(ImageRequest)
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
def get_image(request_id: str):
    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id
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
def get_image_content(request_id: str):
    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id
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