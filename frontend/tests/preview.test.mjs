import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { sourceURL } from "./import-source.mjs";

const { MediaResultCard } = await import(sourceURL("components/MediaResultCard.tsx"));
const result = media => ({ job_id: "j", media_type: media, status: "completed", prompt: "A lighthouse <script>unsafe</script>", provider: "from-api", model: "from-api-model",
    purpose: media === "image" ? "thumbnail" : null, created_at: "2026-01-01", completed_at: "2026-01-02", error: null,
    artifact: { artifact_id: "artifact/id", media_type: media, metadata: { temporary_url: "https://private-provider-canary.test" } } });

test("image preview uses IG content and escapes prompt text", () => {
    const html = renderToStaticMarkup(createElement(MediaResultCard, { result: result("image") }));
    assert.match(html, /<img/); assert.match(html, /\/v2\/artifacts\/artifact%2Fid\/content/);
    assert.equal(html.includes("<script>unsafe</script>"), false);
    assert.equal(html.includes("private-provider-canary"), false);
});
test("video preview has native controls and never uses provider temporary URLs", () => {
    const html = renderToStaticMarkup(createElement(MediaResultCard, { result: result("video") }));
    assert.match(html, /<video[^>]+controls/); assert.match(html, /\/v2\/artifacts\/artifact%2Fid\/content/);
    assert.equal(html.includes("private-provider-canary"), false);
});
