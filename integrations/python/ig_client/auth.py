"""Short-lived signed service delegation. No token or key logging/caching."""
from dataclasses import dataclass, field
import re
import time
from typing import Callable, Iterable

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from .contract import AUDIENCE, MAX_TOKEN_SECONDS, SCOPES, TOKEN_TYPE


@dataclass(frozen=True)
class DelegatedContext:
    actor_issuer: str
    actor_subject: str
    tenant_id: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in
               (self.actor_issuer, self.actor_subject, self.tenant_id)):
            raise ValueError("Actor issuer, subject and tenant ID are required")
        if max(len(self.actor_issuer), len(self.actor_subject)) > 512:
            raise ValueError("Actor identity exceeds the IG contract limit")


def create_service_token(*, service_id: str, key_id: str, private_key,
                         context: DelegatedContext, scopes: Iterable[str],
                         audience: str = AUDIENCE, expiry_seconds: int = MAX_TOKEN_SECONDS) -> str:
    """Sign the existing RS256 contract; expiry is a lifetime, not a timestamp."""
    selected = frozenset(scopes)
    if not selected or not selected <= SCOPES:
        raise ValueError("Select nonempty IG service scopes")
    if type(expiry_seconds) is not int or not 0 < expiry_seconds <= MAX_TOKEN_SECONDS:
        raise ValueError("Token lifetime must be 1–300 seconds")
    if not isinstance(service_id, str) or not service_id.strip():
        raise ValueError("Service ID is required")
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", key_id):
        raise ValueError("Invalid service key ID")
    if not isinstance(audience, str) or not audience:
        raise ValueError("Audience must be a string")
    now = int(time.time())
    try:
        if isinstance(private_key, str):
            private_key = private_key.encode()
        if isinstance(private_key, bytes):
            private_key = serialization.load_pem_private_key(private_key, password=None)
        if not isinstance(private_key, RSAPrivateKey) or private_key.key_size < 2048:
            raise ValueError
        return jwt.encode({"iss": service_id, "sub": service_id, "aud": audience,
            "iat": now, "exp": now + expiry_seconds, "scope": " ".join(sorted(selected)),
            "act": {"iss": context.actor_issuer, "sub": context.actor_subject},
            "tenant_id": context.tenant_id}, private_key, algorithm="RS256",
            headers={"kid": key_id, "typ": TOKEN_TYPE})
    except Exception:
        raise ValueError("IG service signing failed; check the private key") from None


@dataclass(frozen=True)
class ServiceCredentials:
    service_id: str
    key_id: str
    private_key_source: Callable[[], object] = field(repr=False)
    audience: str = AUDIENCE
    expiry_seconds: int = MAX_TOKEN_SECONDS

    def token(self, context: DelegatedContext, scopes: Iterable[str]) -> str:
        try:
            key = self.private_key_source()
        except Exception:
            raise ValueError("IG private key source unavailable") from None
        return create_service_token(service_id=self.service_id, key_id=self.key_id,
            private_key=key, context=context, scopes=scopes, audience=self.audience,
            expiry_seconds=self.expiry_seconds)
