import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

// Use the project's existing compiler and Node test runner; no test dependencies.
const source = readFileSync(new URL("../src/lib/generation.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 } }).outputText;
const { INITIAL_FORM, buildMediaRequest, resolveLabForm, isActiveStatus, executionArtifactId, latestExecution,
    matchesSubmission, pendingReceipt, resultFromJob } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

const controls = { text_to_media: true, reference_roles: [], max_references: 0, aspect_ratios: ["1:1", "16:9"],
    resolutions: [], duration_seconds: null, audio: false, count: { min: 1, max: 1 }, layout_types: ["single"],
    quality_profiles: ["draft", "standard", "high"], styles: ["auto", "illustration"] };
const model = (id, capabilities = controls) => ({ id, display_name: `Display ${id}`, available: true,
    supported_interface_levels: ["easy", "medium", "hard"], selectable_in: ["hard"], capabilities, use_cases: [] });
const provider = (id, models) => ({ id, display_name: `Provider ${id}`, available: true, configured: true, enabled: true,
    default_model: models[0].id, default_model_available: true, models });
function capabilities() { return {
    interface_levels: Object.fromEntries(["easy", "medium", "hard"].map(level => [level, {
        explicit_provider_selection: level === "hard", explicit_model_selection: level === "hard",
    }])),
    media_types: [{ id: "image", available: true, purposes: ["general", "thumbnail"], default_provider: "alpha",
        providers: [provider("alpha", [model("a")]), provider("beta", [model("b")])] },
    { id: "video", available: true, purposes: [], default_provider: "motion", providers: [provider("motion", [model("v", {
        ...controls, aspect_ratios: ["16:9", "9:16", "1:1"], resolutions: ["480p", "720p"],
        duration_seconds: { min: 2, max: 30 }, audio: true, quality_profiles: [], styles: [],
    })])] }],
}; }
const form = patch => ({ ...INITIAL_FORM, prompt: "  A lighthouse  ", ...patch });

test("all four active states poll; terminal states stop", () => {
    for (const status of ["queued", "running", "waiting", "polling"]) assert.equal(isActiveStatus(status), true);
    for (const status of ["completed", "failed", "generating", "unknown"]) assert.equal(isActiveStatus(status), false);
});
test("easy image uses generic purpose and no explicit selection or hidden advanced controls", () => {
    const request = buildMediaRequest(capabilities(), form({ purpose: "thumbnail", provider: "beta", model: "b", quality: "high", style: "illustration" }));
    assert.equal(request.media_type, "image"); assert.equal(request.output.purpose, "thumbnail");
    assert.equal(request.intent.description, "A lighthouse"); assert.equal(request.output.count, 1);
    assert.equal(request.advanced, undefined); assert.equal(request.output.quality, undefined); assert.equal(request.output.style, undefined);
});
test("medium exposes generic quality/style but still sends no provider", () => {
    const request = buildMediaRequest(capabilities(), form({ mode: "medium", provider: "beta", model: "b", quality: "high", style: "illustration" }));
    assert.equal(request.advanced, undefined); assert.equal(request.output.quality, "high"); assert.equal(request.output.style, "illustration");
});
test("hard supports Auto, provider default, or explicit stable IDs", () => {
    assert.equal(buildMediaRequest(capabilities(), form({ mode: "hard" })).advanced, undefined);
    assert.deepEqual(buildMediaRequest(capabilities(), form({ mode: "hard", provider: "beta" })).advanced, { provider: "beta" });
    assert.deepEqual(buildMediaRequest(capabilities(), form({ mode: "hard", provider: "beta", model: "b" })).advanced, { provider: "beta", model: "b" });
});
test("unavailable defaults require a supported explicit model", () => {
    const caps = capabilities(); caps.media_types[0].providers[1].default_model_available = false;
    assert.throws(() => buildMediaRequest(caps, form({ mode: "hard", provider: "beta" })), /available/);
    assert.equal(buildMediaRequest(caps, form({ mode: "hard", provider: "beta", model: "b" })).advanced.model, "b");
});
test("video starts at discovered minimum duration, first resolution, and audio off", () => {
    const request = buildMediaRequest(capabilities(), form({ media: "video", purpose: "thumbnail" }));
    assert.deepEqual(request.output, { aspect_ratio: "16:9", audio: false, resolution: "480p", duration_seconds: 2 });
    assert.equal(request.advanced, undefined);
});
test("controls clamp to capabilities and unsupported choices reset", () => {
    const request = buildMediaRequest(capabilities(), form({ media: "video", aspect: "unsupported", resolution: "unsupported", duration: 900 }));
    assert.equal(request.output.duration_seconds, 30); assert.equal(request.output.resolution, "480p"); assert.equal(request.output.aspect_ratio, "16:9");
});
test("switching media never submits a provider or model from the other media type", () => {
    const request = buildMediaRequest(capabilities(), form({ mode: "hard", media: "video", provider: "beta", model: "b" }));
    assert.equal(request.advanced, undefined);
});
test("Auto uses shared controls without reproducing purpose routing", () => {
    const caps = capabilities();
    caps.media_types[0].providers[1].models[0].capabilities = { ...controls, aspect_ratios: ["16:9"], quality_profiles: ["standard"] };
    const resolved = resolveLabForm(caps, form({ purpose: "thumbnail", quality: "high" }));
    assert.deepEqual(resolved.controls.aspect_ratios, ["16:9"]);
    assert.equal(resolved.values.quality, "standard"); assert.equal(resolved.values.provider, "");
});
test("unavailable media and models do not become selector options", () => {
    const caps = capabilities(); caps.media_types[0].available = false;
    assert.equal(resolveLabForm(caps, form()).values.media, "video");
    caps.media_types[1].providers[0].models[0].available = false;
    assert.equal(resolveLabForm(caps, form()).usable, false);
    assert.throws(() => buildMediaRequest(caps, form()), /available/);
});
test("image correlation is exact request identity, never prompt or timestamp", () => {
    const result = resultFromJob({ job_id: "job-1", capability: "image.generate", status: "completed", created_at: "2026-01-01",
        completed_at: "2026-01-02", error_message: null, input: { request_id: "request-1", intent: { description: "Same prompt" },
            selection: { provider: "beta", model: "b", purpose: "thumbnail" } } });
    assert.equal(matchesSubmission(result, { media_type: "image", request_id: "request-1" }), true);
    assert.equal(matchesSubmission(result, { media_type: "image", request_id: "request-2", prompt: "Same prompt" }), false);
    assert.equal(result.purpose, "thumbnail"); assert.equal(result.provider, "beta");
});
test("video correlation uses job ID and generic persisted request", () => {
    const result = resultFromJob({ job_id: "video-job", capability: "video.generate", status: "waiting", created_at: "2026-01-01",
        completed_at: null, error_message: null, input: { request: { intent: { description: "Moving lighthouse" } }, selection: { provider: "motion", model: "v" } } });
    assert.equal(result.prompt, "Moving lighthouse"); assert.equal(result.media_type, "video");
    assert.equal(matchesSubmission(result, { media_type: "video", job_id: "video-job" }), true);
    assert.equal(matchesSubmission(result, { media_type: "video", job_id: "different" }), false);
});
test("only the latest completed Execution's explicit artifact link is used", () => {
    const first = { attempt: 1, status: "completed", metadata: { artifact_id: "artifact-1" } };
    const latest = { attempt: 2, status: "failed", metadata: { artifact_id: "not-an-output" } };
    assert.equal(executionArtifactId(latestExecution([first, latest])), null);
    assert.equal(executionArtifactId(first), "artifact-1");
    assert.equal(executionArtifactId({ status: "completed", metadata: { artifact_id: { url: "wrong" } } }), null);
});
test("unmatched accepted receipts stop polling after a bounded recovery window", () => {
    const receipt = { media_type: "image", request_id: "request-1", submitted_at: "2026-01-01T00:00:00Z" };
    assert.equal(pendingReceipt(receipt, [], Date.parse(receipt.submitted_at) + 10_000), true);
    assert.equal(pendingReceipt(receipt, [], Date.parse(receipt.submitted_at) + 61_000), false);
    assert.equal(pendingReceipt(receipt, [{ media_type: "image", request_id: "request-1" }], Date.parse(receipt.submitted_at)), false);
});
