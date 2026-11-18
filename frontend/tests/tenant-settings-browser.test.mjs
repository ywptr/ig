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
import '/src/index.css';
import '/src/App.css';
const params=new URL(location.href).searchParams;
const member=params.has('member');
const denied=params.has('delegated');
window.managed={generated_artifact_retention_enabled:true,generated_artifact_retention_days:30,retention_policy_source:'storage_default'};
window.external={generated_artifact_retention_enabled:false,generated_artifact_retention_days:30,retention_policy_source:'storage_default'};
window.drive={google_drive_enabled:false}; window.writes=[];window.calls=[];
window.fetch=async(url,options={})=>{
 window.calls.push(url);
 const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 if(url==='/v2/tenant/model-governance')return reply({providers:[{id:'example-provider',display_name:'example-provider',enabled:true,has_credentials:true,media:[{media_type:'video',restricted:false,default_model:'tenant-video-default',default_override:null,configuration_error:null,models:[{id:'tenant-video-default',display_name:'tenant-video-default',known:true,supported:true,enabled:true,available:true,usable:true,lifecycle:{deprecated:false,sunset_at:null,replacement_model:null}}]}]}]});
 if(url==='/v2/auth/me')return reply({user_id:'user',name:'User',email:'personal@example.test'});
 if(url==='/v2/tenant')return reply({tenant_id:'tenant-one',name:'Studio One',slug:'studio-one',membership:{role:member?'member':'admin'}});
 if(url.startsWith('/v2/tenant/settings/')||url==='/v2/tenant/providers'){
   if(member||denied)return reply({detail:'Tenant admin access required'},403);
   if(options.method==='PATCH'){
     if(window.pauseSave)await new Promise(resolve=>{window.finishSave=resolve});
     if(window.rejectSave)return reply({detail:[{msg:'Retention days must be between 1 and 3650'}]},422);
     window.writes.push({url,body:JSON.parse(options.body)});
     if(url.endsWith('/storage'))window.drive=JSON.parse(options.body);
     else {let value={...JSON.parse(options.body),retention_policy_source:'tenant'};
       if(window.serverDays)value.generated_artifact_retention_days=window.serverDays;
       if(url.endsWith('/external-retention'))window.external=value;else window.managed=value;}
   }
   if(url.endsWith('/storage'))return reply(window.drive);
   if(url.endsWith('/external-retention'))return reply(window.external);
   if(url.endsWith('/retention'))return reply(window.managed);
   return reply([{provider:'example-provider',enabled:true,has_credentials:true,config:{default_models:{video:'tenant-video-default'},private_config:'never-render-this'}}]);
 }
 if(url==='/v2/user/storage')return reply({google_drive_enabled:true,artifact_storage_preference:'ig_managed',connection:{status:'healthy',account_email:'personal-drive@example.test',account_name:'Personal account',target_folder:'IG Artifacts',checked_at:null}});
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



const browserOptions={skip:!browser || typeof WebSocket!=="function",timeout:45000};
const managed="section[aria-labelledby=retention-title]";
const external="section[aria-labelledby=external-retention-title]";

test("admin navigation, direct route, read-only tenant context and private provider summary",browserOptions,async t=>withLab(t,async({evaluate,wait,click,call})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`);
 await click('Tenant Settings');
 await wait(`document.querySelector('#tenant-information-title') && document.querySelector('#provider-settings-title')?.closest('section').textContent.includes('tenant-video-default')`);
 assert.equal(await evaluate('location.pathname'),'/settings/tenant');
 const context=await evaluate(`document.querySelector('.settings-identity').textContent`);
 assert.match(context,/Studio One/);assert.match(context,/studio-one/);assert.match(context,/tenant-one/);
 assert.equal(await evaluate(`document.querySelector('.settings-identity').querySelectorAll('input,textarea,select,button').length`),0);
 const content=await evaluate(`document.querySelector('main').textContent`);
 assert.match(content,/IS\/Nexus remains authoritative/);assert.match(content,/example-provider/);
 assert.doesNotMatch(content,/never-render-this|personal-drive@example.test|Disconnect Google Drive|Connect Google Drive|nexus_tenant_id/);
 assert.match(content,/configure-tenant-provider.py/);
 await call('Page.reload');
 await wait(`document.querySelector('#tenant-information-title')`);
 assert.equal(await evaluate('location.pathname'),'/settings/tenant');
 await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
 assert.equal(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'),true);
}));

test("member navigation hides Tenant Settings and direct route denies without controls",browserOptions,async t=>withLab(t,async({evaluate,wait,call})=>{
 const origin=await evaluate('location.origin');
 await call('Page.navigate',{url:origin+'/settings/tenant?member=1'});
 await wait(`document.querySelector('[role=alert]')?.textContent.includes('Tenant admin access required')`);
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`),false);
 assert.equal(await evaluate(`document.querySelector('.settings-page').querySelectorAll('form').length`),0);
 assert.equal(await evaluate('window.writes.length'),0);
}));

test("admin can enable and disable Drive with independent authoritative section saves",browserOptions,async t=>withLab(t,async({evaluate,wait,click})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`);await click('Tenant Settings');
 await wait(`document.querySelector('#tenant-storage-title')?.closest('section').querySelector('input')`);
 const section=`document.querySelector('#tenant-storage-title').closest('section')`;
 assert.equal(await evaluate(`${section}.querySelector('input').checked`),false);
 for(const enabled of [true,false]){
   await evaluate(`${section}.querySelector('input').click()`);await click('Save Drive availability');
   await wait(`window.drive.google_drive_enabled===${enabled} && ${section}.querySelector('[role=status]')`);
   assert.equal(await evaluate(`${section}.querySelector('strong').textContent`),enabled?'Enabled':'Disabled');
 }
 assert.equal(await evaluate(`window.writes.every(write=>write.url==='/v2/tenant/settings/storage')`),true);
 assert.match(await evaluate(`${section}.textContent`),/private and user-owned/);
}));

test("managed and Drive retention display separate policy, validate and accept server response",browserOptions,async t=>withLab(t,async({evaluate,wait,click,change})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`);await click('Tenant Settings');
 await wait(`document.querySelector('${managed} input[type=number]') && document.querySelector('${external} input[type=number]')`);
 assert.equal(await evaluate(`document.querySelector('${managed} input[type=number]').value`),'30');
 assert.equal(await evaluate(`document.querySelector('${managed} input[type=checkbox]').checked`),true);
 assert.equal(await evaluate(`document.querySelector('${external} input[type=checkbox]').checked`),false);
 assert.match(await evaluate(`document.querySelector('${managed}').textContent`),/Uploads and reference uploads are excluded/);
 await change(`${managed} input[type=number]`,'0');await click('Save retention settings');
 await wait(`document.querySelector('${managed} [role=alert]')?.textContent.includes('1 to 3650')`);
 assert.equal(await evaluate('window.writes.length'),0);
 await change(`${managed} input[type=number]`,'60');
 await evaluate(`document.querySelector('${managed} input[type=checkbox]').click();window.serverDays=75;window.pauseSave=true`);
 await click('Save retention settings');
 await wait(`document.querySelector('${managed} fieldset').disabled`);
 assert.equal(await evaluate(`document.querySelector('${managed} button').textContent`),'Saving…');
 await evaluate('window.pauseSave=false;window.finishSave()');
 await wait(`document.querySelector('${managed} [role=status]')?.textContent==='Retention settings saved.'`);
 assert.equal(await evaluate(`document.querySelector('${managed} input[type=number]').value`),'75');
 assert.equal(await evaluate('window.managed.generated_artifact_retention_enabled'),false);
 assert.equal(await evaluate('window.external.generated_artifact_retention_enabled'),false);
 await evaluate('window.serverDays=null;window.rejectSave=true');await change(`${external} input[type=number]`,'90');
 await click('Save Google Drive retention');
 await wait(`document.querySelector('${external} [role=alert]')?.textContent.includes('Retention days must be between')`);
 await evaluate(`window.rejectSave=false;document.querySelector('${external} input[type=checkbox]').click()`);
 await click('Save Google Drive retention');
 await wait(`window.external.generated_artifact_retention_enabled===true`);
 assert.equal(await evaluate('window.external.generated_artifact_retention_days'),90);
 assert.equal(await evaluate('window.managed.generated_artifact_retention_days'),75);
}));

test("My Settings stays separate and back navigation restores tenant route",browserOptions,async t=>withLab(t,async({evaluate,wait,click})=>{
 await wait(`Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Tenant Settings')`);await click('Tenant Settings');
 await wait(`document.querySelector('#tenant-information-title')`);
 await click('My Settings');await wait(`document.querySelector('#storage-title')`);
 assert.equal(await evaluate('location.pathname'),'/settings/me');
 assert.match(await evaluate(`document.querySelector('main').textContent`),/personal-drive@example.test|Disconnect Google Drive/);
 assert.equal(await evaluate(`document.querySelector('#tenant-information-title')`),null);
 await evaluate('history.back()');await wait(`document.querySelector('#tenant-information-title')`);
 assert.equal(await evaluate('location.pathname'),'/settings/tenant');
}));

test("server denial blocks admin controls even when client identity claims admin",browserOptions,async t=>withLab(t,async({evaluate,wait,call})=>{
 const origin=await evaluate('location.origin');
 await call('Page.navigate',{url:origin+'/settings/tenant?delegated=1'});
 await wait(`document.querySelector('[role=alert]')?.textContent.includes('Tenant admin access required')`);
 assert.equal(await evaluate(`document.querySelector('.settings-page').querySelectorAll('form').length`),0);
 assert.equal(await evaluate('window.writes.length'),0);
}));
