import test from "node:test";
import assert from "node:assert/strict";
import { sourceURL } from "./import-source.mjs";

const api = await import(sourceURL("api/workflows"));
const navigation = await import(sourceURL("lib/settingsNavigation"));

test("Workflow Lab has a stable route for reopening a run", () => {
    const url = new URL("https://ig.example/workflows?id=sample");
    assert.equal(navigation.workspaceView(url), "workflows");
    assert.equal(navigation.workspacePath("workflows"), url.pathname);
});

test("Workflow requests preserve the submission key and address a specific run", async t => {
    const requests = [];
    t.mock.method(globalThis, "fetch", async (url, options) => {
        requests.push({ url, options });
        return Response.json({});
    });
    const submission = { preset: "single_generation", operation: "text_to_image", prompt: "Paint a cloud",
        selection_policy: { mode: "PREFERRED", candidates: [{ model: "model-a" }, { model: "model-b" }], allow_fallback: false } };
    await api.submitWorkflow(submission, "attempt-one");
    await api.getWorkflow("sample");
    await api.updateWorkflow("sample", "cancel");
    await api.updateWorkflow("sample", "retry");
    assert.deepEqual(requests.map(request => request.url), [
        "/v2/workflows", "/v2/workflows/sample", "/v2/workflows/sample/cancel", "/v2/workflows/sample/retry",
    ]);
    assert.equal(new Headers(requests[0].options.headers).get("Idempotency-Key"), "attempt-one");
    assert.deepEqual(JSON.parse(requests[0].options.body), submission);
});
