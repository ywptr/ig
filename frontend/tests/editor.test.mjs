import test from "node:test";
import assert from "node:assert/strict";
import { sourceURL } from "./import-source.mjs";
const { editorForm, editorEvent, validParentMessage, restrictedCapabilities } = await import(sourceURL("lib/editor.ts"));
const { setEditorCredential, authenticatedOptions } = await import(sourceURL("api/editorContext.ts"));

test("image/video draft prefill preserves prompt, controls, model and references", () => {
    for (const media of ["image", "video"]) {
        const draft = { media_type: media, interface_level: "hard", intent: { description: "Prepared scene brief" },
            advanced: { provider: "chosen", model: "model" }, output: { aspect_ratio: "9:16", duration_seconds: 7, resolution: "720p", audio: true },
            references: [{ artifact_id: "owned", role: media === "video" ? "first_frame" : "character", ...(media === "image" ? { strength: "strong" } : {}) }] };
        const form = editorForm(draft, [{ artifact_id: "owned" }]);
        assert.equal(form.prompt, "Prepared scene brief");
        assert.equal(form.media, media);
        assert.equal(form.provider, "chosen");
        assert.equal(form.duration, 7);
        assert.equal(form.references[0].role, draft.references[0].role);
        assert.equal(form.references[0].strength, draft.references[0].strength);
        assert.throws(() => editorForm(draft, []), /unavailable/);
    }
});
test("editor capability filter preserves discovery and media restrictions", () => {
    const source = { media_types: [{ id: "image" }, { id: "video" }] };
    assert.deepEqual(restrictedCapabilities(source, { allowed_media: ["video"] }).media_types, [{ id: "video" }]);
    assert.equal(source.media_types.length, 2);
});
test("versioned events carry session and correlation, strict incoming origin/source/session", () => {
    const target = {};
    const session = { session_id: "editor", correlation_id: "scene", parent_origin: "https://sandcastle.test" };
    const message = editorEvent(session, "ig.editor.completed", { job_id: "job", artifacts: [{ artifact_id: "a", media_type: "video" }] });
    assert.equal(message.version, 1);
    assert.equal(message.correlation_id, "scene");
    const event = { source: target, origin: session.parent_origin, data: { type: "ig.editor.close", version: 1, session_id: "editor" } };
    assert.equal(validParentMessage(event, session, target), true);
    for (const patch of [{ origin: "https://evil.test" }, { source: {} }, { data: { ...event.data, session_id: "wrong" } }, { data: { ...event.data, version: 2 } }]) {
        assert.equal(validParentMessage({ ...event, ...patch }, session, target), false);
    }
    assert.doesNotMatch(JSON.stringify(message), /token|storage_uri|Bearer|private_key/);
    assert.doesNotMatch(JSON.stringify(editorEvent(session, "ig.editor.ready", { token: "secret", storage_uri: "secret", session_id: "other" })), /secret|other/);
});
test("editor HTTP authority stays in headers, uses no browser cookies and cannot leave IG API", () => {
    setEditorCredential({ session: "editor", token: "opaque" });
    const options = authenticatedOptions("/v2/jobs", { method: "GET" });
    assert.equal(options.credentials, "omit");
    assert.equal(options.headers.get("X-IG-Editor-Token"), "opaque");
    assert.equal(options.headers.get("Authorization"), null);
    assert.throws(() => authenticatedOptions("https://storage.test"), /IG API/);
    setEditorCredential(null);
    assert.equal(authenticatedOptions("/v2/jobs").credentials, "same-origin");
});
