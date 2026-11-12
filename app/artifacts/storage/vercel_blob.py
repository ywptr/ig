from typing import Iterator
from urllib.parse import urlparse
from uuid import uuid4

from vercel.blob import (
    BlobNotFoundError,
    delete,
    get,
    head,
    put,
)


SCHEME = "vercel-blob"


class VercelBlobArtifactStore:
    retention_default_enabled = True
    retention_default_days = 28


    def put_bytes(
        self,
        *,
        filename: str,
        data: bytes,
    ) -> str:
        result = put(
            f"artifacts/{uuid4().hex}",
            data,
            access="private",
            add_random_suffix=True,
        )

        return (
            f"{SCHEME}:///"
            f"{result.pathname}"
        )

    def exists(
        self,
        storage_uri: str,
    ) -> bool:
        pathname = self._to_pathname(
            storage_uri
        )

        try:
            head(pathname)
            return True
        except BlobNotFoundError:
            return False

    def iter_bytes(
        self,
        storage_uri: str,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        pathname = self._to_pathname(
            storage_uri
        )

        result = get(
            pathname,
            access="private",
            use_cache=False,
        )

        content = result.content

        for offset in range(
            0,
            len(content),
            chunk_size,
        ):
            yield content[
                offset:offset + chunk_size
            ]

    def _to_pathname(
        self,
        storage_uri: str,
    ) -> str:
        parsed = urlparse(
            storage_uri
        )

        if parsed.scheme != SCHEME:
            raise ValueError("Invalid Blob artifact locator")

        pathname = parsed.path.lstrip("/")
        if not pathname or parsed.netloc or parsed.query or parsed.fragment or ".." in pathname.split("/"):
            raise ValueError("Invalid Blob artifact locator")

        return pathname

    def delete(self, storage_uri: str) -> None:
        pathname = self._to_pathname(storage_uri)
        try:
            delete(pathname)
        except BlobNotFoundError:
            pass
        except Exception:
            raise RuntimeError("Blob artifact deletion failed") from None
