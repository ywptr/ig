import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { createServer } from "vite";
let browser;
try { const cache = join(homedir(), ".cache/ms-playwright"); browser = readdirSync(cache).filter(name => name.startsWith("chromium_headless_shell-")).map(name => join(cache, name, "chrome-headless-shell-linux64/chrome-headless-shell")).find(existsSync); } catch {}
async function until(predicate) { for (let n = 0; n < 200; n++) { if (await predicate()) return; await delay(25); } throw new Error("Browser condition timed out"); }
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import App from '/src/App.tsx';
window.policy={generated_artifact_retention_enabled:true,generated_artifact_retention_days:28};window.saves=[];
const member=new URL(location.href).searchParams.has('member');
window.fetch=async(url,options={})=>{
 let body=[];
 if(url==='/v2/auth/me') body={user_id:'user',name:'Admin',email:'admin@example.test'};
 else if(url==='/v2/tenant') body={membership:{role:member?'member':'admin'}};
 else if(url==='/v2/tenant/settings/retention') {
   if(member) return new Response('{}',{status:403});
   if(options.method==='PATCH') {if(window.rejectSave)return new Response('{}',{status:422});window.policy=JSON.parse(options.body);window.saves.push(window.policy);}
   body=window.policy;
 }else if(url==='/v2/media/capabilities')body={schema_version:1,media_types:[],interface_levels:{}};
 return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
};
createRoot(document.getElementById('root')).render(<App/>);
`;
async function withLab(t, run) {
    const mediaRequests=[];
    const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS8AAAAASUVORK5CYII=', 'base64');
    const server=await createServer({root:new URL('..',import.meta.url).pathname,configFile:false,server:{host:'127.0.0.1',port:0},plugins:[{name:'reference-fixture',resolveId(id){if(id==='/reference-test.tsx')return id;},load(id){if(id==='/reference-test.tsx')return fixture;},configureServer(server){server.middlewares.use((req,res,next)=>{if(req.url==='/fixture'){res.setHeader('Content-Type','text/html');res.end('<html><body><div id="root"></div><script type="module" src="/reference-test.tsx"></script></body></html>');}else if(req.url.startsWith('/v2/artifacts/')){mediaRequests.push(req.url);res.setHeader('Content-Type','image/png');res.end(pixel);}else next();});}}]});
    await server.listen();t.after(()=>server.close());
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

    const evaluate=async expression=>{const value=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(value.exceptionDetails)throw new Error(JSON.stringify(value.exceptionDetails));return value.result?.value;};
    const wait=expression=>until(()=>evaluate(`Boolean(${expression})`));
    const click=text=>evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()===${JSON.stringify(text)}).click()`);
    const change=(selector,value)=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:e instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event(e instanceof HTMLSelectElement?'change':'input',{bubbles:true}));})()`);
    await call('Page.navigate',{url:`http://127.0.0.1:${server.httpServer.address().port}/fixture`});
    await wait(`!!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Settings') || document.querySelector('.brand-name')`);
    await run({ evaluate, wait, click, change, call, mediaRequests });
}

test("admin retention settings persist enable, disable and duration with errors", {skip:!browser || typeof WebSocket!=="function",timeout:45000}, async t => withLab(t, async ({evaluate,wait,click,change})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Settings')`);
 await click('Settings');
 await wait(`document.querySelector('input[type=number]')`);
 assert.equal(await evaluate(`document.querySelector('input[type=number]').value`),'28');
 assert.match(await evaluate(`document.querySelector('main').textContent`),/Uploads and reference uploads are excluded/);
 await evaluate(`document.querySelector('input[type=checkbox]').click()`);
 await change('input[type=number]','60');
 await click('Save retention settings');
 await wait(`document.querySelector('[role=status]')?.textContent==='Retention settings saved.'`);
 assert.equal(await evaluate(`window.policy.generated_artifact_retention_enabled`),false);
 assert.equal(await evaluate(`window.policy.generated_artifact_retention_days`),60);
 await click('Jobs');await click('Settings');
 await wait(`document.querySelector('input[type=number]')?.value==='60'`);
 assert.equal(await evaluate(`document.querySelector('input[type=checkbox]').checked`),false);
 await change('input[type=number]','0');
 await evaluate(`document.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))`);
 await wait(`document.querySelector('[role=status]')?.textContent.includes('1 to 3650')`);
 assert.equal(await evaluate(`window.saves.length`),1);
 await change('input[type=number]','30');
 await evaluate(`document.querySelector('input[type=checkbox]').click(); window.rejectSave=true`);
 await click('Save retention settings');
 await wait(`document.querySelector('[role=status]')?.textContent.includes('Unable to save')`);
 await evaluate(`window.rejectSave=false`);await click('Save retention settings');
 await wait(`window.saves.length===2`);
 assert.equal(await evaluate(`window.policy.generated_artifact_retention_enabled`),true);
 assert.equal(await evaluate(`window.policy.generated_artifact_retention_days`),30);
}));

test("member cannot see retention settings", {skip:!browser || typeof WebSocket!=="function",timeout:45000}, async t => withLab(t, async ({evaluate,wait,call})=>{
 const origin=await evaluate('location.origin');
 await call('Page.navigate',{url:origin+'/fixture?member=1'});
 await wait(`document.querySelector('.brand-name')`);
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Settings')`),false);
 assert.equal(await evaluate(`document.querySelector('input[type=number]')!==null`),false);
}));
