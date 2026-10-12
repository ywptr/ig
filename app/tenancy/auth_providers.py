from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TenantAuthProvider


def get_tenant_auth_provider(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
) -> TenantAuthProvider | None:
    return db.get(
        TenantAuthProvider,
        (
            tenant_id,
            provider,
        ),
    )


def list_tenant_auth_providers(
    db: Session,
    *,
    tenant_id: str,
) -> list[TenantAuthProvider]:
    return list(
        db.scalars(
            select(TenantAuthProvider)
            .where(
                TenantAuthProvider.tenant_id
                == tenant_id
            )
            .order_by(
                TenantAuthProvider.provider.asc()
            )
        )
    )


def upsert_tenant_auth_provider(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    client_id: str,
    issuer: str | None = None,
    metadata: dict | None = None,
) -> TenantAuthProvider:
    auth_provider = get_tenant_auth_provider(
        db,
        tenant_id=tenant_id,
        provider=provider,
    )

    if auth_provider is None:
        auth_provider = TenantAuthProvider(
            tenant_id=tenant_id,
            provider=provider,
            client_id=client_id,
            issuer=issuer,
            metadata_json=metadata,
        )

        db.add(auth_provider)

    else:
        auth_provider.client_id = client_id
        auth_provider.issuer = issuer
        auth_provider.metadata_json = metadata

    db.commit()
    db.refresh(auth_provider)

    return auth_provider