from typing import Any

from pydantic import BaseModel, Field, SecretStr


class TenantProviderConfigUpdate(BaseModel):
    enabled: bool = True

    credentials: dict[str, SecretStr] | None = None

    config: dict[str, Any] | None = None


class TenantProviderConfigResponse(BaseModel):
    provider: str
    enabled: bool
    has_credentials: bool

    config: dict[str, Any] = Field(
        default_factory=dict,
    )