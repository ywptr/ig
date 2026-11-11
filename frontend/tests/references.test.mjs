import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { sourceURL } from "./import-source.mjs";
const refs = await import(sourceURL("lib/references.ts"));
const { ReferenceAttachments, ReferenceThumbnail } = await import(sourceURL("components/ReferenceAttachments.tsx"));
const { buildMediaRequest, INITIAL_FORM } = await import(sourceURL("lib/generation.ts"));
const { listArtifactPage, uploadArtifact } = await import(sourceURL("api/artifacts.ts"));
const imageSlot = { name: "references", media_types: ["image"], mime_types: ["image/png", "image/jpeg", "image/webp"],
    roles: ["character", "style"], min_count: 0, max_count: 2, max_bytes: 32 * 1024 * 1024, strengths: ["loose", "normal", "strong"] };
const videoSlot = { ...imageSlot, name: "first_frame", roles: ["first_frame"], max_count: 1, max_bytes: 10 * 1024 * 1024, strengths: [] };
const artifact = (id = "stable-id", extra = {}) => ({ artifact_id: id, filename: "display.png", media_type: "image", mime_type: "image/png", size_bytes: 100, metadata: {}, ...extra });
function mockFetch(t, handler) { const original = globalThis.fetch; t.after(() => { globalThis.fetch = original; }); globalThis.fetch = handler; }
const response = value => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
test("artifact page/search/provenance/cursor are passed as bounded API queries", async t => {
    const urls = [];
    mockFetch(t, async url => { urls.push(url); return response({ items: [artifact()], next_cursor: "next", has_more: true }); });
    assert.equal((await listArtifactPage({ page_size: 20, q: "blue & sky", media_type: "image", provenance: "uploaded" })).items.length, 1);
    await listArtifactPage({ cursor: "next", order: "asc" });
    assert.equal(new URL(urls[0], "http://ig").searchParams.get("q"), "blue & sky");
    assert.match(urls[0], /page_size=20/); assert.match(urls[0], /provenance=uploaded/);
    assert.match(urls[1], /cursor=next/); assert.match(urls[1], /order=asc/);
});
test("select previous artifact retains stable identity and defaults supported strength", () => {
    const state = refs.attachArtifact(artifact(), [], imageSlot, "character");
    assert.equal(state[0].artifact_id, "stable-id"); assert.equal(state[0].strength, "normal");
    assert.equal(state[0].artifact.filename, "display.png");
    assert.deepEqual(refs.structuredReferences(state), [{ artifact_id: "stable-id", role: "character", strength: "normal" }]);
});
test("remove, role and strength edits retain identity", () => {
    let state = refs.attachArtifact(artifact(), [], imageSlot);
    state = state.map(ref => ({ ...ref, role: "style", strength: "strong" }));
    assert.deepEqual(refs.referenceErrors(state, [imageSlot]), []);
    assert.deepEqual(refs.structuredReferences(state), [{ artifact_id: "stable-id", role: "style", strength: "strong" }]);
    assert.deepEqual(refs.removeAttachment(state, "stable-id"), []);
});
test("incompatible media, MIME and known byte sizes cannot attach; missing size remains server validated", () => {
    for (const extra of [{ media_type: "video" }, { mime_type: "image/gif" }, { size_bytes: imageSlot.max_bytes + 1 }, { size_bytes: 0 }])
        assert.equal(refs.canAttach(artifact("bad", extra), [], imageSlot), false);
    assert.equal(refs.canAttach(artifact("legacy", { size_bytes: null }), [], imageSlot), true);
});
test("max count and duplicate artifact IDs are enforced", () => {
    let state = refs.attachArtifact(artifact("a"), [], imageSlot);
    assert.throws(() => refs.attachArtifact(artifact("a"), state, imageSlot));
    state = refs.attachArtifact(artifact("b"), state, imageSlot);
    assert.throws(() => refs.attachArtifact(artifact("c"), state, imageSlot));
});
test("Alibaba-style declaration permits one first_frame without strength", () => {
    const state = refs.attachArtifact(artifact(), [], videoSlot);
    assert.deepEqual(refs.structuredReferences(state), [{ artifact_id: "stable-id", role: "first_frame" }]);
    assert.throws(() => refs.attachArtifact(artifact("second"), state, videoSlot));
    const html = renderToStaticMarkup(createElement(ReferenceAttachments, { references: state, slots: [videoSlot], onChange() {} }));
    assert.doesNotMatch(html, />Strength</); assert.match(html, /first frame/);
});
test("model changes retain attachments and flag unsupported roles, strengths, bytes and excess counts", () => {
    const state = refs.attachArtifact(artifact(), [], imageSlot);
    assert.match(refs.referenceErrors(state, [videoSlot])[0], /role/);
    assert.match(refs.referenceErrors(state, [{ ...imageSlot, strengths: [] }])[0], /strength/);
    assert.match(refs.referenceErrors(state, [{ ...imageSlot, max_bytes: 1 }])[0], /incompatible/);
    assert.ok(refs.referenceErrors([...state, { ...state[0], artifact_id: "b" }], [{ ...imageSlot, max_count: 1 }]).some(error => /at most/.test(error)));
    assert.match(refs.referenceErrors([], [{ ...imageSlot, min_count: 1 }])[0], /at least/);
});
test("Auto conservatively intersects reference capabilities and disables unknown contracts", () => {
    assert.equal(refs.sharedReferenceSlots([[imageSlot], [{ ...imageSlot, max_count: 1, max_bytes: 1000 }]])[0].max_count, 1);
    assert.equal(refs.sharedReferenceSlots([[imageSlot], [{ ...imageSlot, max_bytes: 1000 }]])[0].max_bytes, 1000);
    assert.deepEqual(refs.sharedReferenceSlots([[imageSlot], []]), []);
});
test("uploads reject unsupported MIME, empty file and 10 MiB overflow before network", () => {
    assert.throws(() => refs.validateUpload({ type: "image/gif", size: 100 }, imageSlot), /supported/);
    assert.throws(() => refs.validateUpload({ type: "image/png", size: 0 }, imageSlot), /empty/);
    assert.throws(() => refs.validateUpload({ type: "image/png", size: 10 * 1024 * 1024 + 1 }, imageSlot), /limit/);
    assert.throws(() => refs.validateUpload({ type: "image/png", size: 101 }, { ...imageSlot, max_bytes: 100 }), /limit/);
});
test("multipart upload returns artifact for immediate attachment without a second list lookup", async t => {
    let calls = 0;
    mockFetch(t, async (url, options) => {
        calls++; assert.equal(url, "/v2/artifacts/uploads"); assert.equal(options.method, "POST");
        assert.equal(options.credentials, "same-origin"); assert.ok(options.body instanceof FormData);
        assert.equal(options.body.get("origin"), "reference"); assert.equal(options.body.get("file").name, "new.png");
        assert.equal(options.headers, undefined); return response(artifact("new-id"));
    });
    const file = new File(["png"], "new.png", { type: "image/png" });
    refs.validateUpload(file, imageSlot);
    const state = refs.attachArtifact(await uploadArtifact(file), [], imageSlot);
    assert.equal(calls, 1); assert.equal(state[0].artifact_id, "new-id");
});
test("preview-only thumbnails and failed previews keep selectable metadata without originals", () => {
    for (const preview of [undefined, { status: "processing", url: "/v2/artifacts/stable-id/preview?v=1" }, { status: "failed" }]) {
        const html = renderToStaticMarkup(createElement(ReferenceThumbnail, { artifact: artifact("stable-id", { preview }) }));
        assert.doesNotMatch(html, /\/content|<video|<a /);
        if (preview?.status === "failed") assert.match(html, /Preview unavailable/); else assert.match(html, /\/preview/);
        assert.equal(refs.canAttach(artifact("stable-id", { preview }), [], imageSlot), true);
    }
});
test("both generation paths send structured IDs independent of prompt and display labels", () => {
    const controls = { text_to_media: true, aspect_ratios: ["1:1"], resolutions: [], duration_seconds: null, audio: false,
        count: { min: 1, max: 1 }, layout_types: ["single"], quality_profiles: [], styles: [] };
    for (const [media, slot] of [["image", imageSlot], ["video", videoSlot]]) {
        const model = { id: "model", available: true, supported_interface_levels: ["hard"], selectable_in: ["hard"], capabilities: controls, technical: { reference_inputs: [slot] } };
        const caps = { interface_levels: { hard: { explicit_provider_selection: true, explicit_model_selection: true } }, media_types: [{ id: media, available: true, purposes: [], providers: [{ id: "provider", available: true, default_model: "model", default_model_available: true, models: [model] }] }] };
        const references = refs.attachArtifact(artifact(), [], slot);
        const form = { ...INITIAL_FORM, mode: "hard", media, provider: "provider", model: "model", prompt: "Plain prompt without artifact identity", references };
        const request = buildMediaRequest(caps, form);
        assert.deepEqual(request.references, refs.structuredReferences(references));
        assert.equal(request.intent.description, form.prompt); assert.doesNotMatch(request.intent.description, /stable-id/);
        assert.throws(() => buildMediaRequest(caps, { ...form, references: [{ ...references[0], role: "invalid" }] }), /role/);
    }
});
