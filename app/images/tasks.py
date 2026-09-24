from sqlalchemy import select

from app.artifacts.service import create_artifact
from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.services.openai_images import OpenAIImageService
from app.artifacts.storage.provider import (
    artifact_store,
)
from app.jobs.result import HandlerResult

import uuid

image_provider = OpenAIImageService()

def execute_image_generation(input_data: dict) -> HandlerResult:
    request_id = input_data["request_id"]
    prompt = input_data["prompt"]

    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id
        )

        image_record = db.scalar(statement)

        if image_record is None:
            raise RuntimeError(
                f"Image request not found: {request_id}"
            )

        if image_record.user_id is None:
            raise RuntimeError(
                f"Image request has no owner: {request_id}"
        )

        try:
            result = image_provider.generate(prompt)

            filename = (
                f"{uuid.uuid4()}.png"
            )

            storage_uri = (
                artifact_store.put_bytes(
                    filename=filename,
                    data=result["data"],
                )
            )

            artifact = create_artifact(
                db,
                user_id=image_record.user_id,
                media_type="image",
                storage_uri=storage_uri,
                mime_type=result["mime_type"],
                filename=filename,
                size_bytes=result["size_bytes"],
                metadata={
                    "provider": "openai",
                    "model": result["model"],
                },
            )

            image_record.status = "completed"

            # Legacy compatibility fields
            image_record.filename = filename
            image_record.mime_type = (
                result["mime_type"]
            )
            image_record.size_bytes = (
                result["size_bytes"]
            )

            image_record.artifact_id = (
                artifact.artifact_id
            )

            db.commit()

            return HandlerResult(
                provider="openai",
                model=result["model"],
                metadata={
                    "artifact_id": artifact.artifact_id,
                    "request_id": request_id,
                },
            )

        except Exception:
            db.rollback()

            # Record failure separately after rolling
            # back any partially-created artifact.
            image_record.status = "failed"
            db.commit()

    finally:
        db.close()