"""Narrow, credential-free tenant policy edits over the local technical catalog."""
from copy import deepcopy
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth.dependencies import get_db
from app.tenancy.dependencies import get_admin_tenant_context
from app.db.models import TenantProviderConfig
from app.media.capabilities import media_capabilities
from app.media.model_governance import ModelGovernanceError, provider_adapters
from app.tenancy.provider_configs import set_provider_config

router = APIRouter(prefix="/tenant/model-governance", tags=["tenant-model-governance"])


def governance_document(db, tenant_id):
    rows = {row.provider: row for row in db.scalars(select(TenantProviderConfig).where(TenantProviderConfig.tenant_id == tenant_id))}
    catalog = media_capabilities(db, tenant_id=tenant_id, configurations=rows)
    providers = {}
    for media in catalog.media_types:
        for provider in media.providers:
            row = rows.get(provider.id)
            if row is None:
                continue
            config = row.config_json if isinstance(row.config_json, dict) else {}
            restrictions = config.get("enabled_models", {})
            entry = providers.setdefault(provider.id, {"id": provider.id, "display_name": provider.display_name,
                "enabled": row.enabled, "has_credentials": bool(row.encrypted_credentials), "media": []})
            defaults = config.get("default_models", {})
            explicit = defaults.get(media.id) if isinstance(defaults, dict) and media.id in defaults else config.get("default_model") if media.id == "image" else None
            if not isinstance(explicit, str) or explicit not in {model.id for model in provider.models}:
                explicit = None
            entry["media"].append({"media_type": media.id, "default_override": explicit,
                "restricted": isinstance(restrictions, dict) and media.id in restrictions,
                "default_model": provider.default_model, "configuration_error": provider.configuration_error,
                "models": [model.model_dump(mode="json") for model in provider.models]})
    return {"providers": list(providers.values())}


@router.get("")
def read_governance(context=Depends(get_admin_tenant_context), db: Session = Depends(get_db)):
    return governance_document(db, context.tenant.tenant_id)


class ModelPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_type: Literal["image", "video", "audio"]
    enabled_models: list[str] | None = Field(default=None, max_length=100)
    default_model: str | None = Field(default=None, max_length=200)


@router.patch("/{provider}")
def update_governance(provider: str, body: ModelPolicyUpdate,
                      context=Depends(get_admin_tenant_context), db: Session = Depends(get_db)):
    if body.media_type not in provider_adapters(provider):
        raise HTTPException(400, "Provider does not support this media type")
    row = db.scalar(select(TenantProviderConfig).where(TenantProviderConfig.tenant_id == context.tenant.tenant_id,
        TenantProviderConfig.provider == provider).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Configure the tenant provider first")
    if not isinstance(row.config_json, dict):
        raise HTTPException(400, "Repair the provider configuration with the operator tool first")
    config = deepcopy(row.config_json)
    for key, value in (("enabled_models", body.enabled_models), ("default_models", body.default_model)):
        mapping = config.get(key, {})
        if not isinstance(mapping, dict):
            raise HTTPException(400, "Repair malformed provider governance with the operator tool first")
        if value is None:
            mapping.pop(body.media_type, None)
        else:
            mapping[body.media_type] = value
        config[key] = mapping
    # Explicitly clearing/replacing the image default supersedes the legacy field.
    if body.media_type == "image":
        config.pop("default_model", None)
    try:
        set_provider_config(db, tenant_id=row.tenant_id, provider=provider, enabled=row.enabled, config=config)
    except ModelGovernanceError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from None
    return governance_document(db, context.tenant.tenant_id)
