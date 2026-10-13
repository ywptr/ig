from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from app.media.schemas import (
    MediaGenerationRequest,
)
from app.media.service import (
    MediaCapabilityNotAvailableError,
    submit_media_generation,
)
from app.media.validation import (
    MediaRequestValidationError,
)
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_authenticated_tenant_context,
)


router = APIRouter(
    prefix="/media",
    tags=["media"],
)


@router.post(
    "/generations",
    status_code=202,
)
def generate_media(
    request: MediaGenerationRequest,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    try:
        submission = submit_media_generation(
            request,
            tenant_id=context.tenant.tenant_id,
            user_id=context.membership.user_id,
        )

    except MediaRequestValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_MEDIA_REQUEST",
                "message": str(exc),
            },
        ) from exc

    except MediaCapabilityNotAvailableError as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "CAPABILITY_NOT_AVAILABLE",
                "capability": exc.capability,
            },
        ) from exc

    return {
        "media_type": submission.media_type,
        "request_id": submission.request_id,
        "status": submission.status,
    }