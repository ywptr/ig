from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse


class LocalArtifactStore:

    def __init__(
        self,
        base_dir: Path,
    ):
        self.base_dir = base_dir

    def put_bytes(
        self,
        *,
        filename: str,
        data: bytes,
    ) -> str:
        self.base_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        filepath = (
            self.base_dir / filename
        ).resolve()

        filepath.write_bytes(data)

        return filepath.as_uri()

    def exists(
        self,
        storage_uri: str,
    ) -> bool:
        return self._to_path(
            storage_uri
        ).exists()

    def iter_bytes(
        self,
        storage_uri: str,
        *,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        filepath = self._to_path(
            storage_uri
        )

        with filepath.open("rb") as file:
            while chunk := file.read(
                chunk_size
            ):
                yield chunk

    def _to_path(
        self,
        storage_uri: str,
    ) -> Path:
        parsed = urlparse(
            storage_uri
        )

        if parsed.scheme != "file":
            raise ValueError(
                "Unsupported storage URI "
                f"for LocalArtifactStore: "
                f"{storage_uri}"
            )

        return Path(parsed.path)