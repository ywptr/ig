"""Select a writer from explicit owner context; adapters know no current user."""
from urllib.parse import urlparse
from app.db.models import User, DriveConnection
from app.storage_integrations.connections import active_connection, require_write_owner, resolve_connection, report_error
from app.storage_integrations.google_drive import DriveError
from app.artifacts.storage.google_drive import GoogleDriveArtifactStore, parse_locator


def select_write_store(db, *, tenant_id, user_id, managed_store, original_uri=None):
    # Derivatives stay with their original backend/connection even after preference changes.
    if original_uri and urlparse(original_uri).scheme != "gdrive":
        return managed_store.store_for_locator(original_uri) if hasattr(managed_store, "store_for_locator") else managed_store
    if original_uri:
        identity, _ = parse_locator(original_uri)
        connection = db.get(DriveConnection, identity)
    else:
        user = db.get(User, user_id)
        if user is None:
            raise DriveError("owner_unavailable")
        if user.artifact_storage_preference == "ig_managed":
            return managed_store
        if user.artifact_storage_preference != "google_drive":
            raise DriveError("invalid_preference")
        connection = active_connection(db, user_id)
    require_write_owner(db, tenant_id, user_id)
    if connection is None or connection.user_id != user_id:
        raise DriveError("owner_mismatch")
    if not connection.active or not connection.encrypted_refresh_token:
        raise DriveError("disconnected")
    if connection.status != "healthy":
        raise DriveError(connection.status)
    return GoogleDriveArtifactStore(resolve_connection, connection_id=connection.connection_id,
        tenant_id=tenant_id, user_id=user_id, report_error=report_error)
