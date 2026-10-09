from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.auth.dependencies import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_db,
)
from app.auth.password import verify_password
from app.auth.session import (
    create_session,
    get_session_by_token,
    revoke_session,
)
from app.db.models import User
from app.users.service import (
    create_user,
    get_user_by_email,
    update_last_login,
)
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import get_tenant_context
from app.tenancy.settings import get_tenant_settings

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    user_id: str
    email: str
    name: str | None


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

@router.post(
    "/register",
    response_model=UserResponse,
)
def register(
    request: RegisterRequest,
    tenant_context: TenantContext = Depends(
        get_tenant_context
    ),
    db: Session = Depends(get_db),
):
    settings = get_tenant_settings(
        db,
        tenant_context.tenant.tenant_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=500,
            detail="Tenant settings not configured",
        )

    if not settings.allow_self_registration:
        raise HTTPException(
            status_code=403,
            detail="Self-registration is disabled",
        )

    email = request.email.lower()

    existing_user = get_user_by_email(
        db,
        email,
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    if len(request.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    user = create_user(
        db,
        email=email,
        password=request.password,
        name=request.name,
    )

    # Now that we have created multi-tenancy model, user must belong to the tenant on which they are registering.
    # We will create a TenantMembership for the user with role "member" and status "active".
    from app.db.models import TenantMembership
    membership = TenantMembership(
        tenant_id=tenant_context.tenant.tenant_id,
        user_id=user.user_id,
        role="member",
        status="active",
    )

    db.add(membership)
    db.commit()

    return UserResponse(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
    )

@router.post(
    "/login",
    response_model=UserResponse,
)
def login(
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    email = request.email.lower()

    user = get_user_by_email(
        db,
        email,
    )

    if (
        user is None
        or user.password_hash is None
        or not verify_password(
            request.password,
            user.password_hash,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    update_last_login(
        db,
        user,
    )

    _, token = create_session(
        db,
        user_id=user.user_id,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )

    return user


@router.get(
    "/me",
    response_model=UserResponse,
)
def me(
    current_user: User = Depends(
        get_current_user
    ),
):
    return current_user


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if token is not None:
        session = get_session_by_token(
            db,
            token,
        )

        if session is not None:
            revoke_session(
                db,
                session,
            )

    response.delete_cookie(
        SESSION_COOKIE_NAME,
    )

    return {
        "status": "logged_out"
    }