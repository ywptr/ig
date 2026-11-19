import os

from ig_storage import VercelBlobConfig, VercelBlobStore


class VercelBlobArtifactStore(VercelBlobStore):
    retention_default_enabled = True
    retention_default_days = 30

    def __init__(self):
        token = os.getenv("BLOB_READ_WRITE_TOKEN") or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN")
        super().__init__(VercelBlobConfig(token=token))
