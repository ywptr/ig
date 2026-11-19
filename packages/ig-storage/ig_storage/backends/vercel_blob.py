from collections.abc import Iterator
from uuid import uuid4

from vercel.blob import BlobNotFoundError, delete, get, head, put

from ..config import VercelBlobConfig
from ..contracts import PutResultMixin, StorageCapabilities, validate_chunk_size
from ..locator import parse_locator
from ..result import ObjectStat

SCHEME = "vercel-blob"


class VercelBlobStore(PutResultMixin):
    # The pinned Python SDK buffers get() before returning content.
    capabilities = StorageCapabilities(streaming_read=False, resumable_write=False, provider_checksum=False)

    def __init__(self, config: VercelBlobConfig):
        self.config = config

    def _token(self) -> str:
        if not self.config.token:
            raise RuntimeError("Blob storage credentials are missing")
        return self.config.token

    def put_bytes(self, *, filename: str, data: bytes) -> str:
        key = f"{self.config.prefix.rstrip('/')}/{uuid4().hex}"
        result = put(key, data, access="private", add_random_suffix=True, token=self._token())
        return f"{SCHEME}:///{result.pathname}"

    def exists(self, storage_uri: str) -> bool:
        return self.stat(storage_uri) is not None

    def stat(self, storage_uri: str) -> ObjectStat | None:
        pathname = self._to_pathname(storage_uri)
        try:
            result = head(pathname, token=self._token())
        except BlobNotFoundError:
            return None
        return ObjectStat(size_bytes=result.size, content_type=result.content_type, etag=getattr(result, "etag", None))

    def iter_bytes(self, storage_uri: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        validate_chunk_size(chunk_size)
        pathname = self._to_pathname(storage_uri)
        options = {"timeout": self.config.read_timeout} if self.config.read_timeout is not None else {}
        result = get(pathname, access="private", use_cache=False, token=self._token(), **options)
        content = result.content
        for offset in range(0, len(content), chunk_size):
            yield content[offset:offset + chunk_size]

    @staticmethod
    def _to_pathname(storage_uri: str) -> str:
        parsed = parse_locator(storage_uri)
        pathname = parsed.path.lstrip("/")
        if (parsed.scheme != SCHEME or not pathname or parsed.netloc or parsed.query or parsed.fragment
            or pathname.startswith(("http://", "https://")) or ".." in pathname.split("/")):
            raise ValueError("Invalid Blob artifact locator")
        return pathname

    def delete(self, storage_uri: str) -> None:
        pathname = self._to_pathname(storage_uri)
        try:
            delete(pathname, token=self._token())
        except BlobNotFoundError:
            pass
        except Exception:
            raise RuntimeError("Blob artifact deletion failed") from None
