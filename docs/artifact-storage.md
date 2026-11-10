# Artifact storage identity and isolation

The invariant is:

```text
Artifact identity != filename != storage key
```

`Artifact.artifact_id` is the stable public application identity. Tenant/user
ownership controls metadata, content, and generation-reference access. A storage
key is not an authorization token, and must never encode or replace that policy.
`Artifact.filename` is display/download metadata. Repeated names are allowed for
the same user and across users/tenants without changing any earlier object's bytes.

## Writes

Every `ArtifactStore.put_bytes` call allocates a fresh opaque object key, including
uploads, generated images, and staged video outputs. The `filename` keyword remains
for call compatibility, but both built-in adapters ignore it when allocating keys.
No caller should derive a physical locator from a filename.

Local storage uses UUID4 keys and exclusive file creation (`O_EXCL`, mode 0600).
An existing object, including a symlink at a colliding key, is never overwritten;
a collision allocates another key. A failed write removes its partially written
object before returning an error. Vercel Blob uses `artifacts/<UUID4 hex>` and keeps
private access plus the SDK's random suffix. Neither backend includes the display
name or tenant/user identity in its new object keys.

The upload service writes bytes before creating/publishing an Artifact row. A
storage failure does not publish a row. If storage succeeds but row creation or
commit fails, the transaction rolls back but the object may remain orphaned.
Generated-output staging has the same orphan possibility after cancellation or
failed finalization. This change does not add deletion, reconciliation, or a cleanup
service. It also does not change the upload media types/signature checks or 10 MiB
limit, provider execution, queue semantics, or public API schemas.

## Local containment

The configured local root is canonicalized when the adapter is constructed. A
configured development symlink is allowed and pins its resolved target. New objects
are created relative to an opened root directory. Reads accept only absolute local
`file:` URIs inside that root, including URI-escaped historical filenames. Remote
authorities, queries/fragments, malformed escapes, control characters, traversal,
and outside-root paths are rejected.

Read operations open each directory component relative to an existing directory
descriptor with `O_NOFOLLOW`, then open only a regular file. Symlinks beneath the
root are rejected, including directory links and file links. This avoids relying
on a path check followed by a separate symlink-following open. Writes use the same
root anchoring and no-follow/exclusive creation. This implementation targets the
repository's Linux/POSIX development and deployment environments.

## Existing objects and privacy

No database migration or row rewrite is required. Existing local URIs pointing to
regular files beneath the configured root remain readable, even when their old
storage names equal their display filenames. Existing Vercel Blob locators remain
readable. The routing store continues choosing reads by each saved URI scheme,
independently of the current write backend.

Historical outside-root locators or symlink-dependent paths are deliberately
rejected. If legitimate historical objects use a different root, configure the
correct trusted root and review those objects explicitly; do not bypass containment.
Previously collided local objects may already contain a later upload's bytes.
The original bytes cannot be inferred from the database, so automatic repair is
unsafe. Restore from a verified backup or re-upload as a new Artifact after review.

`storage_uri` stays private/internal. Artifact list, upload, and metadata responses
expose Artifact IDs and filenames, not locators; generation and reference APIs use
Artifact IDs. Local validation errors do not echo physical locators. Moving bytes
or changing the write backend must not change an Artifact ID.

Run dedicated isolation tests with:

```sh
.venv/bin/python -B -m unittest tests.test_artifact_storage -v
```
