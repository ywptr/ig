# Artifact library foundations (IG#0003)

## Identity, ownership and compatibility

`Artifact.artifact_id` is the stable application identity. The display filename
is metadata; immutable object keys and private storage locators are backend
implementation details. Matching SHA-256 hashes never imply shared access or
shared identity. Local and Vercel Blob objects use the same existing storage
interface. No storage integration or cross-user sharing is introduced here.

Every query and reference resolution requires both tenant and owning user.
Browser callers retain session + hostname/TenantDomain + membership resolution.
Delegated service callers retain signed service/actor/tenant validation, active
membership and `artifacts:read` scope. A service actor cannot browse another
member's private artifacts. See [service access](service-access.md).

Existing IDs, content endpoints, JSON metadata and previews remain valid. The
legacy `GET /v2/artifacts` array remains available with its newest-100 limit;
new clients should use the paginated endpoint below. `parent_artifact_id` remains
unchanged for compatibility: it is not the representation of generation inputs.

## Provenance and producer relationships

`provenance` is a typed, persisted enum:

- `generated`: an IG generation output;
- `uploaded`: bytes ingested through the supported upload path;
- `unknown`: historical or otherwise unclassified evidence.

An upload marked `origin=external_generation` is still an upload into IG.
The existing `origin` and `ingest_method` metadata remains intact. No imported
category is claimed without an actual import path.

`artifact_generations` associates one output Artifact with its Job and Execution.
Each Job/Execution can produce many Artifacts; no JSON traversal is needed to
query outputs. The existing Execution metadata `artifact_id` pointer remains
for compatible clients. New image outputs record the producer using trusted
worker execution context, never caller-provided producer IDs. Video finalization
records it in the existing fenced completion transaction. Artifact and lineage
inserts are atomic within their output transaction; the image handler retains
its existing commit timing relative to generic worker completion.

`artifact_inputs` records ordered edges from output to input:
`output_artifact_id`, `position`, `input_artifact_id`, `relation=reference`, `role`,
and optional `strength`. Repeated inputs with different roles are representable.
Roles match implemented semantic image roles and video `first_frame`; `reference`
is reserved as the generic typed role. Neither prompt text nor `parent_artifact_id`
is used to infer these edges. Producers and inputs are checked against the
output's tenant and owner. Full structured request data also remains in the Job.
The detail endpoint includes ordered `inputs`; listings omit the edges to keep
page queries bounded.

Migration `1a0003000003` is additive. It marks rows uploaded only when
`metadata.ingest_method=upload`. A unique owner-consistent Execution output
pointer establishes generated provenance and producer linkage; upload evidence
wins. Multiple possible producers remain unknown. Existing structured references
are backfilled only when their IDs, roles, strengths and ownership are valid.
No object bytes are read during migration. Dimensions and checksums are not
invented for historical rows. Downgrade drops only these new fields/tables.
Apply through the documented [database operations procedure](database-operations.md);
this development task does not migrate production.

## Listing, search and pagination

`GET /v2/artifacts/page` returns `{items, next_cursor, has_more}`.

| Query | Contract |
| --- | --- |
| `page_size` | 1–100, default 30 |
| `cursor` | Opaque next position from the preceding response |
| `order` | `desc` (default) or `asc`, using `(created_at, artifact_id)` |
| `media_type` | `image`, `video`, `audio`, or `text` |
| `provenance` | `unknown`, `generated`, or `uploaded` |
| `q` | Case-insensitive filename substring, up to 256 characters; SQL wildcard characters are literal |
| `created_after` | Inclusive timestamp lower bound |
| `created_before` | Exclusive timestamp upper bound |

Offset-aware timestamps are normalized to UTC; naive timestamps mean UTC.
Reuse filters/order when continuing a cursor. Cursor scope includes tenant, actor
and filters; changes produce HTTP 400. Invalid cursors return 400; invalid query
types/ranges return 422. Cursors confer no authorization and are not secrets.
Every page independently applies ownership checks. Ordering includes Artifact ID
to handle tied timestamps. Newly inserted earlier items do not shift already
visited positions. This is a live keyset view, not a frozen search snapshot.

The composite tenant/user/created-time/ID index supports private ordered scans.
Filename search uses escaped substring matching, not a full-text index. Additional
media/provenance indexes can be justified by real query volume later; no provider
or model JSON filtering is exposed yet.

Example response (illustrative IDs; no storage-specific data):

```json
{
  "items": [{
    "artifact_id": "34ed4cb4-f863-424e-8809-f1aa53398775",
    "user_id": "2577b485-b6e2-41fb-a536-f035d36c739e",
    "filename": "reference.png",
    "media_type": "image",
    "mime_type": "image/png",
    "size_bytes": 19420,
    "created_at": "2026-11-10T12:00:00",
    "provenance": "uploaded",
    "media_properties": {"width": 1200, "height": 800, "duration_seconds": null, "fps": null},
    "sha256": "5a807726e80b3abb246ccdaf3f116daefa296e1c1564e562062c0fb4de7a179b5",
    "generation": null,
    "metadata": {"origin": "reference", "ingest_method": "upload"},
    "parent_artifact_id": null,
    "preview": {
      "status": "missing", "kind": "thumbnail", "version": 1,
      "url": "/v2/artifacts/34ed4cb4-f863-424e-8809-f1aa53398775/preview?v=1",
      "mime_type": "image/webp", "width": null, "height": null, "size_bytes": null,
      "frame_count": null, "positions": [], "error_code": null
    }
  }],
  "next_cursor": null,
  "has_more": false
}
```

`generation` is null or `{job_id, execution_id}`. Detail responses also contain
`inputs: [{artifact_id, position, relation, role, strength}]`. Lists return
metadata only, never original media bytes or physical storage locators.
Preview metadata is fetched in one batch per page; producer linkage is also
batched. List requests never probe/decode source media or start preview work.

## Normalized media metadata

New ingests persist `sha256` over original bytes. Existing `size_bytes` and
`mime_type` remain canonical common fields. New nullable `width`, `height`,
`duration_seconds` and `fps` appear under `media_properties` in the API.

Image dimensions come from bounded Pillow header inspection at upload/output
persistence, without rewriting the source. Video dimensions, duration and FPS
come from a restricted PyAV header probe in the existing media-processing child
process, before the completion database transaction. It has a five-second wall
timeout, stripped environment, local-only input, source-size/dimension bounds and
existing child memory/CPU limits. Metadata failures leave nullable properties;
they do not discard stored provider output or broaden accepted upload types.
No codec/container fields are normalized because library/reference workflows do
not yet require them. Historical rows may have all new properties null.

Previews remain derivatives authorized through their source Artifact, using the
IG#0009 versioned cache and preview API. Lists render `preview.url`; explicit
open/download uses `/v2/artifacts/{id}/content`.
Preview generation is deterministic media processing. No LLM or generative model
is used. See [preview lifecycle](artifact-previews.md).

## Typed reference-input contract

Adapter `TechnicalCapabilities.reference_inputs` is a tuple of typed slots:

```json
{
  "name": "first_frame",
  "media_types": ["image"],
  "mime_types": ["image/png", "image/jpeg", "image/webp"],
  "roles": ["first_frame"],
  "min_count": 0,
  "max_count": 1,
  "max_bytes": 10485760,
  "strengths": []
}
```

A role selects exactly one slot; names and roles may not overlap across slots.
`min_count` handles required/optional semantics, `max_count` bounds the slot,
and `max_bytes` bounds each reference. Media kinds include future audio/video
inputs without adding execution support. Roles/strengths are typed vocabulary,
not an arbitrary schema language. Slot validation rejects unknown fields and
invalid bounds. Models without slots accept no artifact references.

Current declarations:

| Model | Contract |
| --- | --- |
| OpenAI `gpt-image-2` | 0–8 image references, PNG/JPEG/WebP (legacy `image/jpg` alias), existing seven semantic roles, loose/normal/strong, 32 MiB per input |
| Google `gemini-3.1-flash-lite-image` | 0–1 image, PNG/JPEG/WebP, same semantic roles/strengths, 10 MiB per input |
| Alibaba `wan3.0-video` | Optional one PNG/JPEG/WebP image, `first_frame` role only, no strength, 10 MiB |

OpenAI's 32 MiB bound replaces an unbounded worker read; normal uploads remain
PNG/JPEG/WebP at most 10 MiB. Existing flat controls (`max_references`, roles,
strengths and `reference_mime_types`) remain compatible discovery projections.
The slot contract governs reusable Artifact checks. Adapter-native defensive
checks may remain for direct adapter calls. Legacy tenant-specific model aliases
retain the implemented adapter input contract without being advertised as known
model capabilities.

Submission checks the declaration and actual owner-scoped Artifact metadata
before creating work. Worker resolution repeats ownership/media/MIME/size checks
and bounds actual streamed bytes before a provider call. Missing historical byte
sizes can pass metadata preflight, but actual worker reads remain bounded. Client
metadata is never authoritative. Server errors remain the existing
`INVALID_MEDIA_REQUEST` HTTP 400 contract; schema-invalid roles may return 422.

## Contract for IG#0004 / IG#0005 and Sandcastle

Future pickers can query `/v2/artifacts/page`, render cached previews and compare
artifact metadata against the selected model's `reference_inputs` before submit.
They should preserve selected Artifact IDs independently of labels and filenames.
The existing structured `references` generation field already carries stable IDs,
semantic roles and optional strengths. No attachment UI is built here.

For future `@` autocomplete, insert a display token backed by an Artifact ID.
Resolve mentions to structured references; do not send an ambiguous display name
as the source of identity or rely on prompt parsing as lineage. Prompt editing
and token reconciliation belong to that later frontend milestone.

Sandcastle may consume the same page/detail/preview/content contracts using its
existing delegated service token with `artifacts:read`. Generation uses
`media:generate` and existing idempotency rules; references belong in the
structured request and therefore in its deterministic fingerprint. This does not
allow service uploads or sharing. Sandcastle requires no IG database access and
never receives storage-provider IDs. A future storage backend can change where
bytes live while preserving Artifact IDs, lineage, provenance and API URLs.

## Remaining lifecycle limits

Storage upload still precedes database publication. A database failure or a
stale/canceled finalizer can leave an unreferenced object, as before; this task
does not add a cleanup service, deletion API, deduplication, sharing or historical
media re-probing. Substring search may need a specialized index at larger scale.
