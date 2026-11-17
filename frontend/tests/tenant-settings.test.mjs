import test from "node:test";
import assert from "node:assert/strict";
import { sourceURL } from "./import-source.mjs";
const api = await import(sourceURL("api/tenantSettings.ts"));
const { workspaceView, workspacePath } = await import(sourceURL("lib/settingsNavigation.ts"));
const { setEditorCredential } = await import(sourceURL("api/editorContext.ts"));
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function fakeFetch(t, handler) {
    const original = globalThis.fetch;
    t.after(() => { globalThis.fetch = original; setEditorCredential(null); });
    globalThis.fetch = async (url, options = {}) => handler(url, options);
}

test("settings deep links, OAuth return and back-navigation remain separate", () => {
    assert.equal(workspaceView({ pathname: "/settings/tenant", search: "" }), "settings");
    assert.equal(workspaceView({ pathname: "/settings/tenant/", search: "?storage=connected" }), "settings");
    assert.equal(workspaceView({ pathname: "/settings/me", search: "" }), "storage");
    assert.equal(workspaceView({ pathname: "/", search: "?storage=connected" }), "storage");
    assert.equal(workspaceView({ pathname: "/", search: "" }, "jobs"), "jobs");
    assert.equal(workspaceView({ pathname: "/", search: "" }, "settings"), "generate");
    assert.equal(workspacePath("settings"), "/settings/tenant");
    assert.equal(workspacePath("storage"), "/settings/me");
});

test("existing admin endpoints return independent backend-resolved retention policies", async t => {
    const calls = [];
    fakeFetch(t, (url, options) => {
        calls.push(url);
        assert.equal(options.credentials, "same-origin");
        return json({ generated_artifact_retention_enabled: !url.endsWith("external-retention"), generated_artifact_retention_days: 30, retention_policy_source: "storage_default" });
    });
    const [managed, external] = await Promise.all([api.getRetentionPolicy("managed"), api.getRetentionPolicy("external")]);
    assert.equal(managed.generated_artifact_retention_enabled, true);
    assert.equal(external.generated_artifact_retention_enabled, false);
    assert.equal(managed.retention_policy_source, "storage_default");
    assert.deepEqual(calls, ["/v2/tenant/settings/retention", "/v2/tenant/settings/external-retention"]);
});

test("retention saves target only the affected scope and accept authoritative response", async t => {
    const calls = [];
    fakeFetch(t, (url, options) => {
        calls.push(url);
        assert.equal(options.method, "PATCH");
        assert.deepEqual(JSON.parse(options.body), { generated_artifact_retention_enabled: false, generated_artifact_retention_days: 60 });
        return json({ generated_artifact_retention_enabled: false, generated_artifact_retention_days: 75, retention_policy_source: "tenant" });
    });
    const result = await api.saveRetentionPolicy("managed", false, "60");
    assert.equal(result.generated_artifact_retention_days, 75);
    await api.saveRetentionPolicy("external", false, "60");
    assert.deepEqual(calls, ["/v2/tenant/settings/retention", "/v2/tenant/settings/external-retention"]);
});

test("invalid retention durations never write to the server", async t => {
    fakeFetch(t, () => { throw new Error("unexpected network request"); });
    for (const value of ["", " ", "0", "-1", "3651", "2.5", "invalid"]) {
        assert.throws(() => api.saveRetentionPolicy("external", true, value), /whole number.*1 to 3650/);
    }
});

test("Drive governance saves only capability and uses the server response", async t => {
    fakeFetch(t, (url, options) => {
        assert.equal(url, "/v2/tenant/settings/storage");
        assert.deepEqual(JSON.parse(options.body), { google_drive_enabled: true });
        return json({ google_drive_enabled: false });
    });
    assert.equal((await api.saveDriveCapability(true)).google_drive_enabled, false);
});

test("forbidden admin responses and field validation remain actionable errors", async t => {
    fakeFetch(t, () => json({ detail: "Tenant admin access required" }, 403));
    await assert.rejects(api.getDriveCapability(), error => error.status === 403 && /Tenant admin/.test(error.message));
    globalThis.fetch = async () => json({ detail: [{ loc: ["body", "generated_artifact_retention_days"], msg: "Input should be greater than or equal to 1" }] }, 422);
    await assert.rejects(api.saveRetentionPolicy("managed", true, "30"), /greater than or equal to 1/);
});

test("provider summary allowlists model defaults and omits credentials/arbitrary config", async t => {
    fakeFetch(t, url => {
        assert.equal(url, "/v2/tenant/providers");
        return json([{ provider: "example", enabled: true, has_credentials: true, credentials: { api_key: "private" },
            config: { default_model: "image-default", default_models: { video: "video-default", private: "hidden" }, api_key: "private", base_url: "private-url" } }]);
    });
    const result = await api.getProviderSummaries();
    assert.deepEqual(result, [{ provider: "example", enabled: true, has_credentials: true,
        defaults: [{ media: "Default", model: "image-default" }, { media: "video", model: "video-default" }] }]);
    assert.doesNotMatch(JSON.stringify(result), /private|api_key|base_url/);
});

test("tenant context request is read-only and abortable", async t => {
    const controller = new AbortController();
    fakeFetch(t, (url, options) => {
        assert.equal(url, "/v2/tenant");
        assert.equal(options.signal, controller.signal);
        assert.equal(options.method, undefined);
        return json({ name: "Current tenant", slug: "current", tenant_id: "tenant", membership: { role: "admin" } });
    });
    assert.equal((await api.getTenantInfo(controller.signal)).name, "Current tenant");
});

test("editor context never converts scoped API denial into tenant admin access", async t => {
    setEditorCredential({ session: "editor-session", token: "editor-token" });
    fakeFetch(t, (url, options) => {
        assert.equal(options.credentials, "omit");
        assert.equal(new Headers(options.headers).get("X-IG-Editor-Session"), "editor-session");
        return json({ detail: { code: "EDITOR_ROUTE_FORBIDDEN" } }, 403);
    });
    await assert.rejects(api.getDriveCapability(), error => error.status === 403);
});
