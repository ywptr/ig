from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from .result import ObjectStat, StoredObject


@dataclass(frozen=True)
class StorageCapabilities:
    streaming_read: bool
    resumable_write: bool
    provider_checksum: bool


class ByteStore(Protocol):
    def put_bytes(self, *, filename: str, data: bytes) -> str: ...
    def exists(self, storage_uri: str) -> bool: ...
    def iter_bytes(self, storage_uri: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]: ...
    def delete(self, storage_uri: str) -> None: ...


class ObjectStore(ByteStore, Protocol):
    @property
    def capabilities(self) -> StorageCapabilities: ...

    def put(self, *, filename: str, data: bytes) -> StoredObject: ...

    def stat(self, storage_uri: str) -> ObjectStat | None: ...


class PutResultMixin:
    def put_bytes(self, *, filename: str, data: bytes) -> str:
        raise NotImplementedError

    def put(self, *, filename: str, data: bytes) -> StoredObject:
        return StoredObject(self.put_bytes(filename=filename, data=data), len(data))


def validate_chunk_size(chunk_size: int) -> None:
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("Storage chunk size must be a positive integer")
