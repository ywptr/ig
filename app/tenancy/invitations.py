from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    TenantInvitation,
    TenantMembership,
    User,
)


def hash_invitation_token(
    token: str,
) -> str:
    return sha256(
        token.encode("utf-8")
    ).hexdigest()


def create_tenant_invitation(
    db: Session,
    *,
    tenant_id: str,
    email: str,
    role: str,
    invited_by_user_id: str,
) -> tuple[TenantInvitation, str]:
    email = email.strip().lower()

    # Do not invite somebody who is already a tenant member.
    existing_member = db.scalar(
        select(TenantMembership)
        .join(
            User,
            User.user_id
            == TenantMembership.user_id,
        )
        .where(
            TenantMembership.tenant_id
            == tenant_id,
            User.email == email,
        )
    )

    if existing_member is not None:
        raise ValueError(
            "User is already a member of this tenant"
        )

    # Do not create multiple live invitations
    # for the same tenant/email.
    existing_invitation = db.scalar(
        select(TenantInvitation).where(
            TenantInvitation.tenant_id
            == tenant_id,
            TenantInvitation.email == email,
            TenantInvitation.status
            == "pending",
        )
    )

    if existing_invitation is not None:
        raise ValueError(
            "A pending invitation already exists"
        )

    raw_token = token_urlsafe(32)

    invitation = TenantInvitation(
        invitation_id=str(uuid4()),
        tenant_id=tenant_id,
        email=email,
        role=role,
        status="pending",
        token_hash=hash_invitation_token(
            raw_token
        ),
        invited_by_user_id=(
            invited_by_user_id
        ),
        accepted_by_user_id=None,
        expires_at=(
            datetime.utcnow()
            + timedelta(days=7)
        ),
        created_at=datetime.utcnow(),
        accepted_at=None,
    )

    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    return invitation, raw_token


def list_tenant_invitations(
    db: Session,
    *,
    tenant_id: str,
) -> list[TenantInvitation]:
    return list(
        db.scalars(
            select(TenantInvitation)
            .where(
                TenantInvitation.tenant_id
                == tenant_id
            )
            .order_by(
                TenantInvitation.created_at.desc()
            )
        )
    )


def revoke_tenant_invitation(
    db: Session,
    invitation: TenantInvitation,
) -> TenantInvitation:
    invitation.status = "revoked"

    db.commit()
    db.refresh(invitation)

    return invitation