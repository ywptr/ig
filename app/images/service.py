import uuid

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.jobs.service import create_job
from app.services.openai_images import OpenAIImageService


image_provider = OpenAIImageService()


def submit_image_generation(
    prompt: str,
    user_id: str,
    ) -> ImageRequest:
    request_id = str(uuid.uuid4())

    db = SessionLocal()

    try:
        image_record = ImageRequest(
            request_id=request_id,
            user_id=user_id,
            prompt=prompt,
            model="gpt-image-2",
            status="generating",
        )

        db.add(image_record)
        db.commit()
        db.refresh(image_record)

        try:
            create_job(
                capability="image.generate",
                input_data={
                    "request_id": request_id,
                    "prompt": prompt,
                },
                user_id=user_id,
            )

        except Exception:
            image_record.status = "failed"
            db.commit()
            raise

        return image_record

    finally:
        db.close()
