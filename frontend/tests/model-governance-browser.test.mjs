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
const params=new URL(location.href).searchParams;
const member=params.has('member');const missing=params.has('missing');
window.policy={restricted:true,default_model:'canvas-b',default_override:'canvas-b',configuration_error:null,media_type:'image',models:[
{id:'canvas-a',display_name:'Canvas A',known:true,supported:true,enabled:true,available:true,usable:true,lifecycle:{deprecated:false,sunset_at:null,replacement_model:null}},
{id:'canvas-b',display_name:'Canvas B',known:true,supported:true,enabled:true,available:true,usable:true,lifecycle:{deprecated:true,sunset_at:null,replacement_model:'canvas-a'}},
{id:'canvas-c',display_name:'Canvas C',known:true,supported:true,enabled:false,available:false,usable:false,lifecycle:{deprecated:false,sunset_at:null,replacement_model:null}}]};
window.modelWrites=[];
window.fetch=async(url,options={})=>{
 const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 const document=()=>({providers:[{id:'studio-graphics',display_name:'Studio Graphics',enabled:true,has_credentials:!missing,media:[window.policy]}]});
 if(url==='/v2/auth/me')return reply({user_id:'user',email:'user@example.test'});
 if(url==='/v2/tenant')return reply({tenant_id:'tenant',name:'Studio',slug:'studio',membership:{role:member?'member':'admin'}});
 if(url.startsWith('/v2/tenant/')&&member)return reply({detail:'Tenant admin access required'},403);
 if(url==='/v2/tenant/model-governance')return reply(document());
 if(url==='/v2/tenant/model-governance/studio-graphics'){
  const body=JSON.parse(options.body);window.modelWrites.push(body);
  window.policy.restricted=body.enabled_models!==null;window.policy.default_override=body.default_model;
  for(const model of window.policy.models){model.enabled=body.enabled_models===null||body.enabled_models.includes(model.id);model.available=model.usable=model.enabled;}
  window.policy.default_model=body.default_model||window.policy.models.find(model=>model.enabled)?.id||null;
  return reply(document());
 }
 if(url==='/v2/tenant/settings/storage')return reply({google_drive_enabled:false});
 if(url==='/v2/tenant/providers')return reply([]);
 if(url==='/v2/tenant/settings/retention'||url==='/v2/tenant/settings/external-retention')return reply({generated_artifact_retention_enabled:false,generated_artifact_retention_days:30,retention_policy_source:'storage_default'});
 if(url==='/v2/media/capabilities')return reply({schema_version:1,media_types:[],interface_levels:{}});
 return reply([]);
};
createRoot(document.getElementById('root')).render(<App/>);
`;
async function withLab(t, run) {
    const mediaRequests=[];
    const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS8AAAAASUVORK5CYII=', 'base64');
    const server=await createServer({root:new URL('..',import.meta.url).pathname,configFile:false,server:{host:'127.0.0.1',port:0},plugins:[{name:'reference-fixture',resolveId(id){if(id==='/reference-test.tsx')return id;},load(id){if(id==='/reference-test.tsx')return fixture;},configureServer(server){server.middlewares.use((req,res,next)=>{if(['/fixture','/settings/tenant','/settings/me'].includes(req.url.split('?')[0])){res.setHeader('Content-Type','text/html');res.end('<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/reference-test.tsx"></script></body></html>');}else if(req.url.startsWith('/v2/artifacts/')){mediaRequests.push(req.url);res.setHeader('Content-Type','image/png');res.end(pixel);}else next();});}}]});
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



const options={skip:!browser||typeof WebSocket!=="function",timeout:45000};
const form=`form[aria-label="Studio Graphics image models"]`;

test('dynamic catalog governs enabled models and default with explicit saves',options,async t=>withLab(t,async({evaluate,wait,click,change})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`);await click('Tenant Settings');
 await wait(`document.querySelector('${form} select')`);
 assert.match(await evaluate(`document.querySelector('${form}').textContent`),/Canvas A.*Canvas B.*Canvas C/s);
 assert.match(await evaluate(`document.querySelector('${form}').textContent`),/Deprecated/);
 await evaluate(`document.querySelectorAll('${form} input[type=checkbox]')[2].click()`);
 await click('Save model settings');
 await wait(`document.querySelector('${form} [role=alert]')?.textContent.includes('default must be')`);
 assert.equal(await evaluate('window.modelWrites.length'),0);
 await change(`${form} select`,'canvas-a');await click('Save model settings');
 await wait(`document.querySelector('${form} [role=status]')?.textContent==='Model settings saved.'`);
 assert.equal(await evaluate('window.policy.default_model'),'canvas-a');
 assert.deepEqual(await evaluate('window.modelWrites[0].enabled_models'),['canvas-a']);
 assert.equal(await evaluate(`document.querySelector('${form} select').value`),'canvas-a');
 await evaluate(`document.querySelector('${form} input[type=checkbox]').click()`);await click('Save model settings');
 await wait('window.modelWrites.length===2');assert.equal(await evaluate('window.modelWrites[1].enabled_models'),null);
}));

test('provider without credentials shows setup guidance and disables editing',options,async t=>withLab(t,async({evaluate,wait,call})=>{
 const origin=await evaluate('location.origin');await call('Page.navigate',{url:origin+'/settings/tenant?missing=1'});
 await wait(`document.querySelector('${form} fieldset')`);
 assert.equal(await evaluate(`document.querySelector('${form} fieldset').disabled`),true);
 assert.match(await evaluate(`document.querySelector('${form}').textContent`),/credentials are not configured/);
}));

test('member direct route never renders model governance controls',options,async t=>withLab(t,async({evaluate,wait,call})=>{
 const origin=await evaluate('location.origin');await call('Page.navigate',{url:origin+'/settings/tenant?member=1'});
 await wait(`document.querySelector('[role=alert]')?.textContent.includes('Tenant admin access required')`);
 assert.equal(await evaluate(`document.querySelector('${form}')`),null);
}));
