# Deterministic Artifact previews (IG#0009)

**Preview generation is deterministic media processing. No LLM or generative model
is used.** A preview represents the stored source bytes, rather than creating new
creative content.

```text
Original Artifact (identity, tenant, owner, provenance, lineage)
    └── mechanical derivative (kind + policy version)
```

The original bytes, Artifact ID, ownership, and content endpoint are unchanged.
Previews are not Artifacts and do not appear as creative items in artifact lists.
`parent_artifact_id` is not used for this relationship.

## Image policy, version 1

Pillow processes PNG, JPEG and WebP images (including historical JPEG MIME aliases):

- Apply EXIF orientation; use the first frame of animated images.
- Preserve aspect ratio, fit within 512×512 pixels, and never enlarge a small image.
- Encode a static WebP at quality 75/method 4, preserving alpha where present.
- Strip EXIF, ICC and other source metadata from the derivative.

The original file is never rewritten. Policy implementation is in
`app/artifacts/preview_policy.py` and `preview_worker.py`. See the upstream
[Pillow thumbnail documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html).

## Video policy, version 1

PyAV uses FFmpeg libraries bundled in its binary wheel. A separate `ffmpeg` CLI or
runtime binary download is not required. Current MP4 Artifacts are supported; this
does not expand video uploads or generation capability. See [PyAV](https://pyav.org/docs/stable/).

The actual decoded container/stream duration selects the policy:

| Duration | Frames | Sampling positions |
| --- | --- | --- |
| Less than 5 seconds | 1 | 50% |
| 5 through 15 seconds | 3 | 10%, 50%, 90% |
| More than 15 through 30 seconds | 5 | 10%, 30%, 50%, 70%, 90% |
| More than 30 seconds | 6 maximum | Evenly spaced from 10% through 90% |

Timestamps are rounded to six decimal places. Decode from the preceding keyframe
to the requested time (or the last available frame near the end). Frame zero is
avoided when the source timeline permits. There is no AI or scene detection.

Frames fit within 256×144 cells without enlargement or cropping. One WebP contact
sheet is assembled in chronological row-major order, with up to three columns and
two rows, using a dark letterbox background. Maximum sheet size is 768×288. A
single-frame preview uses one cell. No separate poster or individual frame objects
are stored: one object/request minimizes storage, API and rendering complexity.

## Persistence, identity, and generation timing

`artifact_previews` has primary key `(artifact_id, kind, version)`. Kinds are
`thumbnail` and `contact_sheet`; the current policy version is 1. It stores state,
claim/lease data, private storage locator, dimensions, byte count, frame count,
sampled timestamps, and an output-content hash for ETag validation. Ownership is
always read from the original Artifact. The additive migration is
`1a0009000009_artifact_previews.py`; it performs no backfill or source-row rewrite.

Metadata/list reads never decode or fetch original media. The first authorized
preview-content GET lazily builds and persists a derivative. This works for old
Artifacts and avoids delaying generation Job completion. The cold HTTP request
waits for bounded processing in a FastAPI worker thread; this is not a background
queue. No Job, worker, continuation, queue protocol or provider behavior is changed.

A unique row and conditional claim updates prevent concurrent normal processing
of the same derivative. The claim is committed before source/storage I/O. A
120-second lease permits recovery after a process crash; at most two attempts are
allowed for expired claims. Claim fencing prevents a stale processor from
publishing over a replacement. Failed derivations are cached rather than retried
on every list render. Ready derivatives reuse the same object across requests and
API/process restarts. Only two cold processors run concurrently per API process;
additional requests receive a processing response and can retry later.

Changing dimensions, sampling, encoding, or another material policy requires a
version increment. Current metadata advertises the current version. Already-ready
older versions remain readable through their explicit `v` URL; an older version
never starts new processing. Missing/nonready unsupported versions return 404.

## API contract

Existing Artifact upload, list and metadata representations gain a lightweight
`preview` field, for example:

```json
{
  "artifact_id": "original-artifact-UUID",
  "preview": {
    "status": "ready",
    "kind": "contact_sheet",
    "version": 1,
    "url": "/v2/artifacts/original-artifact-UUID/preview?v=1",
    "mime_type": "image/webp",
    "width": 768,
    "height": 288,
    "size_bytes": 15000,
    "frame_count": 5,
    "positions": [2, 6, 10, 14, 18],
    "error_code": null
  }
}
```

Dimensions/size/frame count are null before derivation. Status is `missing`,
`processing`, `ready`, `failed`, or `unsupported`. Unsupported media has no preview
URL. Artifact-list metadata is loaded with one batched derivative query, not one
query or media read per item. The v1 image compatibility list/detail APIs additionally return
its existing `artifact_id`, when available, so legacy history can use the same API. The v1 submission response stays unchanged.

```http
GET /v2/artifacts/{artifact_id}/preview?v=1
```

| Response | Meaning |
| --- | --- |
| 200 `image/webp` | Cached or freshly created static derivative |
| 304 | Authorized conditional request matches the derivative ETag |
| 202 JSON + `Retry-After: 3` | Another processor owns the claim, or this process is at capacity |
| 422 JSON with `status: failed` | Cached mechanical-processing failure; source is never substituted |
| 415 JSON with `status: unsupported` | Unsupported media/MIME type |
| 404 | Unknown/wrong-owner/wrong-tenant Artifact, unsupported version, or unavailable saved preview content |

Processing failures expose only allowlisted codes: `PROCESSING_FAILED`,
`PROCESSING_TIMEOUT`, or `SOURCE_SIZE_LIMIT`. Decoder diagnostics and paths are not
returned. Failed/pending responses are `private, no-store`.

Successful responses have a content-hash `ETag`, `Cache-Control: private, no-cache`,
and `Vary: Cookie, Authorization`. Browsers can retain the small derivative but
must revalidate; IG checks authorization before returning 304. Previews are never
public/CDN-shared content. Original `GET /v2/artifacts/{id}/content` behavior is
unchanged, including its existing headers.

Authorization is identical to the original Artifact: active tenant membership,
matching tenant and user ownership. Another user's private preview remains
inaccessible even to a same-tenant actor. Delegated services use the existing
`artifacts:read` scope and signed actor/tenant contract. Neither metadata nor
content responses expose physical source or preview storage URIs.

## Storage and resource bounds

Sources are read and previews written exclusively through `ArtifactStore`.
Temporary source/output files have fixed internal names beneath an owned private
temporary directory; display filenames and stored locators are not decoder paths.
Local filesystem and Vercel Blob work through the existing router. Future storage
adapters can participate through the same interface without changing Artifact IDs.

Both codecs run in a separate process with a stripped environment (no inherited
service/provider/database credentials), a 25-second wall timeout, 15-second CPU
limit, 768 MiB virtual-memory limit, 4 MiB output-file limit, no core dumps and a
64-file-descriptor limit. Timeout kills and reaps the subprocess group. Arguments
are passed as an array; no shell command is constructed from source names.

Additional limits:

- Original image bytes: 32 MiB; video bytes: 100 MiB. Declared size is checked before
  reading, and actual stream bytes are checked independently.
- Image/video dimensions: at most 8192 per axis and 20 million pixels. Pillow
  decompression-bomb warnings are fatal.
- Video processing: positive finite duration at most 600 seconds; at most 360
  decoded frames per seek; one decoder thread. Invalid streams fail safely.
- MP4 demuxing only, local file/pipe protocols only, external MOV data references
  disabled. Audio/subtitle tracks are not decoded.

Storage adapters retain their existing I/O timeout/buffering behavior. In
particular, Vercel Blob currently buffers a fetched object in its adapter before
exposing chunks; the cold server-side source read is not eliminated. Subsequent
preview requests do not reread/decode the source. This change targets repeated
browser original downloads, not a storage-adapter streaming rewrite.

A failed processing attempt creates no creative Artifact. Derivative bytes are
stored before their ready state commits. A DB failure or stale claim after storage
can leave an orphan object, as with existing artifact staging; bounded claims
limit duplicate attempts. No deletion/retention/garbage-collection subsystem is
added. Future Artifact deletion must account for derivative rows and their objects.
A cached failure is not reset by a normal retry; correcting infrastructure requires
an explicit operational retry policy or a new preview version.

## Frontend and reuse

Generation results render `<img>` pointing only to the preview URL for both images
and videos. There is no list `<video>`, MP4 preload, autoplay, original `img.src`,
or original fallback on preview failure. Legacy history follows the same behavior;
records without an Artifact ID show a placeholder and an explicit original link.

Originals remain behind explicit Open links. Pending/error responses show a
placeholder and a manual Retry preview button; there is no idle preview polling.
The metadata/URL contract can later serve artifact libraries, attachment pickers,
search/autocomplete and Sandcastle service clients without exposing storage details.
Those UIs and external-storage integrations are outside this milestone.

A real headless-browser fixture check observed two preview requests and zero
original-content requests for image/video result rendering; an explicit click
then requested the original. A representative deterministic 1600×1000 noisy PNG
was 4,807,776 bytes, versus 50,762 bytes for its 512×320 WebP preview (98.94% less).
Actual reduction depends on source size/content; tiny originals may not shrink.

Run backend tests with `python -m unittest tests.test_artifact_previews`. Frontend
renderer tests require no browser. The optional `preview-network.test.mjs` uses a
cached Chromium headless shell and Node WebSocket support (on Node 20, pass
`--experimental-websocket`); it needs the browser's normal runtime libraries and
uses only a disposable profile and loopback fixture server.

## Implementation file map

| Area | Files added/updated for IG#0009 |
| --- | --- |
| Derivation | `app/artifacts/preview_policy.py`, `app/artifacts/preview_worker.py`, `app/artifacts/previews.py` |
| Persistence | `app/db/models/artifact_preview.py`, `app/db/models/__init__.py`, `alembic/versions/1a0009000009_artifact_previews.py` |
| API/authorization | `app/api/artifacts.py`, `app/api/images.py`, `app/auth/service_access.py` |
| Frontend | `frontend/src/api/artifacts.ts`, `frontend/src/types/media.ts`, `frontend/src/components/ArtifactPreviewImage.tsx`, `frontend/src/components/ImageCard.tsx`, `frontend/src/components/MediaResultCard.tsx` |
| Tests | `tests/test_artifact_previews.py`, `frontend/tests/preview.test.mjs`, `frontend/tests/preview-network.test.mjs` |
| Dependencies | `pyproject.toml`, `requirements.txt`, `uv.lock` |
| Documentation | `docs/artifact-previews.md`, `docs/service-access.md`, `README.md` |
