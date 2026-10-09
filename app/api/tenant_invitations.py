from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
)
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.auth.dependencies import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_db,
)
from app.auth.sessions import create_session
from app.db.models import (
    TenantInvitation,
    User,
)
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_admin_tenant_context,
)
from app.tenancy.invitations import (
    create_tenant_invitation,
    list_tenant_invitations,
    revoke_tenant_invitation,
    get_invitation_by_token,
    accept_invitation,
    validate_invitation,
)
from app.users.service import (
    create_user,
    get_user_by_email,
)

router = APIRouter(
    prefix="/tenant/invitations",
    tags=["tenant-invitations"],
)


class TenantInvitationCreate(BaseModel):
    email: EmailStr
    role: str = "member"

class TenantInvitationAccept(BaseModel):
    token: str

class TenantInvitationRegister(BaseModel):
    token: str
    password: str
    name: str | None = None

def invitation_to_dict(
    invitation: TenantInvitation,
) -> dict:
    return {
        "invitation_id": (
            invitation.invitation_id
        ),
        "email": invitation.email,
        "role": invitation.role,
        "status": invitation.status,
        "expires_at": (
            invitation.expires_at.isoformat()
        ),
        "created_at": (
            invitation.created_at.isoformat()
        ),
        "accepted_at": (
            invitation.accepted_at.isoformat()
            if invitation.accepted_at
            else None
        ),
    }


@router.post("")
def create_invitation(
    request: TenantInvitationCreate,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    if request.role not in {
        "admin",
        "member",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid tenant role",
        )

    try:
        invitation, raw_token = (
            create_tenant_invitation(
                db,
                tenant_id=(
                    context.tenant.tenant_id
                ),
                email=request.email,
                role=request.role,
                invited_by_user_id=(
                    context.membership.user_id
                ),
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    result = invitation_to_dict(
        invitation
    )

    # Temporary during V3 development.
    # Later this token will be delivered by email.
    result["token"] = raw_token

    return result


@router.get("")
def list_invitations(
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    invitations = list_tenant_invitations(
        db,
        tenant_id=(
            context.tenant.tenant_id
        ),
    )

    return [
        invitation_to_dict(invitation)
        for invitation in invitations
    ]


@router.delete("/{invitation_id}")
def revoke_invitation(
    invitation_id: str,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    invitation = db.get(
        TenantInvitation,
        invitation_id,
    )

    if (
        invitation is None
        or invitation.tenant_id
        != context.tenant.tenant_id
    ):
        raise HTTPException(
            status_code=404,
            detail="Invitation not found",
        )

    if invitation.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=(
                "Only pending invitations "
                "can be revoked"
            ),
        )

    invitation = revoke_tenant_invitation(
        db,
        invitation,
    )

    return invitation_to_dict(
        invitation
    )

@router.post("/accept")
def accept_existing_user_invitation(
    request: TenantInvitationAccept,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(get_db),
):
    invitation = get_invitation_by_token(
        db,
        request.token,
    )

    if invitation is None:
        raise HTTPException(
            status_code=404,
            detail="Invitation not found",
        )

    try:
        membership = accept_invitation(
            db,
            invitation=invitation,
            user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    return {
        "tenant_id": membership.tenant_id,
        "user_id": membership.user_id,
        "role": membership.role,
        "status": membership.status,
    }

@router.post("/register")
def register_from_invitation(
    request: TenantInvitationRegister,
    response: Response,
    db: Session = Depends(get_db),
):
    invitation = get_invitation_by_token(
        db,
        request.token,
    )

    if invitation is None:
        raise HTTPException(
            status_code=404,
            detail="Invitation not found",
        )

    # Validate status and expiry before creating
    # any user account.
    try:
        validate_invitation(
            invitation
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    existing_user = get_user_by_email(
        db,
        invitation.email,
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "User already exists. "
                "Log in and accept the invitation."
            ),
        )

    if len(request.password) < 8:
        raise HTTPException(
            status_code=400,
            detail=(
                "Password must be at least "
                "8 characters"
            ),
        )

    user = create_user(
        db,
        email=invitation.email,
        password=request.password,
        name=request.name,
    )

    try:
        membership = accept_invitation(
            db,
            invitation=invitation,
            user=user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    _, session_token = create_session(
        db,
        user_id=user.user_id,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )

    return {
        "user": {
            "user_id": user.user_id,
            "email": user.email,
            "name": user.name,
        },
        "membership": {
            "tenant_id": membership.tenant_id,
            "role": membership.role,
            "status": membership.status,
        },
    }