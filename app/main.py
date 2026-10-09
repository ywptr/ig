import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends

from app.tenancy.context import TenantContext
from app.tenancy.dependencies import get_tenant_context,get_authenticated_tenant_context

from app.api.auth import router as auth_router
from app.api.images import router as image_router
from app.api.jobs import router as job_router
from app.api.artifacts import (
    router as artifact_router,
)
from app.api.executions import (
    router as execution_router,
)

from app.api.tenant_settings import (
    router as tenant_settings_router,
)
from app.api.tenant_members import (
    router as tenant_members_router,
)
from app.api.tenant_invitations import (
    router as tenant_invitations_router,
)

from app.core.logging import configure_logging

configure_logging()

app = FastAPI(
    title="IG",
    version="0.1.0",
)


app.include_router(
    image_router,
    prefix="/v1"
)

app.include_router(
    job_router,
    prefix="/v2"
)

app.include_router(
    auth_router,
    prefix="/v2"
)

app.include_router(
    artifact_router,
    prefix="/v2",
)

app.include_router(
    execution_router,
    prefix="/v2",
)

app.include_router(
    tenant_settings_router,
    prefix="/v2",
)

app.include_router(
    tenant_members_router,
    prefix="/v2",
)

app.include_router(
    tenant_invitations_router,
    prefix="/v2",
)

@app.get("/v2/tenant")
def tenant_info(
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
):
    return {
        "tenant_id": context.tenant.tenant_id,
        "slug": context.tenant.slug,
        "name": context.tenant.name,
        "status": context.tenant.status,
        "membership": {
            "role": context.membership.role,
            "status": context.membership.status,
        },
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "queue_provider": os.getenv(
            "JOB_QUEUE_PROVIDER",
            "rq",
        ),
    }