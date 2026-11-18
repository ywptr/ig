from typing import Iterator, Protocol


class ArtifactStore(Protocol):
    """Each put allocates a fresh immutable object and returns a private locator.

    filename is a display/compatibility hint, never an authoritative storage key.
    Reads must enforce backend containment. Application authorization uses Artifact
    IDs and ownership, independently of these locators.
    """

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

    def delete(self, storage_uri: str) -> None:
        """Idempotently remove one object. Never include locators in errors."""
        ...
