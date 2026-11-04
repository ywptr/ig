# Alibaba Cloud Model Studio video adapter

Provider: `alibaba`. Initial model: `wan3.0-video` (currently **preview**).
This adapter calls Model Studio directly using the existing requests dependency.
No DashScope SDK or intermediary is used.

The implementation was checked against Alibaba's
[Wan 3.0 API reference](https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference).
Preview behavior and account/region model availability must be verified before
enabling a tenant. No live generation was performed during implementation.

## Tenant configuration

Use the existing generic tool unchanged:

```sh
python scripts/configure-tenant-provider.py --tenant-slug <tenant-slug> --provider alibaba
```

Enter credentials through its secret prompt:

```json
{"api_key": "<tenant-alibaba-api-key>"}
```

Non-secret configuration:

```json
{
  "base_url": "https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com",
  "default_models": {"video": "wan3.0-video"}
}
```

The base URL must be an HTTPS **origin**, without path/query/userinfo. Workspace
is part of the hostname. Use the workspace endpoint from the same region as the
API key and available model. Singapore is the intended initial environment; there
is no global region/key fallback and no global Alibaba API-key environment variable.
Other regional origins may be configured explicitly. Existing image defaults
remain untouched.

Keep a provider's workspace/region stable while its operations are active;
continuations recreate adapters from the tenant's current configuration. Moving
an endpoint or replacing a key with one for another region can make existing
operations inaccessible. IG does not retain historical credential versions.

Hard-mode selection can specify:

```json
{"interface_level": "hard", "advanced": {"provider": "alibaba", "model": "wan3.0-video"}}
```

Only this model is initially registered. Other model variants are rejected even
if the provider API supports them.

## Provider-only translation

The adapter posts to `/api/v1/services/aigc/video-generation/video-synthesis`
with API authorization and the asynchronous submission header. It maps lowercase
IG resolution to provider casing, and maps generic duration/aspect ratio/audio to
the provider parameter object. First-frame bytes become a MIME-tagged base64 data
URI in the provider's media input. Other provider input modes are not exposed.

Submission returns a task ID. All continuations GET `/api/v1/tasks/{task_id}`;
they never POST another task. PENDING/RUNNING translate to generic pending;
SUCCEEDED downloads output; FAILED/CANCELED/UNKNOWN translate to a terminal
operation error. Unsupported/invalid responses and network faults do not create
replacement tasks.

Only allowlisted request identifiers and numeric usage fields enter metadata.
API keys, native status objects, raw responses/messages, and signed result URLs
are not persisted. API exceptions use static sanitized error messages.

## Output ingestion and bounds

The temporary output URL is consumed immediately inside the adapter, not stored
as an Artifact URI. Downloads require HTTPS on an `*.aliyuncs.com` output host,
do not follow redirects, and receive no API Authorization header. Other CDN
hosts will require a deliberate adapter policy change.

API calls use 5-second connect / 20-second read timeouts, without automatic
submission retries. Download reads use 5/10-second connect/read timeouts, 64 KiB
chunks, a 60-second total iteration deadline, and a hard 100 MiB cap. The response
must be HTTP 200 with MP4/octet-stream content type and an MP4 file-type signature.
Read-timeout granularity can extend the deadline by one read interval. These
bounds are below the current 120-second continuation lease for normal status and
download work. Storage upload may still outlive a lease; the final fence prevents
stale Artifact rows.

The bounded bytes are assembled in memory because current storage accepts
put_bytes. Streaming-to-storage, byte-range delivery, and orphan-upload cleanup
are future work. Reference-image dimensions/transparency are ultimately checked
by the provider; IG checks ownership, MIME type, and its 10 MiB reference limit.

## Manual billable smoke test — do not run automatically

After an operator confirms region/model availability and explicitly authorizes a
billable request:

1. Configure the tenant as above and confirm its queue subscriber is running.
   RQ deployments need `--with-scheduler`; Vercel uses the existing subscriber.
2. Authenticate normally through IS and use an authorized application session.
3. Submit **once** through IG (not directly to Alibaba):

   ```sh
   curl --fail-with-body -b <session-cookie-file> \
     -H 'Content-Type: application/json' \
     https://<tenant-host>/v2/media/generations \
     -d '{"media_type":"video","interface_level":"easy","intent":{"description":"A calm ocean sunrise, fixed camera"},"output":{"duration_seconds":2,"resolution":"480p","aspect_ratio":"9:16","audio":false}}'
   ```

4. Follow the returned Job ID and its executions until completed/failed. Do not
   resubmit because generation is slow. Confirm one provider operation ID is
   retained across waiting/polling updates.
5. On success, retrieve the artifact ID from execution metadata, then use
   `/v2/artifacts/{artifact_id}` and `/content`. Confirm video/mp4, tenant ownership,
   an MP4 filename, and permanent IG storage rather than the temporary provider URL.
6. Inspect sanitized error/status data on failure. Region/key/model mismatch is a
   configuration issue; do not blindly retry a billable generation.

Enabling/disabling audio does not change provider pricing according to the API
reference. Short duration and low resolution bound the smoke-test scope; no price
or account quota is assumed here.
