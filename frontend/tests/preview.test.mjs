import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { sourceURL } from "./import-source.mjs";

const { MediaResultCard } = await import(sourceURL("components/MediaResultCard.tsx"));
const { default: ImageCard } = await import(sourceURL("components/ImageCard.tsx"));
const result = media => ({ job_id: "j", media_type: media, status: "completed", prompt: "A lighthouse <script>unsafe</script>", provider: "from-api", model: "from-api-model",
    purpose: media === "image" ? "thumbnail" : null, created_at: "2026-01-01", completed_at: "2026-01-02", error: null,
    artifact: { artifact_id: "artifact/id", media_type: media, metadata: { temporary_url: "https://private-provider-canary.test" },
        preview: { url: "/v2/artifacts/artifact%2Fid/preview?v=1", status: "missing" } } });

for (const media of ["image", "video"]) test(`${media} list renders static preview, original only as explicit link`, () => {
    const html = renderToStaticMarkup(createElement(MediaResultCard, { result: result(media) }));
    assert.match(html, /<img[^>]+src="\/v2\/artifacts\/artifact%2Fid\/preview\?v=1"/);
    assert.match(html, /href="\/v2\/artifacts\/artifact%2Fid\/content"/);
    assert.doesNotMatch(html, /<(video|source)|autoplay|preload=|<img[^>]+src="[^"]*\/content/);
    assert.equal(html.includes("<script>unsafe</script>"), false);
    assert.equal(html.includes("private-provider-canary"), false);
});

test("legacy history uses artifact preview and leaves original behind click", () => {
    const html = renderToStaticMarkup(createElement(ImageCard, { image: {
        request_id: "request-1", artifact_id: "artifact-1", status: "completed", created_at: "2026-01-01", prompt: "A lighthouse",
    } }));
    assert.match(html, /src="\/v2\/artifacts\/artifact-1\/preview"/);
    assert.match(html, /href="\/v1\/images\/request-1\/content"/);
    assert.doesNotMatch(html, /<img[^>]+src="[^"]*\/content/);
});

test("legacy records without artifact identity show placeholder, never original fallback", () => {
    const html = renderToStaticMarkup(createElement(ImageCard, { image: {
        request_id: "old-1", status: "completed", created_at: "2026-01-01", prompt: "A lighthouse",
    } }));
    assert.doesNotMatch(html, /<img|<video/);
    assert.match(html, /href="\/v1\/images\/old-1\/content"/);
});
