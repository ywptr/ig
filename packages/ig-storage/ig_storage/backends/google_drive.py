from collections.abc import Iterator, Mapping
import re
from typing import Protocol

from .drive_client import DriveError, valid_id
from ..config import GoogleDriveConfig
from ..contracts import PutResultMixin, StorageCapabilities, validate_chunk_size
from ..locator import parse_locator
from ..result import Checksum, ObjectStat


class DriveRuntime(Protocol):
    def upload(self, folder_id: str, filename: str, data: bytes) -> str: ...
    def stat(self, file_id: str) -> Mapping[str, object]: ...
    def iter_bytes(self, file_id: str, chunk_size: int) -> Iterator[bytes]: ...
    def delete(self, file_id: str) -> None: ...


def parse_drive_locator(uri: str) -> tuple[str, str]:
    try:
        parsed = parse_locator(uri)
        if (parsed.scheme != "gdrive" or parsed.query or parsed.fragment
            or not parsed.path.startswith("/")):
            raise ValueError
        return valid_id(parsed.netloc), valid_id(parsed.path[1:])
    except ValueError:
        raise DriveError("invalid_locator") from None


class GoogleDriveStore(PutResultMixin):
    capabilities = StorageCapabilities(streaming_read=True, resumable_write=True, provider_checksum=True)

    def __init__(self, config: GoogleDriveConfig, *, client: DriveRuntime):
        self.namespace = valid_id(config.namespace)
        self.folder_id = config.folder_id
        self.client = client

    def put_bytes(self, *, filename: str, data: bytes) -> str:
        if self.folder_id is None:
            raise DriveError("folder_unavailable")
        file_id = valid_id(self.client.upload(valid_id(self.folder_id), filename, data))
        return f"gdrive://{self.namespace}/{file_id}"

    def _file_id(self, storage_uri: str) -> str:
        namespace, file_id = parse_drive_locator(storage_uri)
        if namespace != self.namespace:
            raise DriveError("invalid_locator")
        return file_id

    def stat(self, storage_uri: str) -> ObjectStat | None:
        try:
            metadata = self.client.stat(self._file_id(storage_uri))
        except DriveError as exc:
            if exc.code == "not_found":
                return None
            raise
        if metadata.get("trashed"):
            return None
        size = metadata.get("size")
        try:
            size_bytes = int(size) if isinstance(size, (str, int)) else None
        except ValueError:
            size_bytes = None
        content_type = metadata.get("mimeType")
        md5 = metadata.get("md5Checksum")
        return ObjectStat(
            size_bytes=size_bytes if size_bytes is not None and size_bytes >= 0 else None,
            content_type=content_type if isinstance(content_type, str) else None,
            checksum=Checksum("md5", md5) if isinstance(md5, str) and re.fullmatch(r"[0-9a-fA-F]{32}", md5) else None,
        )

    def exists(self, storage_uri: str) -> bool:
        return self.stat(storage_uri) is not None

    def iter_bytes(self, storage_uri: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        validate_chunk_size(chunk_size)
        yield from self.client.iter_bytes(self._file_id(storage_uri), chunk_size)

    def delete(self, storage_uri: str) -> None:
        self.client.delete(self._file_id(storage_uri))
