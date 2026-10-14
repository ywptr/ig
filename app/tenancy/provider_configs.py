from sqlalchemy.orm import Session

from app.db.models import TenantProviderConfig
from app.security.secrets import (
    decrypt_credentials,
    encrypt_credentials,
)


class ProviderNotConfiguredError(
    RuntimeError
):
    pass


def get_provider_config(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
) -> TenantProviderConfig | None:
    return db.get(
        TenantProviderConfig,
        (
            tenant_id,
            provider.strip().lower(),
        ),
    )


def get_provider_credentials(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
) -> dict[str, str]:
    config = get_provider_config(
        db,
        tenant_id=tenant_id,
        provider=provider,
    )

    if (
        config is None
        or not config.enabled
        or not config.encrypted_credentials
    ):
        raise ProviderNotConfiguredError(
            f"Provider is not configured "
            f"for this tenant: {provider}"
        )

    return decrypt_credentials(
        config.encrypted_credentials
    )


def set_provider_config(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    enabled: bool,
    credentials: dict[str, str] | None = None,
    config: dict | None = None,
) -> TenantProviderConfig:
    provider = provider.strip().lower()

    provider_config = get_provider_config(
        db,
        tenant_id=tenant_id,
        provider=provider,
    )

    if provider_config is None:
        provider_config = TenantProviderConfig(
            tenant_id=tenant_id,
            provider=provider,
            enabled=enabled,
            config_json=config or {},
        )
        db.add(
            provider_config
        )

    else:
        provider_config.enabled = enabled

        if config is not None:
            provider_config.config_json = config

    if credentials is not None:
        provider_config.encrypted_credentials = (
            encrypt_credentials(
                credentials
            )
        )

    db.commit()
    db.refresh(
        provider_config
    )

    return provider_config