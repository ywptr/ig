from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.media.capabilities import media_capabilities
from app.media.capability_schemas import MediaCapabilities
from app.media.providers.video import VideoProviderConfigurationError
from app.media.providers.base import ImageProviderConfigurationError
from app.tenancy.provider_configs import ProviderNotConfiguredError

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


@router.get("/capabilities", response_model=MediaCapabilities)
def get_media_capabilities(
    context: TenantContext = Depends(get_authenticated_tenant_context),
    db: Session = Depends(get_db),
):
    return media_capabilities(db, tenant_id=context.tenant.tenant_id)


@router.post(
    "/generations",
    status_code=202,
)
def generate_media(
    request: MediaGenerationRequest,
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
    db: Session = Depends(get_db),
):
    try:
        submission = submit_media_generation(
            request,
            db=db,
            tenant_id=context.tenant.tenant_id,
            user_id=context.membership.user_id,
        )

    except (MediaRequestValidationError, ImageProviderConfigurationError, VideoProviderConfigurationError, ProviderNotConfiguredError) as exc:
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

    response = {
        "media_type": submission.media_type,
        "request_id": submission.request_id,
        "status": submission.status,
    }
    if submission.job_id is not None:
        response["job_id"] = submission.job_id
    return response
