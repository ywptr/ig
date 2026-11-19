from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LocalConfig:
    root: Path


@dataclass(frozen=True)
class VercelBlobConfig:
    token: str | None = field(repr=False)
    prefix: str = "artifacts"
    read_timeout: float | None = None


@dataclass(frozen=True)
class GoogleDriveConfig:
    namespace: str
    folder_id: str | None
