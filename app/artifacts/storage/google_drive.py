"""Resolve IG's persisted Drive connection and delegate object operations."""
from contextlib import contextmanager
from uuid import UUID

from ig_storage import GoogleDriveConfig, GoogleDriveStore
from ig_storage.backends.drive_client import DriveError
from ig_storage.backends.google_drive import parse_drive_locator
from ig_storage.contracts import PutResultMixin


def parse_locator(uri):
    connection, file_id = parse_drive_locator(uri)
    try:
        if str(UUID(connection)) != connection:
            raise ValueError
    except ValueError:
        raise DriveError("invalid_locator") from None
    return connection, file_id


class GoogleDriveArtifactStore(PutResultMixin):
    capabilities = GoogleDriveStore.capabilities
    retention_policy_scope = "external"
    retention_default_enabled = False
    retention_default_days = 30

    def __init__(self, resolve, *, connection_id=None, user_id=None, tenant_id=None, report_error=None):
        self.resolve = resolve
        self.connection_id = connection_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.report_error = report_error

    @contextmanager
    def _configured_store(self, identity, *, write=False):
        try:
            client, folder_id = self.resolve(
                identity, user_id=self.user_id, tenant_id=self.tenant_id, write=write,
            )
            yield GoogleDriveStore(GoogleDriveConfig(identity, folder_id), client=client)
        except DriveError as exc:
            if self.report_error and exc.code not in {"not_found", "invalid_locator", "tenant_disabled", "owner_mismatch"}:
                self.report_error(identity, exc.code)
            raise

    def put_bytes(self, *, filename, data):
        if self.connection_id is None or self.user_id is None or self.tenant_id is None:
            raise DriveError("owner_required")
        with self._configured_store(self.connection_id, write=True) as store:
            return store.put_bytes(filename=filename, data=data)

    def stat(self, storage_uri):
        identity, _ = parse_locator(storage_uri)
        with self._configured_store(identity) as store:
            return store.stat(storage_uri)

    def exists(self, storage_uri):
        try:
            return self.stat(storage_uri) is not None
        except DriveError as exc:
            if exc.code == "not_found":
                return False
            raise

    def iter_bytes(self, storage_uri, *, chunk_size=64 * 1024):
        identity, _ = parse_locator(storage_uri)
        with self._configured_store(identity) as store:
            yield from store.iter_bytes(storage_uri, chunk_size=chunk_size)

    def delete(self, storage_uri):
        identity, _ = parse_locator(storage_uri)
        with self._configured_store(identity) as store:
            store.delete(storage_uri)
