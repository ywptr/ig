import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { createServer } from "vite";
let browser;
try {
    const cache = join(homedir(), ".cache/ms-playwright");
    browser = readdirSync(cache).filter(name => name.startsWith("chromium_headless_shell-"))
        .map(name => join(cache, name, "chrome-headless-shell-linux64/chrome-headless-shell")).find(existsSync);
} catch {
    // Chromium is optional for unit-only environments.
}

async function until(predicate) {
    for (let attempt = 0; attempt < 240; attempt++) {
        if (await predicate()) return;
        await delay(25);
    }
    throw new Error("Browser condition timed out");
}

const fixture = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '/src/App.tsx';

const image = { artifact_id: 'frame', filename: 'frame.png', media_type: 'image' };
const output = { artifact_id: 'result', filename: 'result.png', media_type: 'image' };
const model = { provider: 'test', model: 'canvas', label: 'Canvas' };
const step = (operation, output, models, requires_image = false) => ({ operation, output, models, requires_image });
const presets = [
    { id: 'single_generation', label: 'Single generation', available: true,
      steps: [step('text_to_image', 'image', [model]), step('image_to_image', 'image', [model], true)] },
    { id: 'voice_then_video', label: 'Voice → Video', available: false, experimental: true,
      reason: 'No executable tenant model for text_to_speech.',
      steps: [step('text_to_speech', 'audio', []), step('image_audio_to_video', 'video', [], true)] },
];
const run = { workflow_id: 'run', preset: 'single_generation', status: 'running', steps: [{
    step_id: 'generation', operation: 'image_to_image', status: 'queued', job_ids: ['job'],
    dependencies: [], inputs: { image: { kind: 'artifact', artifact_id: 'frame' } }, outputs: [],
}] };
window.workflowCalls = [];
window.workflowState = run;
const realFetch = window.fetch.bind(window);
window.fetch = async (url, options = {}) => {
    const reply = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
    if (url === '/v2/auth/me') return reply({ user_id: 'actor', email: 'actor@example.test' });
    if (url === '/v2/tenant') return reply({ membership: { role: 'member' } });
    if (url === '/v2/media/capabilities') return reply({ schema_version: 1, media_types: [], interface_levels: {} });
    if (url === '/v2/workflows/presets') return reply(presets);
    if (url.startsWith('/v2/artifacts/page?')) return reply({ items: [image], has_more: false });
    if (url.startsWith('/v2/artifacts/result/')) return realFetch(url, options);
    if (url === '/v2/workflows') {
        window.workflowCalls.push(JSON.parse(options.body));
        window.workflowState = structuredClone(run);
        return reply(window.workflowState);
    }
    if (url === '/v2/workflows/run/cancel') {
        window.workflowCalls.push('cancel');
        window.workflowState.status = 'canceled';
    }
    if (url === '/v2/workflows/run/retry') {
        window.workflowCalls.push('retry');
        window.workflowState.status = 'completed';
        window.workflowState.steps[0].status = 'completed';
        window.workflowState.steps[0].outputs = [output];
        window.workflowState.steps[0].retryable = false;
    }
    if (url.startsWith('/v2/workflows/run')) return reply(window.workflowState);
    if (url === '/v2/workflows/voice-run') return reply({
        workflow_id: 'voice-run', preset: 'voice_then_video', status: 'completed',
        steps: [
            { ...run.steps[0], step_id: 'voice', operation: 'text_to_speech', status: 'completed',
              outputs: [{ artifact_id: 'voice-track', filename: 'voice.wav', media_type: 'audio' }] },
            { ...run.steps[0], step_id: 'video', operation: 'image_audio_to_video', status: 'completed',
              inputs: { voice_track: { kind: 'step_output', step_id: 'voice' } },
              outputs: [{ ...output, filename: 'result.mp4', media_type: 'video' }] },
        ],
    });
    return reply([]);
};
createRoot(document.getElementById('root')).render(<App />);
`;
async function withLab(t, run) {
    const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS8AAAAASUVORK5CYII=", "base64");
    const fixtureModule = "/workflow-test.tsx";
    const server = await createServer({
        root: new URL("..", import.meta.url).pathname,
        configFile: false,
        server: { host: "127.0.0.1", port: 0 },
        plugins: [{
            name: "workflow-fixture",
            resolveId(id) { if (id === fixtureModule) return id; },
            load(id) { if (id === fixtureModule) return fixture; },
            configureServer(vite) {
                vite.middlewares.use((request, response, next) => {
                    const pathname = request.url.split("?")[0];
                    if (pathname === "/fixture" || pathname === "/workflows") {
                        response.setHeader("Content-Type", "text/html");
                        response.end(`<div id="root"></div><script type="module" src="${fixtureModule}"></script>`);
                    } else if (pathname === "/v2/artifacts/result/preview") {
                        response.setHeader("Content-Type", "image/png");
                        response.end(pixel);
                    } else {
                        next();
                    }
                });
            },
        }],
    });
    t.after(() => server.close());
    await server.listen();

    const profile = mkdtempSync(join(tmpdir(), "ig-workflow-browser-"));
    const child = spawn(browser, ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
        "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
    const exited = new Promise(resolve => child.once("exit", resolve));
    t.after(async () => {
        child.kill();
        await exited;
        rmSync(profile, { recursive: true, force: true });
    });
    let diagnostics = "";
    child.stderr.on("data", chunk => { diagnostics = (diagnostics + chunk).slice(-16000); });
    await until(() => diagnostics.includes("DevTools listening on") || child.exitCode !== null);
    assert.equal(child.exitCode, null, diagnostics);

    const port = new URL(diagnostics.match(/ws:\/\/[^\s]+/)[0]).port;
    const target = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" }).then(response => response.json());
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    t.after(() => socket.close());
    await new Promise((resolve, reject) => {
        socket.addEventListener("open", resolve, { once: true });
        socket.addEventListener("error", reject, { once: true });
    });

    let nextId = 0;
    const pending = new Map();
    socket.addEventListener("message", event => {
        const message = JSON.parse(event.data);
        const handler = pending.get(message.id);
        if (!handler) return;
        pending.delete(message.id);
        handler(message);
    });
    const call = (method, params = {}) => new Promise((resolve, reject) => {
        const id = ++nextId;
        pending.set(id, message => {
            if (message.error) reject(new Error(JSON.stringify(message.error)));
            else resolve(message.result);
        });
        socket.send(JSON.stringify({ id, method, params }));
    });
    await call("Page.enable");

    async function evaluate(expression) {
        const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true, userGesture: true });
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
        return result.result?.value;
    }
    const wait = expression => until(() => evaluate(`Boolean(${expression})`));
    const click = label => evaluate(`Array.from(document.querySelectorAll('button'))
        .find(button => button.textContent.trim() === ${JSON.stringify(label)}).click()`);
    const change = (selector, value) => evaluate(`(() => {
        const input = document.querySelector(${JSON.stringify(selector)});
        const prototype = Object.getPrototypeOf(input);
        Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, ${JSON.stringify(value)});
        input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    })()`);
    await call("Page.navigate", { url: `http://127.0.0.1:${server.httpServer.address().port}/fixture` });
    await wait("document.querySelector('.brand-name')");
    await run({ evaluate, wait, click, change });
}

test("Workflow Lab gates capabilities, submits references, polls, retries and cancels", { skip: !browser }, async t => {
    await withLab(t, async ({ evaluate, wait, click, change }) => {
        await click("Workflow Lab");
        await wait("document.querySelector('.workflow-lab select option')");
        await change(".workflow-lab select", "voice_then_video");
        await wait("document.body.textContent.includes('No executable tenant model')");
        assert.equal(await evaluate("document.querySelector('.workflow-lab button').disabled"), true);
        assert.equal(await evaluate("document.querySelectorAll('[aria-label=\"Planned steps\"] li').length"), 2);

        await change(".workflow-lab select", "single_generation");
        await change(".workflow-lab label:nth-of-type(2) select", "image_to_image");
        await change(".workflow-lab textarea", "A test frame");
        await change("[list=workflow-image-artifacts]", "frame");
        await click("Run workflow");
        await wait("document.body.textContent.includes('Status running')");
        assert.equal(await evaluate("window.workflowCalls[0].image_artifact_id"), "frame");
        await evaluate("window.workflowState.status = 'failed'; window.workflowState.steps[0].retryable = true");
        await wait("document.body.textContent.includes('Retry failed step')");
        await click("Retry failed step");
        await wait("document.body.textContent.includes('Final image')");
        assert.equal(await evaluate("document.querySelector('.workflow-lab img').getAttribute('src')"), "/v2/artifacts/result/preview");

        await click("Run workflow");
        await wait("document.body.textContent.includes('Cancel workflow')");
        await click("Cancel workflow");
        await wait("document.body.textContent.includes('Status canceled')");
        assert.deepEqual(await evaluate("window.workflowCalls.filter(x => typeof x === 'string')"), ["retry", "cancel"]);
        await change('.workflow-lab form:nth-of-type(2) input', "voice-run");
        await click("Inspect run");
        await wait("document.body.textContent.includes('Intermediate audio')");
        assert.equal(await evaluate("document.body.textContent.includes('Final video')"), true);
        assert.equal(await evaluate("document.querySelector('img[src*=voice-track]')"), null);
    });
});
