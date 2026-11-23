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
const speech = {
    voices: [{ id: 'opaque:one', label: 'Studio One' }, { id: 'opaque:two', label: 'Studio Two' }],
    formats: [{ id: 'mp3', mime_type: 'audio/mpeg' }, { id: 'wav', mime_type: 'audio/wav' }],
    default_voice: 'opaque:one', default_format: 'mp3', max_text_characters: 500,
};
const speechModel = { provider: 'studio', model: 'studio-voice', label: 'Studio Voice', speech };
const videoModel = { provider: 'cinema', model: 'native-video', label: 'Native Video', native_audio_required: true };
const rules = explicit => ({ explicit_provider_selection: explicit, explicit_model_selection: explicit });
const mediaCapabilities = {
    schema_version: 1, interface_levels: { easy: rules(false), medium: rules(false), hard: rules(true) },
    media_types: [{ id: 'audio', available: true, default_provider: 'studio', purposes: [], providers: [{
        id: 'studio', display_name: 'Studio', available: true, default_model: 'studio-voice', default_model_available: true,
        models: [{ id: 'studio-voice', display_name: 'Studio Voice', available: true, usable: true,
            selectable_in: ['hard'], supported_interface_levels: ['easy', 'medium', 'hard'], technical: null, speech,
            capabilities: { text_to_media: true, audio: false, aspect_ratios: [], resolutions: [], styles: [],
                quality_profiles: [], layout_types: ['single'], duration_seconds: null, count: { min: 1, max: 1 } },
        }],
    }] }],
};
mediaCapabilities.media_types.push({ id: 'video', available: true, default_provider: 'cinema', purposes: [],
    providers: [{ id: 'cinema', display_name: 'Cinema', available: true, default_model: 'native-video', default_model_available: true,
        models: [{ id: 'native-video', display_name: 'Native Video', available: true, usable: true,
            selectable_in: ['hard'], supported_interface_levels: ['easy', 'medium', 'hard'], technical: null,
            capabilities: { text_to_media: true, audio: true, native_audio_required: true, max_references: 0,
                aspect_ratios: ['21:9'], resolutions: ['4k'], styles: [], quality_profiles: [],
                layout_types: ['single'], duration_seconds: { min: 3, max: 30 }, count: { min: 1, max: 1 } },
        }],
    }],
});
const step = (operation, output, models, requires_image = false) => ({ operation, output, models, requires_image });
const presets = [
    { id: 'single_generation', label: 'Single generation', available: true,
      steps: [step('text_to_image', 'image', [model]), step('image_to_image', 'image', [model], true), step('text_to_speech', 'audio', [speechModel]), step('text_to_video', 'video', [videoModel])] },
    { id: 'voice_then_video', label: 'Voice → Video', available: false, experimental: true,
      reason: 'No executable tenant model for image_audio_to_video.',
      steps: [step('text_to_speech', 'audio', [speechModel]), step('image_audio_to_video', 'video', [], true)] },
];
const run = { workflow_id: 'run', preset: 'single_generation', status: 'running', steps: [{
    step_id: 'generation', operation: 'image_to_image', status: 'queued', job_ids: ['job'],
    dependencies: [], inputs: { image: { kind: 'artifact', artifact_id: 'frame' } }, outputs: [],
}] };
const voiceOutput = { artifact_id: 'voice-track', filename: 'voice.wav', media_type: 'audio', mime_type: 'audio/wav' };
const completedJob = { job_id: 'speech-job', status: 'completed', capability: 'audio.generate',
    created_at: '2026-11-20T00:00:00Z', completed_at: '2026-11-20T00:00:01Z' };
window.workflowCalls = [];
window.speechRequest = null;
window.workflowState = run;
const realFetch = window.fetch.bind(window);
window.fetch = async (url, options = {}) => {
    const reply = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
    if (url === '/v2/auth/me') return reply({ user_id: 'actor', email: 'actor@example.test' });
    if (url === '/v2/tenant') return reply({ membership: { role: 'member' } });
    if (url === '/v2/media/capabilities') return reply(mediaCapabilities);
    if (url === '/v2/media/generations') {
        const request = JSON.parse(options.body);
        if (request.media_type === 'video') {
            window.videoRequest = request;
            return reply({ media_type: 'video', job_id: 'video-job', request_id: 'video-job', status: 'queued' });
        }
        window.speechRequest = request;
        return reply({ media_type: 'audio', job_id: 'speech-job', request_id: 'speech-job', status: 'queued' });
    }
    if (url === '/v2/jobs') return reply(window.speechRequest ? [completedJob] : []);
    if (url === '/v2/jobs/speech-job') return reply({ ...completedJob, input: { request: window.speechRequest } });
    if (url === '/v2/jobs/speech-job/executions') return reply([{ status: 'completed', attempt: 1,
        metadata: { artifact_id: 'voice-track' }, provider: 'studio', model: 'studio-voice' }]);
    if (url === '/v2/artifacts/voice-track') return reply(voiceOutput);
    if (url === '/v2/workflows/presets') return reply(presets);
    if (url.startsWith('/v2/artifacts/page?')) return reply({ items: [image], has_more: false });
    if (url.startsWith('/v2/artifacts/result/')) return realFetch(url, options);
    if (url === '/v2/workflows') {
        const input = JSON.parse(options.body);
        window.workflowCalls.push(input);
        if (input.operation === 'text_to_video') return reply({
            workflow_id: 'video-run', preset: 'single_generation', status: 'completed',
            steps: [{ ...run.steps[0], operation: 'text_to_video', status: 'completed', inputs: {},
                selection_policy: input.selection_policy,
                selection_resolution: { policy: input.selection_policy, selected: input.selection_policy?.candidates?.[0],
                    reason: "LOCKED_MODEL", fallback_used: false }, outputs: [{ ...output, filename: 'result.mp4', media_type: 'video' }] }],
        });
        if (input.operation === 'text_to_speech') return reply({
            workflow_id: 'speech-run', preset: 'single_generation', status: 'completed',
            steps: [{ ...run.steps[0], operation: 'text_to_speech', status: 'completed', inputs: {},
                selection: { provider: 'studio', model: 'studio-voice' }, speech: input.speech, outputs: [voiceOutput] }],
        });
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
    if (url === '/v2/workflows/video-run') return reply({
        workflow_id: 'video-run', status: 'completed',
        steps: [{ ...run.steps[0], operation: 'text_to_video', status: 'completed', inputs: {},
            selection_policy: window.workflowCalls.at(-1).selection_policy,
            selection_resolution: { policy: window.workflowCalls.at(-1).selection_policy,
                selected: window.workflowCalls.at(-1).selection_policy?.candidates?.[0],
                reason: "LOCKED_MODEL", fallback_used: false },
            outputs: [{ ...output, filename: 'result.mp4', media_type: 'video' }] }],
    });
    if (url === '/v2/workflows/speech-run') return reply({
        workflow_id: 'speech-run', status: 'completed',
        steps: [{ ...run.steps[0], operation: 'text_to_speech', status: 'completed', inputs: {},
            selection: { provider: 'studio', model: 'studio-voice' }, speech: window.workflowCalls.at(-1).speech, outputs: [voiceOutput] }],
    });
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
    const wav = Buffer.alloc(44 + 1600);
    wav.write("RIFF", 0);
    wav.writeUInt32LE(wav.length - 8, 4);
    wav.write("WAVEfmt ", 8);
    wav.writeUInt32LE(16, 16);
    wav.writeUInt16LE(1, 20);
    wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(8000, 24);
    wav.writeUInt32LE(16000, 28);
    wav.writeUInt16LE(2, 32);
    wav.writeUInt16LE(16, 34);
    wav.write("data", 36);
    wav.writeUInt32LE(1600, 40);
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
                    } else if (pathname === "/v2/artifacts/voice-track/content") {
                        response.setHeader("Content-Type", "audio/wav");
                        response.end(wav);
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


test("speech discovery and authenticated playback work in both Labs", { skip: !browser }, async t => {
    await withLab(t, async ({ evaluate, wait, click, change }) => {
        await wait("document.querySelector('.lab-form select')");
        await click("Hard");
        await change(".provider-controls select", "studio");
        await change(".provider-controls label:nth-of-type(2) select", "studio-voice");
        await change(".lab-controls:not(.provider-controls) label:first-child select", "opaque:two");
        await change(".lab-controls:not(.provider-controls) label:nth-child(2) select", "wav");
        await change(".lab-form textarea", "Hello from the lab.");
        await click("Generate audio");
        await wait("document.querySelector('.media-result audio')");
        assert.equal(await evaluate("window.speechRequest.output.voice"), "opaque:two");
        assert.equal(await evaluate("window.speechRequest.advanced.model"), "studio-voice");
        assert.equal(await evaluate("document.querySelector('.media-result audio').getAttribute('src')"),
            "/v2/artifacts/voice-track/content");
        await evaluate("document.querySelector('.media-result audio').play()");
        assert.ok(await evaluate("document.querySelector('.media-result audio').duration > 0"));

        await click("Workflow Lab");
        await wait("document.querySelectorAll('.workflow-lab select').length >= 2");
        await change(".workflow-lab label:nth-of-type(2) select", "text_to_speech");
        await change(".workflow-lab textarea", "Hello from a workflow.");
        await click("Run workflow");
        await wait("document.querySelector('.workflow-lab audio')");
        assert.equal(await evaluate("document.body.textContent.includes('Voice opaque:one')"), true);
        assert.equal(await evaluate("document.querySelector('.workflow-lab audio').preload"), "none");
    });
});

test("native-audio video is discovered and submitted through both Labs", { skip: !browser }, async t => {
    await withLab(t, async ({ evaluate, wait, click, change }) => {
        await wait("document.querySelector('.lab-segments [aria-pressed]')");
        await click("Video");
        await wait("document.body.textContent.includes('Native audio included')");
        await change(".lab-form textarea", "A moving landscape.");
        await click("Generate video");
        await wait("window.videoRequest");
        assert.equal(await evaluate("window.videoRequest.output.audio"), true);
        assert.equal(await evaluate("window.videoRequest.selection_policy.mode"), "AUTO");
        assert.equal(await evaluate("window.videoRequest.output.resolution"), "4k");

        await click("Workflow Lab");
        await wait("document.querySelectorAll('.workflow-lab select').length >= 2");
        await change(".workflow-lab label:nth-of-type(2) select", "text_to_video");
        await wait("document.body.textContent.includes('text to video')");
        await change(".workflow-lab label:nth-of-type(4) select", "0");
        await change(".workflow-lab textarea", "A quiet street.");
        await click("Run workflow");
        await wait("document.body.textContent.includes('Final video')");
        assert.equal(await evaluate("window.workflowCalls.at(-1).selection_policy.candidates[0].model"), "native-video");
        assert.equal(await evaluate("document.body.textContent.includes('cinema / native-video')"), true);
        assert.equal(await evaluate("document.body.textContent.includes('Selection mode LOCKED') && document.body.textContent.includes('locked model')"), true);
    });
});
