// Optional real-browser check. Node 20: run with --experimental-websocket.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { sourceURL } from "./import-source.mjs";

const cache = join(homedir(), ".cache/ms-playwright");
let browser;
try {
    browser = readdirSync(cache).filter(name => name.startsWith("chromium_headless_shell-"))
        .map(name => join(cache, name, "chrome-headless-shell-linux64/chrome-headless-shell")).find(existsSync);
} catch { /* The static renderer tests remain available without a browser install. */ }
const { MediaResultCard } = await import(sourceURL("components/MediaResultCard.tsx"));
const fixture = media => ({ job_id: media, media_type: media, status: "completed", prompt: "Preview test",
    created_at: "2026-01-01", completed_at: "2026-01-02", provider: "fixture", model: "fixture", purpose: null, error: null,
    artifact: { artifact_id: media, media_type: media } });

async function until(predicate) {
    for (let attempt = 0; attempt < 200; attempt++) {
        if (predicate()) return;
        await delay(25);
    }
    throw new Error("Browser network observation timed out");
}

test("browser loads only previews until an explicit original click", {
    skip: !browser || typeof globalThis.WebSocket !== "function", timeout: 25000,
}, async t => {
    const requests = [];
    const markup = renderToStaticMarkup(createElement(Fragment, null,
        createElement(MediaResultCard, { result: fixture("image") }), createElement(MediaResultCard, { result: fixture("video") })));
    const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS8AAAAASUVORK5CYII=", "base64");
    const server = createServer((request, response) => {
        requests.push(request.url);
        if (request.url === "/list") {
            response.setHeader("Content-Type", "text/html");
            response.end(`<!doctype html><html><body>${markup}</body></html>`);
        } else if (/\/v2\/artifacts\/(image|video)\/(preview|content)$/.test(request.url)) {
            response.setHeader("Content-Type", "image/png");
            response.end(pixel);
        } else { response.statusCode = 404; response.end(); }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    t.after(() => { server.closeAllConnections(); server.close(); });
    const profile = mkdtempSync(join(tmpdir(), "ig-preview-browser-"));
    const child = spawn(browser, ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
        "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
    const exited = new Promise(resolve => child.once("exit", resolve));
    t.after(async () => { child.kill(); await exited; rmSync(profile, { recursive: true, force: true }); });
    let diagnostics = "";
    child.stderr.on("data", chunk => { diagnostics = (diagnostics + chunk).slice(-16000); });
    await until(() => diagnostics.includes("DevTools listening on") || child.exitCode !== null);
    assert.equal(child.exitCode, null, diagnostics);
    const port = new URL(diagnostics.match(/ws:\/\/[^\s]+/)[0]).port;
    const target = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" }).then(response => response.json());
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    t.after(() => socket.close());
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
    let next = 0;
    const pending = new Map();
    socket.addEventListener("message", event => {
        const message = JSON.parse(event.data);
        if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
    });
    const call = (method, params = {}) => new Promise((resolve, reject) => {
        const id = ++next;
        pending.set(id, message => message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result));
        socket.send(JSON.stringify({ id, method, params }));
    });
    await call("Page.enable");
    await call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 1800, deviceScaleFactor: 1, mobile: false });
    await call("Page.navigate", { url: `http://127.0.0.1:${server.address().port}/list` });
    await until(() => requests.includes("/v2/artifacts/image/preview") && requests.includes("/v2/artifacts/video/preview"));
    assert.equal(requests.filter(path => path.endsWith("/content")).length, 0);
    const video = await call("Runtime.evaluate", { expression: "document.querySelectorAll('video').length", returnByValue: true });
    assert.equal(video.result.value, 0);
    await call("Runtime.evaluate", { expression: "document.querySelector('a[href$=\"/content\"]').click()", userGesture: true });
    await until(() => requests.includes("/v2/artifacts/image/content"));
    t.diagnostic("List: 2 preview requests, 0 original requests. Explicit click: original requested.");
});
