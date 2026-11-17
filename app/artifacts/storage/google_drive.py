"""Drive adapter bound to an explicit connection, never a request/session."""
from urllib.parse import urlparse
from uuid import UUID
from app.storage_integrations.google_drive import DriveError, valid_id


def parse_locator(uri):
    parsed = urlparse(uri)
    try:
        if (parsed.scheme != "gdrive" or str(UUID(parsed.netloc)) != parsed.netloc
            or parsed.query or parsed.fragment or parsed.params or not parsed.path.startswith("/")):
            raise ValueError
        return parsed.netloc, valid_id(parsed.path[1:])
    except (ValueError, TypeError, AttributeError):
        raise DriveError("invalid_locator") from None


class GoogleDriveArtifactStore:
    retention_policy_scope = "external"
    retention_default_enabled = False
    retention_default_days = 30

    def __init__(self, resolve, *, connection_id=None, user_id=None, tenant_id=None, report_error=None):
        self.resolve = resolve
        self.connection_id = connection_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.report_error = report_error

    def _client(self, connection_id, write=False):
        return self.resolve(connection_id, user_id=self.user_id, tenant_id=self.tenant_id, write=write)

    def put_bytes(self, *, filename, data):
        if self.connection_id is None or self.user_id is None or self.tenant_id is None:
            raise DriveError("owner_required")
        try:
            client, folder_id = self._client(self.connection_id, write=True)
            identity = client.upload(folder_id, filename, data)
            return f"gdrive://{self.connection_id}/{identity}"
        except DriveError as exc:
            self._failed(self.connection_id, exc)
            raise

    def _failed(self, identity, exc):
        if self.report_error and exc.code not in {"not_found", "invalid_locator", "tenant_disabled", "owner_mismatch"}:
            self.report_error(identity, exc.code)

    def stat(self, storage_uri):
        identity, file_id = parse_locator(storage_uri)
        try:
            client, _ = self._client(identity)
            return client.stat(file_id)
        except DriveError as exc:
            self._failed(identity, exc)
            raise

    def exists(self, storage_uri):
        try:
            return not self.stat(storage_uri).get("trashed", False)
        except DriveError as exc:
            if exc.code == "not_found":
                return False
            raise

    def iter_bytes(self, storage_uri, *, chunk_size=64 * 1024):
        identity, file_id = parse_locator(storage_uri)
        try:
            client, _ = self._client(identity)
            yield from client.iter_bytes(file_id, chunk_size)
        except DriveError as exc:
            self._failed(identity, exc)
            raise

    def delete(self, storage_uri):
        identity, file_id = parse_locator(storage_uri)
        try:
            client, _ = self._client(identity)
            client.delete(file_id)
        except DriveError as exc:
            self._failed(identity, exc)
            raise
