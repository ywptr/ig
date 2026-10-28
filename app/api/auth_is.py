import hmac

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Query,
)
from fastapi.responses import (
    RedirectResponse,
)
from sqlalchemy.orm import Session

from app.auth.dependencies import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_db,
    get_optional_current_user,
)
from app.auth.is_oidc import (
    create_authorization_request,
    exchange_authorization_code,
    get_oidc_discovery,
    get_post_login_redirect,
    verify_id_token,
)
from app.auth.session import create_session
from app.db.models import User
from app.db.models import TenantMembership
from app.tenancy.context import (
    TenantContext,
)
from app.tenancy.dependencies import (
    get_tenant_context,
)
from app.tenancy.membership import (
    get_tenant_membership,
)
from app.users.identities import (
    create_user_identity,
    get_user_by_external_identity,
)
from app.users.service import (
    create_external_user,
    update_last_login,
)
from app.tenancy.settings import (
    get_tenant_settings,
)


router = APIRouter(
    prefix="/auth/is",
    tags=["auth-is"],
)


OIDC_STATE_COOKIE = (
    "ig_oidc_state"
)

OIDC_NONCE_COOKIE = (
    "ig_oidc_nonce"
)

OIDC_VERIFIER_COOKIE = (
    "ig_oidc_verifier"
)

OIDC_FLOW_COOKIE = (
    "ig_oidc_flow"
)


def set_transient_cookie(
    response: RedirectResponse,
    *,
    key: str,
    value: str,
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=10 * 60,
        path="/",
    )


def clear_transient_cookies(
    response: RedirectResponse,
) -> None:
    for key in (
        OIDC_STATE_COOKIE,
        OIDC_NONCE_COOKIE,
        OIDC_VERIFIER_COOKIE,
        OIDC_FLOW_COOKIE,
    ):
        response.delete_cookie(
            key,
            path="/",
        )


@router.get("/login")
def login_with_is():
    return start_is_flow(
        flow="login"
    )

@router.get("/link")
def link_is_identity(
    current_user: User = Depends(
        get_current_user
    ),
):
    return start_is_flow(
        flow="link"
    )

@router.get("/callback")
def is_callback(
    code: str | None = Query(
        default=None
    ),
    state: str | None = Query(
        default=None
    ),
    error: str | None = Query(
        default=None
    ),
    error_description: str | None = Query(
        default=None
    ),
    oidc_state: str | None = Cookie(
        default=None,
        alias=OIDC_STATE_COOKIE,
    ),
    oidc_nonce: str | None = Cookie(
        default=None,
        alias=OIDC_NONCE_COOKIE,
    ),
    oidc_verifier: str | None = Cookie(
        default=None,
        alias=OIDC_VERIFIER_COOKIE,
    ),
    oidc_flow: str | None = Cookie(
        default=None,
        alias=OIDC_FLOW_COOKIE,
    ),
    current_user: User | None = Depends(
        get_optional_current_user
    ),
    tenant_context: TenantContext = Depends(
        get_tenant_context
    ),
    db: Session = Depends(get_db),
):
    if error is not None:
        detail = error

        if error_description:
            detail += (
                f": {error_description}"
            )

        raise HTTPException(
            status_code=401,
            detail=detail,
        )

    if (
        code is None
        or state is None
        or oidc_state is None
        or oidc_nonce is None
        or oidc_verifier is None
        or oidc_flow is None
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Incomplete OIDC callback"
            ),
        )

    if not hmac.compare_digest(
        state,
        oidc_state,
    ):
        raise HTTPException(
            status_code=400,
            detail="OIDC state mismatch",
        )

    try:
        token_response = (
            exchange_authorization_code(
                code=code,
                code_verifier=(
                    oidc_verifier
                ),
            )
        )

        claims = verify_id_token(
            id_token=(
                token_response[
                    "id_token"
                ]
            ),
            expected_nonce=oidc_nonce,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail=(
                "IS authentication failed"
            ),
        ) from exc

    issuer = get_oidc_discovery()[
        "issuer"
    ]

    subject = str(
        claims["sub"]
    )

    user = (
        get_user_by_external_identity(
            db,
            issuer=issuer,
            subject=subject,
        )
    )

    if oidc_flow == "link":
        if current_user is None:
            raise HTTPException(
                status_code=401,
                detail=(
                    "An existing IG session is required to link IS identity"
                ),
            )

        if (
            user is not None
            and user.user_id
            != current_user.user_id
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This IS identity is already linked to another IG user"
                ),
            )

        if user is None:
            create_user_identity(
                db,
                user_id=current_user.user_id,
                issuer=issuer,
                subject=subject,
            )

        response = RedirectResponse(
            get_post_login_redirect(),
            status_code=302,
        )

        clear_transient_cookies(
            response
        )

        return response

    if oidc_flow != "login":
        raise HTTPException(
            status_code=400,
            detail="Unknown OIDC flow",
        )

    user, membership = (
        get_or_create_is_user(
            db,
            tenant_id=(
                tenant_context.tenant.tenant_id
            ),
            issuer=issuer,
            claims=claims,
        )
    )

    update_last_login(
        db,
        user,
    )

    _, session_token = create_session(
        db,
        user_id=user.user_id,
    )

    response = RedirectResponse(
        get_post_login_redirect(),
        status_code=302,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )

    clear_transient_cookies(
        response
    )

    return response

def start_is_flow(
    *,
    flow: str,
) -> RedirectResponse:
    (
        authorization_url,
        state,
        nonce,
        verifier,
    ) = create_authorization_request()

    response = RedirectResponse(
        authorization_url,
        status_code=302,
    )

    set_transient_cookie(
        response,
        key=OIDC_STATE_COOKIE,
        value=state,
    )

    set_transient_cookie(
        response,
        key=OIDC_NONCE_COOKIE,
        value=nonce,
    )

    set_transient_cookie(
        response,
        key=OIDC_VERIFIER_COOKIE,
        value=verifier,
    )

    set_transient_cookie(
        response,
        key=OIDC_FLOW_COOKIE,
        value=flow,
    )

    return response

def get_or_create_is_user(
    db: Session,
    *,
    tenant_id: str,
    issuer: str,
    claims: dict,
) -> tuple[User, TenantMembership]:
    subject = str(
        claims["sub"]
    )

    user = get_user_by_external_identity(
        db,
        issuer=issuer,
        subject=subject,
    )

    settings = get_tenant_settings(
        db,
        tenant_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Tenant settings are not configured"
            ),
        )

    # Existing IS identity.
    if user is not None:
        membership = db.get(
            TenantMembership,
            (
                tenant_id,
                user.user_id,
            ),
        )

        if membership is not None:
            if membership.status != "active":
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Tenant membership is inactive"
                    ),
                )

            return user, membership

        if not settings.allow_self_registration:
            raise HTTPException(
                status_code=403,
                detail=(
                    "User is not admitted to this IG tenant"
                ),
            )

        membership = TenantMembership(
            tenant_id=tenant_id,
            user_id=user.user_id,
            role="member",
            status="active",
        )

        db.add(membership)
        db.commit()
        db.refresh(membership)

        return user, membership

    # New IS identity.
    #
    # Do not create an orphan IG principal when
    # this tenant does not permit admission.
    if not settings.allow_self_registration:
        raise HTTPException(
            status_code=403,
            detail=(
                "This tenant does not allow self-registration"
            ),
        )

    email = claims.get("email")

    if (
        not isinstance(email, str)
        or not email.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "IS identity does not contain an email address"
            ),
        )

    name = claims.get("name")

    if not isinstance(name, str):
        name = None

    picture_url = claims.get("picture")

    if not isinstance(
        picture_url,
        str,
    ):
        picture_url = None

    user = create_external_user(
        db,
        email=email,
        name=name,
        picture_url=picture_url,
    )

    create_user_identity(
        db,
        user_id=user.user_id,
        issuer=issuer,
        subject=subject,
    )

    membership = TenantMembership(
        tenant_id=tenant_id,
        user_id=user.user_id,
        role="member",
        status="active",
    )

    db.add(membership)
    db.commit()
    db.refresh(membership)

    return user, membership