import test from "node:test";
import assert from "node:assert/strict";
import { sourceURL } from "./import-source.mjs";

const { createGenerationLoader } = await import(sourceURL("api/generations.ts"));
const { submitMedia, getMediaCapabilities } = await import(sourceURL("api/media.ts"));
const { requestJSON, safeMessage } = await import(sourceURL("api/client.ts"));
const { getJobs } = await import(sourceURL("api/jobs.ts"));
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function fakeFetch(t, handler) {
    const original = globalThis.fetch;
    t.after(() => { globalThis.fetch = original; });
    globalThis.fetch = async (url, options) => { assert.match(url, /^\//, "Only IG relative endpoints may be called"); return handler(url, options); };
}

test("submission keeps image request identity and video Job identity without forcing identical IDs", async t => {
    fakeFetch(t, (url, options) => {
        assert.equal(url, "/v2/media/generations"); assert.equal(options.method, "POST"); assert.equal(options.credentials, "same-origin");
        const body = JSON.parse(options.body);
        assert.equal(body.advanced, undefined);
        return json(body.media_type === "image" ? { media_type: "image", request_id: "request-1", status: "generating" }
            : { media_type: "video", request_id: "video-job", job_id: "video-job", status: "queued" }, 202);
    });
    const image = await submitMedia({ media_type: "image", interface_level: "easy", intent: { description: "Image prompt" }, output: { purpose: "thumbnail" } });
    assert.equal(image.request_id, "request-1"); assert.equal(image.job_id, undefined); assert.equal(image.purpose, "thumbnail");
    assert.ok(Number.isFinite(Date.parse(image.submitted_at)));
    const video = await submitMedia({ media_type: "video", interface_level: "easy", intent: { description: "Video prompt" }, output: { audio: false } });
    assert.equal(video.job_id, "video-job"); assert.equal(video.prompt, "Video prompt");
});

test("reload recovery joins exact Job input and terminal Execution Artifact links, then caches completed results", async t => {
    let phase = "waiting";
    const calls = [];
    const summary = id => ({ job_id: id, capability: id === "i" ? "image.generate" : "video.generate", status: phase,
        created_at: "2026-01-01T00:00:00", started_at: "2026-01-01T00:00:01", completed_at: phase === "completed" ? "2026-01-01T00:01:00" : null, error_message: null });
    fakeFetch(t, url => {
        calls.push(url);
        if (url === "/v2/jobs") return json([summary("i"), summary("v")]);
        if (url === "/v2/jobs/i") return json({ ...summary("i"), input: { request_id: "image-request", intent: { description: "Image prompt" }, selection: { provider: "test-image", model: "test-model", purpose: "thumbnail" } } });
        if (url === "/v2/jobs/v") return json({ ...summary("v"), input: { request: { intent: { description: "Video prompt" } }, selection: { provider: "test-motion", model: "test-video" } } });
        if (url.endsWith("/executions")) return json([{ attempt: 1, status: "completed", provider: "resolved", model: "resolved-model", metadata: { artifact_id: url.includes("/i/") ? "image-artifact" : "video-artifact" } }]);
        if (url.startsWith("/v2/artifacts/")) return json({ artifact_id: url.split("/").at(-1), media_type: url.includes("image") ? "image" : "video", metadata: {}, mime_type: url.includes("image") ? "image/png" : "video/mp4" });
        assert.fail(`Unexpected request ${url}`);
    });
    const load = createGenerationLoader(); const signal = new AbortController().signal;
    const waiting = await load(signal);
    assert.equal(waiting.results.length, 2); assert.equal(waiting.results[0].status, "waiting");
    assert.equal(waiting.results.find(result => result.media_type === "image").request_id, "image-request");
    assert.equal(waiting.results.find(result => result.media_type === "video").prompt, "Video prompt");
    assert.equal(calls.some(url => url.endsWith("/executions")), false);
    phase = "completed";
    const complete = await load(signal);
    assert.equal(complete.results.find(result => result.media_type === "video").artifact.artifact_id, "video-artifact");
    const previous = calls.length;
    await load(signal);
    assert.deepEqual(calls.slice(previous), ["/v2/jobs"]);
    assert.equal(calls.filter(url => url === "/v2/jobs/i").length, 1);
});

test("missing result links produce a refreshable error instead of guessing an Artifact", async t => {
    let linked = false;
    const job = { job_id: "j", capability: "image.generate", status: "completed", created_at: "2026-01-01", completed_at: "2026-01-02", error_message: null };
    fakeFetch(t, url => {
        if (url === "/v2/jobs") return json([job]);
        if (url === "/v2/jobs/j") return json({ ...job, input: { request_id: "r", intent: { description: "A prompt" } } });
        if (url.endsWith("/executions")) return json([{ attempt: 1, status: "completed", metadata: linked ? { artifact_id: "correct" } : { other: { artifact_id: "wrong" } } }]);
        assert.equal(url, "/v2/artifacts/correct");
        return json({ artifact_id: "correct", media_type: "image" });
    });
    const load = createGenerationLoader(); const signal = new AbortController().signal;
    const missing = await load(signal);
    assert.equal(missing.results[0].artifact, null); assert.match(missing.results[0].error, /no linked Artifact/);
    linked = true;
    assert.equal((await load(signal)).results[0].artifact.artifact_id, "correct");
});

test("validation, authentication, HTML, network and credential-shaped errors are safe", async t => {
    let mode = "validation";
    fakeFetch(t, () => {
        if (mode === "validation") return json({ detail: { message: "No available image provider" } }, 400);
        if (mode === "unauthenticated") return json({ detail: "private-error-canary" }, 401);
        if (mode === "html") return new Response("<html>private-error-canary</html>", { status: 500 });
        throw new Error("private-error-canary");
    });
    await assert.rejects(requestJSON("/v2/test"), /No available image provider/);
    mode = "unauthenticated";
    await assert.rejects(requestJSON("/v2/test"), error => error.status === 401 && !error.message.includes("private-error-canary"));
    mode = "html";
    await assert.rejects(requestJSON("/v2/test"), error => !error.message.includes("<html>") && !error.message.includes("private-error-canary"));
    mode = "network";
    await assert.rejects(requestJSON("/v2/test"), /Unable to reach IG/);
    assert.equal(safeMessage("Authorization: private-error-canary", "safe"), "safe");
    assert.equal(safeMessage("Traceback (most recent call last)", "safe"), "safe");
});

test("invalid capability data is isolated from the independently usable Jobs API", async t => {
    fakeFetch(t, url => json(url === "/v2/media/capabilities" ? {} : []));
    await assert.rejects(getMediaCapabilities(), /invalid capability document/);
    assert.deepEqual(await getJobs(), []);
});

test("artifact page client carries cursor, filters and cancellation without requesting media bytes", async t => {
    const { listArtifactPage } = await import(sourceURL("api/artifacts.ts"));
    const signal = new AbortController().signal;
    fakeFetch(t, (url, options) => {
        const query = new URL(url, "http://ig.test");
        assert.equal(query.pathname, "/v2/artifacts/page");
        assert.equal(query.searchParams.get("cursor"), "opaque+/=");
        assert.equal(query.searchParams.get("q"), "blue_100%");
        assert.equal(query.searchParams.get("provenance"), "uploaded");
        assert.equal(query.searchParams.get("page_size"), "20");
        assert.equal(options.signal, signal);
        assert.equal(options.credentials, "same-origin");
        return json({ items: [{ artifact_id: "a", provenance: "uploaded", preview: { status: "missing", url: "/v2/artifacts/a/preview?v=1" } }],
            next_cursor: null, has_more: false });
    });
    const page = await listArtifactPage({ cursor: "opaque+/=", q: "blue_100%", provenance: "uploaded", page_size: 20 }, signal);
    assert.equal(page.items[0].artifact_id, "a");
    assert.equal(page.has_more, false);
});
