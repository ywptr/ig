from dataclasses import dataclass, field


@dataclass(frozen=True)
class Checksum:
    algorithm: str
    value: str


@dataclass(frozen=True)
class ObjectStat:
    size_bytes: int | None = None
    content_type: str | None = None
    checksum: Checksum | None = None
    etag: str | None = None


@dataclass(frozen=True)
class StoredObject:
    locator: str = field(repr=False)
    size_bytes: int
