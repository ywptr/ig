import uuid

from sqlalchemy.orm import Session
from app.db.models import ImageRequest
from app.jobs.service import create_job

def submit_image_generation(
    db: Session,
    *,
    prompt: str,
    tenant_id: str,
    user_id: str,
    media_request: dict | None = None,
    ) -> ImageRequest:
        request_id = str(uuid.uuid4())

    
        image_record = ImageRequest(
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            prompt=prompt,
            model=None,
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
                        "style": "auto",
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
                db,
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
