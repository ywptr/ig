import uuid

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.jobs.service import create_job
from app.services.openai_images import OpenAIImageService


image_provider = OpenAIImageService()


def submit_image_generation(
    prompt: str,
    tenant_id: str,
    user_id: str,
    media_request: dict | None = None,
    ) -> ImageRequest:
    request_id = str(uuid.uuid4())

    db = SessionLocal()

    try:
        image_record = ImageRequest(
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            prompt=prompt,
            model="gpt-image-2",
            status="generating",
        )

        db.add(image_record)
        db.commit()
        db.refresh(image_record)

        try:
            if media_request is None:
                job_input = {
                    "request_version": "1",
                    "media_type": "image",
                    "request_id": request_id,
                    "interface_level": "easy",
                    "intent": {
                        "description": prompt,
                    },
                    "output": {
                        "quality": "standard",
                        "aspect_ratio": None,
                        "count": 1,
                        "layout": {
                            "type": "single",
                            "panel_count": None,
                        },
                    },
                    "references": [],
                    "advanced": None,
                }
            else:
                job_input = {
                    **media_request,
                    "request_id": request_id,
                }
            create_job(
                capability="image.generate",
                input_data=job_input,
                tenant_id=tenant_id,
                user_id=user_id,
            )

        except Exception:
            image_record.status = "failed"
            db.commit()
            raise

        return image_record

    finally:
        db.close()
