"""Credential resolution with short DB sessions; no login-token reuse."""
from sqlalchemy import select, update
from app.db.database import SessionLocal
from app.db.models import DriveConnection, TenantSettings, TenantMembership, Tenant
from app.core.time import utc_now
from app.security.secrets import decrypt_secret
from app.storage_integrations.google_drive import DriveClient, DriveError, token_request


def active_connection(db, user_id):
    return db.scalar(select(DriveConnection).where(DriveConnection.user_id == user_id, DriveConnection.active.is_(True)))


def require_capability(db, tenant_id):
    settings = db.get(TenantSettings, tenant_id)
    if settings is None or not settings.google_drive_enabled:
        raise DriveError("tenant_disabled")


def require_write_owner(db, tenant_id, user_id):
    require_capability(db, tenant_id)
    membership = db.get(TenantMembership, (tenant_id, user_id))
    tenant = db.get(Tenant, tenant_id)
    if not membership or membership.status != "active" or not tenant or tenant.status != "active":
        raise DriveError("owner_unavailable")


def report_error(connection_id, code):
    status = code if code in {"revoked", "permission_lost", "folder_unavailable"} else "unknown_error"
    with SessionLocal() as db:
        db.execute(update(DriveConnection).where(DriveConnection.connection_id == connection_id,
            DriveConnection.active.is_(True)).values(status=status, checked_at=utc_now()))
        db.commit()


def resolve_connection(connection_id, *, user_id=None, tenant_id=None, write=False):
    with SessionLocal() as db:
        connection = db.get(DriveConnection, connection_id)
        if connection is None or (user_id is not None and connection.user_id != user_id):
            raise DriveError("owner_mismatch")
        if not connection.active or not connection.encrypted_refresh_token:
            raise DriveError("disconnected")
        if write:
            require_write_owner(db, tenant_id, user_id)
            if connection.status != "healthy":
                raise DriveError(connection.status)
        encrypted, folder = connection.encrypted_refresh_token, connection.folder_id
    try:
        refresh = decrypt_secret(encrypted)
        token = token_request({"grant_type": "refresh_token", "refresh_token": refresh})
        return DriveClient(token["access_token"]), folder
    except DriveError as exc:
        report_error(connection_id, exc.code)
        raise
    except Exception:
        raise DriveError("credential_unavailable") from None
