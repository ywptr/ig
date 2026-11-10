import uuid

from sqlalchemy.orm import Session
from app.db.models import ImageRequest
from app.jobs.service import create_job
from app.media.routing import resolve_image_selection
from app.media.schemas import MediaGenerationRequest
from app.media.validation import validate_media_request

def submit_image_generation(
    db: Session,
    *,
    prompt: str,
    tenant_id: str,
    user_id: str,
    media_request: dict | None = None,
    defer_dispatch: bool = False,
) -> ImageRequest:
    if media_request is None:
        job_input = {
            "request_version": "1", "media_type": "image", "interface_level": "easy",
            "intent": {"description": prompt},
            "output": {
                "quality": "standard", "aspect_ratio": None, "style": "auto", "count": 1,
                "layout": {"type": "single", "panel_count": None},
            },
            "references": [], "advanced": None,
        }
    else:
        job_input = dict(media_request)
    request = MediaGenerationRequest.model_validate({
        key: value for key, value in job_input.items() if key in MediaGenerationRequest.model_fields
    })
    validate_media_request(request)
    selection = resolve_image_selection(db, request, tenant_id=tenant_id)
    request_id = str(uuid.uuid4())
    job_input = {**job_input, "request_id": request_id, "selection": selection.model_dump()}

    image_record = ImageRequest(
        request_id=request_id, tenant_id=tenant_id, user_id=user_id,
        prompt=prompt, model=None, status="generating",
    )
    db.add(image_record)
    if defer_dispatch:
        db.flush()
    else:
        db.commit()
        db.refresh(image_record)

    try:
        create_job(db, capability="image.generate", input_data=job_input,
                   tenant_id=tenant_id, user_id=user_id, defer_dispatch=defer_dispatch)
    except Exception:
        if defer_dispatch:
            raise
        image_record.status = "failed"
        db.commit()
        raise
    return image_record
