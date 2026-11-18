import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { sourceURL } from "./import-source.mjs";
const { getModelGovernance, saveModelGovernance } = await import(sourceURL("api/tenantSettings.ts"));
const { ProviderModelForm } = await import(sourceURL("components/ProviderModelSettings.tsx"));
const { eligibleModels, resolveLabForm, buildMediaRequest, INITIAL_FORM } = await import(sourceURL("lib/generation.ts"));
const model=(id, extra={})=>({id,display_name:`Display ${id}`,known:true,supported:true,enabled:true,available:true,usable:true,
    lifecycle:{deprecated:false,sunset_at:null,replacement_model:null},technical:null,
    supported_interface_levels:['easy','medium','hard'],selectable_in:['hard'],use_cases:[],capabilities:{
        text_to_media:true,aspect_ratios:['1:1'],resolutions:[],count:{min:1,max:1},duration_seconds:null,audio:false,
        reference_roles:[],max_references:0,reference_strengths:[],layout_types:['single'],quality_profiles:['standard'],styles:['auto']},...extra});
const provider={id:'studio-graphics',display_name:'Studio graphics',enabled:true,has_credentials:true};
const media={media_type:'image',restricted:true,default_model:'canvas-b',default_override:'canvas-b',configuration_error:null,
    models:[model('canvas-a',{enabled:false,available:false,usable:false}),model('canvas-b')]};
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
function fakeFetch(t,handler){const previous=globalThis.fetch;t.after(()=>globalThis.fetch=previous);globalThis.fetch=handler;}

function capabilities(){return {schema_version:1,interface_levels:Object.fromEntries(['easy','medium','hard'].map(level=>[level,{explicit_provider_selection:level==='hard',explicit_model_selection:level==='hard'}])),
    media_types:[{id:'image',available:true,default_provider:provider.id,purposes:['general'],providers:[{...provider,configured:true,available:true,default_model:'canvas-b',default_model_available:true,models:media.models}]}]};}

test('generation choices filter disabled/unusable models and preserve discovered IDs',()=>{
    const caps=capabilities();
    assert.deepEqual(eligibleModels(caps.media_types[0],'hard').map(model=>model.id),['canvas-b']);
    const form={...INITIAL_FORM,mode:'hard',provider:provider.id,model:'canvas-b',prompt:'A lake'};
    assert.deepEqual(resolveLabForm(caps,form).models.map(model=>model.id),['canvas-b']);
    assert.equal(buildMediaRequest(caps,form).advanced.model,'canvas-b');
    caps.media_types[0].providers[0].models[1].usable=false;
    assert.equal(eligibleModels(caps.media_types[0],'hard').length,0);
});

test('tenant form renders catalog labels, checkbox state and dynamic defaults without provider constants',()=>{
    const html=renderToStaticMarkup(React.createElement(ProviderModelForm,{provider,media,onSaved:()=>{}}));
    assert.match(html,/Display canvas-a/);assert.match(html,/Display canvas-b/);
    assert.match(html,/<option value="canvas-a" disabled="">/);
    assert.match(html,/<option value="canvas-b" selected="">/);
    assert.doesNotMatch(html,/gpt-image|OpenAI|api_key|refresh_token/);
});

test('missing credentials disable model edits and deprecation metadata is shown',()=>{
    const value={...media,models:[model('canvas-b',{lifecycle:{deprecated:true,sunset_at:'2099-01-01',replacement_model:'canvas-c'}})]};
    const html=renderToStaticMarkup(React.createElement(ProviderModelForm,{provider:{...provider,has_credentials:false},media:value,onSaved:()=>{}}));
    assert.match(html,/credentials are not configured/);assert.match(html,/<fieldset disabled=""/);assert.match(html,/Deprecated.*2099-01-01/);
});

test('admin policy read and save use narrow existing provider identity and authoritative document',async t=>{
    const expected={providers:[{...provider,media:[media]}]};
    fakeFetch(t,async(url,options={})=>{
        assert.equal(options.credentials,'same-origin');
        if(options.method==='PATCH'){
            assert.equal(url,'/v2/tenant/model-governance/studio-graphics');
            assert.deepEqual(JSON.parse(options.body),{media_type:'image',enabled_models:['canvas-b'],default_model:'canvas-b'});
            assert.doesNotMatch(options.body,/credentials|config|api_key/);
        }else assert.equal(url,'/v2/tenant/model-governance');
        return response(expected);
    });
    assert.deepEqual(await getModelGovernance(),expected);
    assert.deepEqual(await saveModelGovernance(provider.id,'image',['canvas-b'],'canvas-b'),expected);
});

test('clearing an override is explicit and preserves unrestricted semantics',async t=>{
    fakeFetch(t,async(url,options)=>{
        assert.deepEqual(JSON.parse(options.body),{media_type:'image',enabled_models:null,default_model:null});
        return response({providers:[]});
    });
    await saveModelGovernance(provider.id,'image',null,null);
});
