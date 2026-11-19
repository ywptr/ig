from collections.abc import Callable, Iterator, Mapping

from .contracts import ObjectStore, PutResultMixin
from .result import ObjectStat
from .locator import parse_locator

StoreResolver = Callable[[str], ObjectStore]


class StorageRouter(PutResultMixin):
    """Route existing objects by locator and send writes to an explicit store."""

    def __init__(
        self,
        providers: Mapping[str, ObjectStore | StoreResolver],
        *,
        write_store: ObjectStore,
    ):
        self.providers = dict(providers)
        self.write_store = write_store

    def register(self, scheme: str, store: ObjectStore | StoreResolver) -> None:
        if scheme in self.providers:
            raise ValueError("Storage provider already registered")
        self.providers[scheme] = store

    def store_for_locator(self, storage_uri: str) -> ObjectStore:
        scheme = parse_locator(storage_uri).scheme
        try:
            target = self.providers[scheme]
        except KeyError:
            raise ValueError("Unsupported artifact storage provider") from None
        return target(storage_uri) if callable(target) else target

    def put_bytes(self, *, filename: str, data: bytes) -> str:
        return self.write_store.put_bytes(filename=filename, data=data)

    def exists(self, storage_uri: str) -> bool:
        return self.store_for_locator(storage_uri).exists(storage_uri)

    def stat(self, storage_uri: str) -> ObjectStat | None:
        return self.store_for_locator(storage_uri).stat(storage_uri)

    def iter_bytes(self, storage_uri: str, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        return self.store_for_locator(storage_uri).iter_bytes(storage_uri, chunk_size=chunk_size)

    def delete(self, storage_uri: str) -> None:
        self.store_for_locator(storage_uri).delete(storage_uri)
