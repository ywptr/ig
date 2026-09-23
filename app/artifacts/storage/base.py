from typing import Iterator, Protocol


class ArtifactStore(Protocol):

    def put_bytes(
        self,
        *,
        filename: str,
        data: bytes,
    ) -> str:
        ...

    def exists(
        self,
        storage_uri: str,
    ) -> bool:
        ...

    def iter_bytes(
        self,
        storage_uri: str,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        ...