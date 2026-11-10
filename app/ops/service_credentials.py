"""Administrative public-key registration; never accepts private key material."""
import re
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from sqlalchemy.orm import Session

from app.auth.service_contract import SCOPES
from app.db.models import ServiceCredential, Tenant


def valid_actor_issuer(issuer: str) -> bool:
    if not isinstance(issuer, str) or not 0 < len(issuer) <= 512:
        return False
    try:
        parsed = urlsplit(issuer)
        return bool(parsed.hostname and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and (parsed.scheme == "https" or (parsed.scheme == "http"
                 and parsed.hostname in {"localhost", "127.0.0.1", "::1"})))
    except ValueError:
        return False


def register_credential(db: Session, *, key_id: str, service_id: str, public_key: bytes,
                        scopes: list[str], tenant_ids: list[str], actor_issuers: list[str]):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", key_id) or not re.fullmatch(r"[a-z0-9_-]{1,64}", service_id):
        raise ValueError("Invalid key/service identifier")
    key = serialization.load_pem_public_key(public_key)
    if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
        raise ValueError("An RSA public key of at least 2048 bits is required")
    if (not scopes or not set(scopes) <= SCOPES or not tenant_ids or not actor_issuers
        or any(not valid_actor_issuer(issuer) for issuer in actor_issuers)
        or any(db.get(Tenant, tenant) is None for tenant in tenant_ids)):
        raise ValueError("Invalid scopes, tenant authorization or actor issuers")
    pem = key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    row = db.get(ServiceCredential, key_id)
    if row is not None and (row.service_id != service_id or row.public_key != pem):
        raise ValueError("Key identity is immutable; rotate using a new key ID")
    if row is None:
        row = ServiceCredential(key_id=key_id, service_id=service_id, public_key=pem)
        db.add(row)
    row.enabled = True
    row.scopes, row.tenant_ids, row.actor_issuers = sorted(set(scopes)), sorted(set(tenant_ids)), sorted(set(actor_issuers))
    db.flush()
    return row
