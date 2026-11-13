import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdtempSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { createServer as httpServer } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { createServer } from "vite";
let browser;
try { const cache = join(homedir(), ".cache/ms-playwright"); browser = readdirSync(cache).filter(name => name.startsWith("chromium_headless_shell-")).map(name => join(cache, name, "chrome-headless-shell-linux64/chrome-headless-shell")).find(existsSync); } catch {}
async function until(predicate) { for (let n = 0; n < 300; n++) { if (await predicate()) return; await delay(30); } throw new Error("Embed browser condition timed out"); }

test("cross-origin parent → shared editor → prefilled draft → generation → strict completion and close", {
    skip: !browser || typeof WebSocket !== "function", timeout: 45000,
}, async t => {
    let editorOrigin;
    const parent = httpServer((req, res) => {
        res.setHeader("Content-Type", "text/html");
        res.end(`<html><body><script>window.events=[];addEventListener('message',e=>{if(e.origin===${JSON.stringify(editorOrigin)}&&e.source===document.querySelector('iframe').contentWindow&&e.data.session_id==='session'){if(e.data.type==='ig.editor.handshake')e.source.postMessage({...e.data,type:'ig.editor.authorize'},${JSON.stringify(editorOrigin)});else window.events.push(e.data);}});</script><iframe src="${editorOrigin}/embed/generation/session#launch=one-time-launch" width="1100" height="900"></iframe></body></html>`);
    });
    await new Promise(resolve => parent.listen(0, "127.0.0.1", resolve));
    t.after(() => { parent.closeAllConnections(); parent.close(); });
    const parentOrigin = `http://127.0.0.1:${parent.address().port}`;
    const fixture = `
const slot={name:'references',media_types:['image'],mime_types:['image/png'],roles:['character'],min_count:0,max_count:2,max_bytes:10485760,strengths:['normal']};
const controls={text_to_media:true,reference_roles:['character'],max_references:2,aspect_ratios:['1:1','16:9'],resolutions:[],duration_seconds:null,audio:false,count:{min:1,max:1},layout_types:['single'],quality_profiles:['standard'],styles:['auto']};
const model={id:'image-model',display_name:'Image model',usable:true,available:true,supported_interface_levels:['easy','medium','hard'],selectable_in:['hard'],capabilities:controls,technical:{version:1,input_media:['text','image'],output_media:['image'],operations:['text_to_image','reference_image_conditioning'],reference_inputs:[slot],reference_mime_types:['image/png'],output_mime_types:['image/png'],execution:{mode:'synchronous',polling_required:false,provider_cancellation_supported:false}}};
const caps={schema_version:1,interface_levels:Object.fromEntries(['easy','medium','hard'].map(mode=>[mode,{explicit_provider_selection:mode==='hard',explicit_model_selection:mode==='hard'}])),media_types:[{id:'image',available:true,purposes:['general'],default_provider:'fixture',providers:[{id:'fixture',display_name:'Fixture',available:true,configured:true,enabled:true,default_model:'image-model',default_model_available:true,models:[model]}]}]};
let submitted=false;let jobReads=0;
const draft={media_type:'image',interface_level:'hard',intent:{description:'Prepared Sandcastle scene'},output:{aspect_ratio:'16:9'},references:[]};
window.fetch=async(url,options={})=>{
 let body;const headers=new Headers(options.headers);
 if(String(url).endsWith('/redeem'))body={session_id:'session',correlation_id:'scene-17',parent_origin:${JSON.stringify(parentOrigin)},expires_at:new Date(Date.now()+60000).toISOString(),editor_token:'opaque-browser-only',draft,policy:{allowed_media:['image'],allowed_operations:['text_to_image'],reference_editing:true,upload:false,model_switching:true},callback_context:{},draft_errors:[]};
 else {if(headers.get('X-IG-Editor-Token')!=='opaque-browser-only'||options.credentials!=='omit')throw Error('Missing editor authorization');
 if(url==='/v2/media/capabilities')body=caps;
 else if(url==='/v2/media/generations'){if(!headers.get('Idempotency-Key'))throw Error('Missing attempt identity');submitted=true;body={media_type:'image',request_id:'request',job_id:'job',status:'queued'};}
 else if(url==='/v2/jobs')body=submitted?[{job_id:'job',capability:'image.generate',status:++jobReads>1?'completed':'running',created_at:'2026-01-01',completed_at:jobReads>1?'2026-01-02':null,error_message:null}]:[];
 else if(url==='/v2/jobs/job')body={job_id:'job',capability:'image.generate',status:'completed',created_at:'2026-01-01',completed_at:'2026-01-02',input:{prompt:'Prepared Sandcastle scene'},error_message:null};
 else if(url==='/v2/jobs/job/executions')body=[{execution_id:'execution',status:'completed',attempt:1,metadata:{artifact_id:'artifact'}}];
 else if(url==='/v2/artifacts/artifact')body={artifact_id:'artifact',media_type:'image',mime_type:'image/png',filename:'output.png',size_bytes:10,metadata:{},preview:{status:'missing',url:'/v2/artifacts/artifact/preview'}};
 else if(String(url).includes('/preview'))return new Response('{}',{status:202});
 else if(String(url).startsWith('/v2/artifacts/page'))body={items:[],has_more:false,next_cursor:null};
 else if(url==='/v2/editor/session/close')body={status:'closed'};
 else throw Error('Unexpected API path '+url);}
 return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
};
await import('/src/embed.tsx');`;
    const server = await createServer({root:new URL('..',import.meta.url).pathname,configFile:false,
        server:{host:'127.0.0.1',port:0},plugins:[{name:'embed-fixture',resolveId(id){if(id==='/editor-fixture.ts')return id;},load(id){if(id==='/editor-fixture.ts')return fixture;},configureServer(server){server.middlewares.use((req,res,next)=>{
            if(req.url.startsWith('/embed/generation/')){res.setHeader('Content-Type','text/html');res.setHeader('Content-Security-Policy',`frame-ancestors ${parentOrigin}`);res.end(`<html><body><div id="root" data-parent-origin="${parentOrigin}"></div><script type="module" src="/editor-fixture.ts"></script></body></html>`);}else next();
        });}}]});
    await server.listen(); t.after(()=>server.close());
    editorOrigin=`http://127.0.0.1:${server.httpServer.address().port}`;
    const profile=mkdtempSync(join(tmpdir(),'ig-embed-browser-'));
    const child=spawn(browser,['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
    const exited=new Promise(resolve=>child.once('exit',resolve));
    t.after(async()=>{child.kill();await exited;rmSync(profile,{recursive:true,force:true});});
    let diagnostics='';child.stderr.on('data',chunk=>{diagnostics=(diagnostics+chunk).slice(-16000);});
    await until(()=>diagnostics.includes('DevTools listening on')||child.exitCode!==null);
    assert.equal(child.exitCode,null,diagnostics);
    const port=new URL(diagnostics.match(/ws:\/\/[^\s]+/)[0]).port;
    const target=await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'}).then(r=>r.json());
    const socket=new WebSocket(target.webSocketDebuggerUrl);t.after(()=>socket.close());
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    let next=0;const pending=new Map();socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(pending.has(message.id)){pending.get(message.id)(message);pending.delete(message.id);}});
    const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,message=>message.error?reject(Error(JSON.stringify(message.error))):resolve(message.result));socket.send(JSON.stringify({id,method,params}));});
    const evaluate=async(expression,contextId)=>{const result=await call('Runtime.evaluate',{expression,contextId,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value;};
    await call('Page.enable');await call('Page.navigate',{url:parentOrigin});
    await until(async()=>await evaluate(`window.events?.some(e=>e.type==='ig.editor.ready')`));
    const frames=await call('Page.getFrameTree');
    const frame=frames.frameTree.childFrames[0].frame.id;
    const world=await call('Page.createIsolatedWorld',{frameId:frame,worldName:'editor-test'});
    const context=world.executionContextId;
    await until(async()=>await evaluate(`!!document.querySelector('textarea')`,context));
    assert.equal(await evaluate(`document.querySelector('textarea').value`,context),'Prepared Sandcastle scene');
    assert.equal(await evaluate(`location.hash`,context),'');
    assert.equal(await evaluate(`document.body.textContent.includes('Legacy')`,context),false);
    await evaluate(`document.querySelector('iframe').contentWindow.postMessage({type:'ig.editor.close',version:1,session_id:'wrong'},${JSON.stringify(editorOrigin)})`);
    await evaluate(`document.querySelector('.generate-button').click()`,context);
    await until(async()=>await evaluate(`window.events.some(e=>e.type==='ig.editor.completed')`));
    const events=await evaluate(`window.events`);
    const completed=events.find(event=>event.type==='ig.editor.completed');
    assert.equal(completed.correlation_id,'scene-17');
    assert.equal(completed.job_id,'job');
    assert.deepEqual(completed.execution_ids,['execution']);
    assert.deepEqual(completed.artifacts,[{artifact_id:'artifact',media_type:'image'}]);
    assert.equal(typeof completed.attempt_id,'string');
    assert.doesNotMatch(JSON.stringify(events),/opaque-browser-only|Bearer|storage_uri|private_key/);
    await evaluate(`Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Close editor').click()`,context);
    await until(async()=>await evaluate(`window.events.some(e=>e.type==='ig.editor.closed')`));
});
