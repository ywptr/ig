from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.services.openai_images import OpenAIImageService

from sqlalchemy import select

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

        try:
            result = image_provider.generate(prompt)

            image_record.status = "completed"
            image_record.filename = result["filename"]
            image_record.mime_type = "image/png"
            image_record.size_bytes = result["size_bytes"]

            db.commit()

        except Exception:
            image_record.status = "failed"
            db.commit()
            raise

    finally:
        db.close()