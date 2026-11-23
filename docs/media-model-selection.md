# Media model selection

Sandcastle decides what is creatively suitable.
IG decides what is technically executable for the tenant.

Sandcastle ranks models using its own scene, continuity, quality, and budget requirements. It can submit the ordered candidates it recommends. IG uses model declarations, actor-scoped Artifact metadata, and tenant configuration to evaluate those candidates. IG uses prompt length when a catalog declares a limit. It performs no scene-semantic scoring.

## Request and response

Send the same normalized media body to `POST /v2/media/selection` to inspect eligibility and to `POST /v2/media/generations` to create a Job.

```json
{
  "media_type": "video",
  "intent": {"description": "A slow pan across the harbor."},
  "output": {"duration_seconds": 5},
  "selection_policy": {
    "mode": "PREFERRED",
    "candidates": [
      {"provider": "kling", "model": "kling-4.0"},
      {"provider": "alibaba", "model": "wan3.0-video"}
    ],
    "allow_fallback": false
  }
}
```

- **AUTO** tries explicit tenant defaults first. Provider registration order breaks ties. Remaining choices follow provider registration order, each provider's default then stable catalog order.
- **PREFERRED** checks candidates in supplied order. Model-only IDs must identify a unique registration in the requested media catalog. Omitted `allow_fallback` defaults to `false`. When it is `true`, IG uses AUTO after exhausting the list. Picking a later candidate in the supplied list keeps `fallback_used` false, with the earlier rejections recorded.
- **LOCKED** requires exactly one provider and model. IG rejects the request if that candidate fails eligibility.

Inspection is read-only and uses the caller's tenant and actor scope. Delegated service callers need `media:discover` to inspect. Generation requires `media:generate`. A 200 inspection may return `selected: null`. Generation recomputes selection at submission, and an unresolved policy returns 400 with `NO_ELIGIBLE_MODEL`.

The response includes the policy, selected provider/model, stable selection reason, fallback flag, caller candidate rejections, and per-model evaluations. Diagnostics distinguish catalog registration, credential/configuration presence, enabled state, availability, input compatibility, and final eligibility. Examples include `PROVIDER_NOT_CONFIGURED`, `MODEL_DISABLED`, `MODEL_SUNSET`, `OPERATION_UNSUPPORTED`, `REFERENCE_COUNT_UNSUPPORTED`, `ASPECT_RATIO_UNSUPPORTED`, and `DURATION_UNSUPPORTED`. Preview checks declared controls and stored reference metadata. Actual provider access, quotas, Artifact byte access, and provider acceptance are verified during execution.

Generation bodies containing `selection_policy` cannot also specify legacy `advanced.provider` or `advanced.model`. Existing callers that omit the policy retain their prior routing, including image purpose routing. The embedded editor retains its scoped selection rules.

## Execution and audit

Job detail exposes `input.selection` and the compact `input.selection_audit`. For normalized requests, the audit records the original policy, selected candidate, decision reason, fallback, and relevant rejections. Legacy V2 routes preserve their original routing reason and record an equivalent resolved policy. AUTO keeps a bounded diagnostic at failure or when it skips the first choice. Provider payloads and catalog copies are excluded.

Workers use the accepted provider/model and the persisted video operation ID. Later tenant model allowlist or catalog sunset changes govern admission. Existing work continues while the provider, credentials, and adapter implementation remain available. A missing adapter or provider configuration causes execution to fail instead of rerouting. A polling Job may still have upstream work in flight when local completion becomes impossible.

Workflow policy is evaluated at step admission after dependency Artifacts resolve. A retry with a prior accepted Job preserves its model and reuse of completed upstream outputs. Workflow Lab shows requested mode, selected model, fallback, and diagnostics. Generation Lab defaults to AUTO, supports exact models, and can inspect request eligibility on demand.

For normal Sandcastle creator Auto, Sandcastle can send PREFERRED with its own ranked list. A user-selected model can mean PREFERRED with fallback enabled, or LOCKED when exact reproducibility is required.
