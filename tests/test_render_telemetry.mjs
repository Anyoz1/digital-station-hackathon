import test from 'node:test';
import assert from 'node:assert/strict';
import {createRenderTelemetry} from '../src/digital_station/static/render-telemetry.js';

const stored=new Map();
globalThis.sessionStorage={getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)};
globalThis.document={visibilityState:'visible'};
const frames=[];
globalThis.requestAnimationFrame=callback=>{frames.push(callback);};
const defer=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const clock=()=>({server_received_ms:Date.now(),server_sent_ms:Date.now()});

test('stop drains pending calibration; a logged-out generation cannot schedule more probes',async()=>{
  const first=defer(),calls=[];
  const meter=createRenderTelemetry(path=>{calls.push(path);return first.promise;},()=> 'test-client');
  const started=meter.start(),stopped=meter.stop();
  first.resolve(clock());await Promise.all([started,stopped]);
  assert.deepEqual(calls,['/api/v1/time']);
  meter.rendered({run_id:'r',event_seq:1},{kind:'tick'},Date.now(),true);
  assert.equal(frames.length,0);
});

test('real double-rAF report is deduplicated and drained before session revocation',async()=>{
  const report=defer(),calls=[];
  const meter=createRenderTelemetry(async(path,options)=>{
    calls.push({path,options});return path.endsWith('/time')?clock():report.promise;
  },()=> 'test-client');
  await meter.start();
  assert.equal(calls.length,5);
  const state={run_id:'r',event_seq:1},cause={kind:'tick',ingested_at:new Date().toISOString()};
  meter.rendered(state,cause,Date.now(),true);meter.rendered(state,cause,Date.now(),true);
  assert.equal(frames.length,1);frames.shift()();assert.equal(frames.length,1);frames.shift()();
  assert.equal(calls.length,6);
  let drained=false;const stopped=meter.stop().then(()=>{drained=true;});
  await Promise.resolve();assert.equal(drained,false);
  report.resolve(null);await stopped;
  assert.equal(meter.diagnostic.reports[0].accepted,true);
  assert.equal(meter.diagnostic.errors,0);
  meter.rendered({...state,event_seq:2},cause,Date.now(),true);assert.equal(frames.length,0);
});
