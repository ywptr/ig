# ig-storage

Internal byte and object storage for local filesystems, private Vercel Blob stores, and Google Drive. The application selects a configured store and owns object identity, authorization, ownership, credential persistence, and lifecycle policy. The package carries out storage operations.

Install with `pip install ./packages/ig-storage` or install its wheel from an internal distribution channel. The package supports Python 3.11 and later and reads no application environment variables. The local adapter uses POSIX descriptor-relative filesystem operations.

## Usage

```python
from pathlib import Path
from ig_storage import LocalConfig, LocalStore

store = LocalStore(LocalConfig(root=Path("/srv/myapp/objects")))
stored = store.put(filename="report.bin", data=b"report")
assert store.stat(stored.locator).size_bytes == stored.size_bytes
assert b"".join(store.iter_bytes(stored.locator)) == b"report"
store.delete(stored.locator)
```

```python
from ig_storage import VercelBlobConfig, VercelBlobStore

blob = VercelBlobStore(VercelBlobConfig(token=blob_token, prefix="reports"))
stored = blob.put(filename="report.bin", data=b"report")
```

Blob uses randomized private keys, uncached private reads and the pinned SDK's default request retry and timeout behavior. An application may set `read_timeout` explicitly. The pinned SDK buffers the whole downloaded object before the adapter yields slices.

```python
from ig_storage import DriveClient, GoogleDriveConfig, GoogleDriveStore

drive = GoogleDriveStore(
    GoogleDriveConfig(namespace="reports", folder_id=folder_id),
    client=DriveClient(access_token, timeout=60),
)
stored = drive.put(filename="report.bin", data=b"report")
```

Supply a fresh runtime client when refreshing access. An application can also supply a client implementing `DriveRuntime` from `ig_storage.backends.google_drive`. The namespace is an opaque locator scope that the application binds to the correct credentials. Drive streams HTTP reads and uses resumable 8 MiB upload chunks over the supplied bytes. It probes after an uncertain acknowledgement and reuses one preallocated ID across retries. The transport accepts caller-supplied Drive `app_properties` on its upload method. [Google Drive resumable uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads)

## Routing, stat, and copy

`StorageRouter({"file": local, "vercel-blob": blob, "gdrive": resolve_drive}, write_store=selected)` routes reads, stat, and deletes by locator scheme. Each entry can be a store or a callback taking the locator and returning a configured store. Writes use the application-selected store. Local absolute `file:` URIs, `vercel-blob:///pathname`, and `gdrive://namespace/file-id` are supported. Keep locators private and authorize access before routing.

`put` returns a `StoredObject` with a locator and byte count. `put_bytes` returns the locator alone. `stat` returns `ObjectStat` or `None` for missing content. Optional metadata includes MIME type, tagged checksum, and ETag where available. `StorageCapabilities` records streaming reads, resumable writes, and provider checksum support. Delete is idempotent for missing objects. Authentication and transport failures surface to the caller.

A future copy can read chunks under an application-defined size limit, write to an explicitly selected destination, and verify size and a matching checksum or readback before adopting the new locator. Current writers accept bytes, so the caller controls how much data to buffer. The caller owns metadata transactions, failure cleanup, and any source deletion.

IG uses these adapters for artifact and preview bytes. It retains tenant and owner selection, Drive connection records, OAuth refresh and encryption, artifact and preview rows, and retention. Sandcastle V2 can install this package and supply its own equivalent policy and credentials.

## Optional operator smoke tests

- **Local** — Run the first example against a temporary directory. Verify the byte count, content and missing stat after delete.
- **Vercel Blob** — On Preview or staging, use a dedicated private test store and supply its token explicitly. Write a test object, verify the locator, stat and content, delete it, then confirm it is missing. [Vercel private storage](https://vercel.com/docs/vercel-blob/private-storage)
- **Google Drive** — Connect a dedicated development account and select a scratch folder. Pass that folder and a fresh access token to the Drive example. Write a test object, read and stat it, delete it, then confirm it is missing.
