# Voice generation

Speech is a synchronous media operation. To submit it through `POST /v2/media/generations`, send:

```json
{
  "media_type": "audio",
  "operation": "text_to_speech",
  "intent": {"description": "Hello from IG."},
  "output": {"voice": "coral", "output_format": "wav"}
}
```

Omit the voice or format to use the selected model’s defaults. In Hard mode, `advanced.provider` and `advanced.model` can pin a model from `GET /v2/media/capabilities`. `output.speaking_rate` and `output.instructions` are available for models that declare them. References and provider parameters are outside this operation. The Python integration client accepts the same request body and uses its existing delegated media and Artifact APIs.

## OpenAI contract verified November 20, 2026

| Aspect | OpenAI and IG behavior |
| --- | --- |
| Endpoint | `POST /v1/audio/speech` |
| Models | OpenAI documents `tts-1`, `tts-1-hd`, and `gpt-4o-mini-tts` and its snapshots. IG registers only the `gpt-4o-mini-tts` alias. OpenAI lists January 6, 2027 as the shutdown date. The IG catalog exposes and enforces that sunset. The recommended `gpt-realtime-2.1-mini` replacement uses the Realtime API and needs its own adapter transport. |
| Built-in voices | Alloy, Ash, Ballad, Coral, Echo, Fable, Onyx, Nova, Sage, Shimmer, Verse, Marin, and Cedar. IG defaults to Coral. The catalog exposes these voices for enabled speech models. |
| Limits and controls | The endpoint caps text at 4,096 characters. OpenAI documents a 2,000-input-token cap for this model, enforced upstream. IG validates character limits and model controls before generation, including speaking rate from 0.25× to 4× and optional instructions up to 4,096 characters. |
| Formats | OpenAI offers MP3, Opus, AAC, FLAC, WAV, and raw PCM. IG supports MP3 and WAV for this adapter and defaults to MP3. |
| Response | The reference documents a binary response as `application/octet-stream` and supports streamed audio and server-sent events for this model. The adapter consumes binary chunks with time and size bounds, checks the container signature, and assigns the canonical Artifact MIME type (`audio/mpeg` or `audio/wav`). IG publishes the complete file. |

Sources checked on November 20, 2026. [Speech API reference](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create) · [Speech guide](https://developers.openai.com/api/docs/guides/text-to-speech) · [Model page](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts) · [Deprecation schedule](https://developers.openai.com/api/docs/deprecations)

## Provider and identity boundary

`ig_core.media.speech.SpeechGenerationRequest` carries text, a model-scoped voice reference, format, and optional delivery controls. `VoiceSelection` is the catalog ID and display label. A provider resolves its own IDs and maps controls to its API. The Job records provider and model separately and pins the resolved request. A future voice-profile resolver can map a creative voice to a model-scoped selection.

A future ElevenLabs adapter implements `SpeechProvider.from_config` and `synthesize`, advertises model and voice capabilities, returns a bounded audio `ArtifactPayload`, and normalizes upstream failures. Register it in the speech provider registry with its own configuration. Job execution, storage, workflow plans, and the Labs consume the catalog and common protocol. Sandcastle owns character and creative voice identity. IG and `ig-core` own provider execution and audio Artifact generation.

IG uses the tenant’s existing encrypted OpenAI credentials. Tenant Model Governance can enable, disable, and set defaults for audio independently of image. Model eligibility is checked before selecting a Job and rechecked at execution. Voice-level policy can later narrow discovery and admission together. This release exposes the complete built-in catalog for an enabled model.

## Jobs, Artifacts, and workflows

Speech follows the normal queued, running, and completed Job and Execution path with RQ or Vercel Queue. Cancellation uses the existing cooperative Job fence. Job claims prevent duplicate work on queue redelivery. Result publication commits the Artifact lineage with Job and Execution completion. Unsupported model, voice, format, rate, and text length fail validation. Provider access, rate limit, invalid audio, generation failure, and uncertain transport failures use sanitized messages.

Generated audio follows the owner’s normal IG-managed or Google Drive storage preference and the corresponding generated-media retention policy. IG stores MIME, size, hash, voice/format, and container metadata. WAV ingest also records measurable duration and sample rate. Playback uses authenticated `/v2/artifacts/{id}/content` with no image preview or persisted locator in the API. The Labs label AI-generated audio and defer downloads until playback. The current content endpoint streams sequentially.

Generation Lab exposes Audio when discovery reports it as available. Workflow Lab supports `single_generation` with `text_to_speech`, and shows the planned voice and video step availability in `voice_then_video`. The two-step preset remains unavailable until a registered, configured model can take the audio and the required image. The existing typed `voice_track` binding can pass the audio Artifact ID into a future video step.

## Optional operator smoke test

After an authorized Vercel Preview deployment, use a test tenant with existing OpenAI credentials and a speech model enabled under Tenant Settings. Ensure Preview is using the current `ig-core` package, the existing queue subscriber, normal Artifact storage configuration, and a database already at the IG#0014 workflow schema head.

1. Open Generation Lab, select Audio, choose Coral and WAV, and enter “Hello from IG.”
2. Submit once. Check Jobs and Executions for completion and the expected provider and model.
3. Confirm the generated Artifact has MIME `audio/wav`, plays inline, and has a producing Job and Execution.
4. Open Workflow Lab and confirm Speech is available as a single step and the combined Voice → Video preset reports the missing audio-input video capability. Run the same short sentence there only if workflow execution also needs a live check.

The smoke test incurs provider costs. IG#0015 has no schema migration.
