from sqlalchemy import select

from app.artifacts.service import create_artifact
from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.services.openai_images import OpenAIImageService

image_provider = OpenAIImageService()

def execute_image_generation(input_data: dict) -> None:
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

            storage_uri = (
                f"file:///app/output/"
                f"{result['filename']}"
            )

            artifact = create_artifact(
                db,
                user_id=image_record.user_id,
                media_type="image",
                storage_uri=storage_uri,
                mime_type="image/png",
                filename=result["filename"],
                size_bytes=result["size_bytes"],
                metadata={
                    "provider": "openai",
                    "model": result["model"],
                },
            )

            image_record.status = "completed"

            # Legacy artifact fields
            image_record.filename = result["filename"]
            image_record.mime_type = "image/png"
            image_record.size_bytes = result["size_bytes"]

            # New artifact relationship
            image_record.artifact_id = artifact.artifact_id

            db.commit()

        except Exception:
            db.rollback()

            # Record failure separately after rolling
            # back any partially-created artifact.
            image_record.status = "failed"
            db.commit()

    finally:
        db.close()