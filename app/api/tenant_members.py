from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.db.models import TenantMembership, User
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_admin_tenant_context,
)


router = APIRouter(
    prefix="/tenant/members",
    tags=["tenant-members"],
)


class TenantMemberUpdate(BaseModel):
    role: str | None = None
    status: str | None = None


def member_to_dict(
    membership: TenantMembership,
    user: User,
) -> dict:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "role": membership.role,
        "status": membership.status,
        "created_at": (
            membership.created_at.isoformat()
        ),
    }


@router.get("")
def list_tenant_members(
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    statement = (
        select(TenantMembership, User)
        .join(
            User,
            User.user_id
            == TenantMembership.user_id,
        )
        .where(
            TenantMembership.tenant_id
            == context.tenant.tenant_id
        )
        .order_by(
            TenantMembership.created_at.asc()
        )
    )

    rows = db.execute(statement).all()

    return [
        member_to_dict(membership, user)
        for membership, user in rows
    ]


@router.patch("/{user_id}")
def update_tenant_member(
    user_id: str,
    request: TenantMemberUpdate,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    membership = db.get(
        TenantMembership,
        (
            context.tenant.tenant_id,
            user_id,
        ),
    )

    if membership is None:
        raise HTTPException(
            status_code=404,
            detail="Tenant member not found",
        )

    # Prevent removal or deactivation of the last active tenant admin.
    currently_active_admin = (
        membership.role == "admin"
        and membership.status == "active"
    )
    will_remain_active_admin = (
        (
            request.role
            if request.role is not None
            else membership.role
        )
        == "admin"
        and
        (
            request.status
            if request.status is not None
            else membership.status
        )
        == "active"
    )
    if (
        currently_active_admin
        and not will_remain_active_admin
    ):
        active_admin_count = db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(
                TenantMembership.tenant_id
                == context.tenant.tenant_id,
                TenantMembership.role == "admin",
                TenantMembership.status == "active",
            )
        )
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Tenant must have at least "
                    "one active admin"
                ),
            )

    # If admin check is passed, then update the membership role and/or status
    if request.role is not None:
        if request.role not in {
            "admin",
            "member",
        }:
            raise HTTPException(
                status_code=400,
                detail="Invalid tenant role",
            )

        membership.role = request.role

    if request.status is not None:
        if request.status not in {
            "active",
            "inactive",
        }:
            raise HTTPException(
                status_code=400,
                detail="Invalid membership status",
            )

        membership.status = request.status

    db.commit()
    db.refresh(membership)

    user = db.get(
        User,
        membership.user_id,
    )

    if user is None:
        raise HTTPException(
            status_code=500,
            detail="Tenant member user record not found",
        )

    return member_to_dict(
        membership,
        user,
    )