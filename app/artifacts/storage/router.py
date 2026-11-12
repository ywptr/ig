from typing import Iterator
from urllib.parse import urlparse

from app.artifacts.storage.base import (
    ArtifactStore,
)


class RoutingArtifactStore:

    def __init__(
        self,
        *,
        write_store: ArtifactStore,
        local_store: ArtifactStore,
        vercel_blob_store: ArtifactStore,
    ):
        self.providers = {"file": local_store, "vercel-blob": vercel_blob_store}
        self.write_store = write_store
        self.local_store = local_store
        self.vercel_blob_store = (
            vercel_blob_store
        )

    def put_bytes(
        self,
        *,
        filename: str,
        data: bytes,
    ) -> str:
        return self.write_store.put_bytes(
            filename=filename,
            data=data,
        )

    def exists(
        self,
        storage_uri: str,
    ) -> bool:
        store = self._store_for_uri(
            storage_uri
        )

        return store.exists(
            storage_uri
        )

    def iter_bytes(
        self,
        storage_uri: str,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        store = self._store_for_uri(
            storage_uri
        )

        return store.iter_bytes(
            storage_uri,
            chunk_size=chunk_size,
        )

    def _store_for_uri(
        self,
        storage_uri: str,
    ) -> ArtifactStore:
        scheme = urlparse(
            storage_uri
        ).scheme

        if scheme not in self.providers:
            raise ValueError("Unsupported artifact storage provider")
        return self.providers[scheme]

    def register(self, scheme: str, store: ArtifactStore) -> None:
        if scheme in self.providers:
            raise ValueError("Storage provider already registered")
        self.providers[scheme] = store

    def delete(self, storage_uri: str) -> None:
        self._store_for_uri(storage_uri).delete(storage_uri)
