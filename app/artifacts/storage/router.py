from ig_storage import StorageRouter


class RoutingArtifactStore(StorageRouter):
    def __init__(self, *, write_store, local_store, vercel_blob_store):
        super().__init__({"file": local_store, "vercel-blob": vercel_blob_store}, write_store=write_store)
        self.local_store = local_store
        self.vercel_blob_store = vercel_blob_store

    _store_for_uri = StorageRouter.store_for_locator
