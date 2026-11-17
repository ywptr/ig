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
window.storage={google_drive_enabled:true,artifact_storage_preference:'ig_managed',connection:null};
window.actions=[];
const member=new URL(location.href).searchParams.has('member');
window.fetch=async(url,options={})=>{
 let body=[];
 if(url==='/v2/auth/me') body={user_id:'user',name:'User',email:'user@example.test'};
 else if(url==='/v2/tenant') body={tenant_id:'tenant-one',slug:'one',name:'One',membership:{role:member?'member':'admin'}};
 else if(url==='/v2/user/storage') {
   if(options.method==='PATCH') {window.storage.artifact_storage_preference=JSON.parse(options.body).artifact_storage_preference;window.actions.push('preference');}
   body=window.storage;
 }else if(url==='/v2/user/storage/google-drive/disconnect') {window.storage.connection=null;window.actions.push('disconnect');body=window.storage;}
 else if(url==='/v2/user/storage/google-drive/health') {window.actions.push('health');body=window.storage;}
 else if(url==='/v2/tenant/settings/storage') {
   if(options.method==='PATCH') {window.storage.google_drive_enabled=JSON.parse(options.body).google_drive_enabled;window.actions.push('capability');}
   body={google_drive_enabled:window.storage.google_drive_enabled};
 }else if(url==='/v2/tenant/settings/external-retention') body={generated_artifact_retention_enabled:false,generated_artifact_retention_days:30,retention_policy_source:'storage_default'};
 else if(url==='/v2/tenant/providers') body=[];
 else if(url==='/v2/tenant/settings/retention') body={generated_artifact_retention_enabled:true,generated_artifact_retention_days:30};
 else if(url==='/v2/media/capabilities') body={schema_version:1,media_types:[],interface_levels:{}};
 return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
};
createRoot(document.getElementById('root')).render(<App/>);
`;
async function withLab(t, run) {
    const mediaRequests=[];
    const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS8AAAAASUVORK5CYII=', 'base64');
    const server=await createServer({root:new URL('..',import.meta.url).pathname,configFile:false,server:{host:'127.0.0.1',port:0},plugins:[{name:'reference-fixture',resolveId(id){if(id==='/reference-test.tsx')return id;},load(id){if(id==='/reference-test.tsx')return fixture;},configureServer(server){server.middlewares.use((req,res,next)=>{if(['/fixture','/settings/tenant','/settings/me'].includes(req.url.split('?')[0])){res.setHeader('Content-Type','text/html');res.end('<html><body><div id="root"></div><script type="module" src="/reference-test.tsx"></script></body></html>');}else if(req.url.startsWith('/v2/artifacts/')){mediaRequests.push(req.url);res.setHeader('Content-Type','image/png');res.end(pixel);}else next();});}}]});
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
    await wait(`!!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Tenant Settings') || document.querySelector('.brand-name')`);
    await run({ evaluate, wait, click, change, call, mediaRequests });
}


test("member storage preference, disconnect and disabled governance", {skip:!browser || typeof WebSocket!=="function",timeout:45000}, async t => withLab(t, async ({evaluate,wait,click,call})=>{
 const origin=await evaluate('location.origin');
 await call('Page.navigate',{url:origin+'/fixture?member=1'});
 await wait(`document.querySelector('.brand-name')`);
 await click('My Settings');
 await wait(`document.querySelector('#storage-title') && document.querySelector('input[type=radio]')`);
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`),false);
 assert.equal(await evaluate(`document.querySelectorAll('input[type=radio]')[1].disabled`),true);
 await evaluate(`window.storage.connection={status:'healthy',account_email:'drive@example.test',account_name:'Drive user',target_folder:'IG Artifacts',checked_at:null}`);
 await click('Generate'); await click('My Settings');
 await wait(`!document.querySelectorAll('input[type=radio]')[1].disabled`);
 await evaluate(`document.querySelectorAll('input[type=radio]')[1].click()`);
 await wait(`window.storage.artifact_storage_preference==='google_drive'`);
 await click('Disconnect Google Drive');
 await wait(`document.querySelector('[role=status]')?.textContent.includes('Drive is selected but unavailable')`);
 assert.equal(await evaluate(`window.storage.artifact_storage_preference`),'google_drive');
 await evaluate(`document.querySelector('input[type=radio]').click()`);
 await wait(`window.storage.artifact_storage_preference==='ig_managed'`);
 await evaluate(`window.storage.google_drive_enabled=false`);
 await click('Generate'); await click('My Settings');
 await wait(`document.querySelector('#storage-title')?.closest('section').textContent.includes('New Drive writes are disabled')`);
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Connect Google Drive').disabled`),true);
}));

test("tenant admin sees capability without other users account details", {skip:!browser || typeof WebSocket!=="function",timeout:45000}, async t => withLab(t, async ({evaluate,wait,click})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`);
 await click('Tenant Settings');
 await wait(`document.querySelector('#tenant-storage-title')?.closest('section').querySelector('input')`);
 const section=`document.querySelector('#tenant-storage-title').closest('section')`;
 assert.equal(await evaluate(`${section}.querySelector('input').checked`),true);
 await evaluate(`${section}.querySelector('input').click()`);
 await click('Save Drive availability');
 await wait(`window.storage.google_drive_enabled===false`);
 assert.doesNotMatch(await evaluate(`${section}.textContent`),/drive@example.test|refresh_token|Disconnect Google Drive/);
}));
