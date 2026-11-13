"""Caller and human actor stay distinct; ownership always uses the human actor."""
from dataclasses import dataclass, field
from typing import Literal

from app.db.models import User
from app.tenancy.context import TenantContext


@dataclass(frozen=True)
class RequestPrincipal(TenantContext):
    caller_type: Literal["browser", "service", "editor"] = "browser"
    actor_user: User | None = None
    actor_user_id: str | None = None
    tenant_id: str | None = None
    service_id: str | None = None
    credential_id: str | None = None
    identity_id: str | None = None
    actor_issuer: str | None = None
    actor_subject: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    editor_session_id: str | None = None
