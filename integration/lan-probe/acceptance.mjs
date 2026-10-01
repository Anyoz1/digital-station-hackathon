// Reproducible integration/SLA QA, not a product frontend or a simulator.
// Launches TWO independent headed system Chromium instances. Never emulates visibility.
import {chromium} from 'playwright-core';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {randomUUID,createHash} from 'node:crypto';
import {resolve} from 'node:path';

const root=resolve(import.meta.dirname,'../..'),out=resolve(root,'artifacts');
const env=Object.fromEntries((await readFile(resolve(root,'.env'),'utf8')).split('\n').filter(s=>/^[A-Z_]+=/.test(s)).map(s=>{const i=s.indexOf('=');return [s.slice(0,i),s.slice(i+1).trim().replace(/^(["'])(.*)\1$/,'$2')];}));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const save=async(name,value)=>writeFile(resolve(out,name),JSON.stringify(value,null,2)+'\n');
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const stats=values=>{const a=[...values].sort((x,y)=>x-y);return {sample_count:a.length,p50_ms:a[Math.max(0,Math.ceil(a.length*.5)-1)]??null,p95_ms:a[Math.max(0,Math.ceil(a.length*.95)-1)]??null,max_ms:a.at(-1)??null};};
await mkdir(out,{recursive:true});
const startDelay=Number(process.env.H18_START_DELAY_S??0);
if(startDelay>0){console.log(`Chromium will open after ${startDelay} seconds. Switch to workspace9 now.`);await sleep(startDelay*1000);}
// User explicitly requested workspace 9, with no focus stealing. Runtime-only
// rule, narrowly matched to our unique class; no personal config files edited.
const signatures=await readdir('/run/user/1000/hypr');
const wmEnv={...process.env,XDG_RUNTIME_DIR:'/run/user/1000',HYPRLAND_INSTANCE_SIGNATURE:process.env.HYPRLAND_INSTANCE_SIGNATURE||signatures[0]};
const wm=(...args)=>execFileSync('hyprctl',args,{env:wmEnv,encoding:'utf8'}).trim();
checkRule();
function checkRule(){
  const focus=process.env.H18_REQUIRE_FOREGROUND==='1'?'false':'true';
  const suppression=process.env.H18_REQUIRE_FOREGROUND==='1'?'':'activate activatefocus';
  const reply=wm('eval',`hl.window_rule({name="alt-h18-qa-workspace",match={class="alt-h18-sla-[01]"},workspace="9 silent",no_initial_focus=${focus},suppress_event="${suppression}"})`);
  if(reply!=='ok')throw new Error('Cannot guarantee workspace9/no-focus; refusing to open windows');
}
const activeWorkspace=()=>JSON.parse(wm('activeworkspace','-j')).id;
if(process.env.H18_REQUIRE_FOREGROUND==='1'){
  const until=Date.now()+60000;
  while(activeWorkspace()!==9&&Date.now()<until)await sleep(1000);
  if(activeWorkspace()!==9)throw new Error('Workspace9 is not active; no windows opened');
}
const contexts=await Promise.all([9224,9223].map((port,index)=>chromium.launchPersistentContext(
  `/tmp/alt-h18-qa-${port}`,{executablePath:'/usr/bin/chromium',headless:false,
    env:{...process.env,DISPLAY:':1'},viewport:{width:900,height:950},acceptDownloads:true,
    args:['--ozone-platform=x11',`--remote-debugging-port=${port}`,`--class=alt-h18-sla-${index}`,
      '--window-size=960,1040',`--window-position=${index*960},30`],
  })));
const pages=contexts.map(c=>c.pages()[0]);
const [a,b]=pages;
process.once('SIGINT',()=>{Promise.all(contexts.map(c=>c.close())).finally(()=>process.exit(130));});
const errors=[],bad=[],failures=[];
pages.forEach((p,index)=>{
  p.on('pageerror',e=>errors.push({client:index,message:e.message}));
  p.on('console',m=>{if(m.type()==='error')errors.push({client:index,message:m.text()});});
  p.on('response',r=>{if(r.status()>=400)bad.push({client:index,status:r.status(),url:r.url()});});
  p.on('requestfailed',r=>failures.push({client:index,url:r.url(),reason:r.failure()?.errorText}));
});
const check=(condition,message)=>{if(!condition)throw new Error(message);};
async function login(p,role){
  if(await p.locator('#logout').isVisible())await p.locator('#logout').click();
  await p.locator('#username').fill(role);
  await p.locator('#password').fill(env[`DEMO_${role.toUpperCase()}_PASSWORD`]);
  await p.getByRole('button',{name:'Войти',exact:true}).click();
  await p.waitForFunction(()=>window.stationState&&document.querySelector('#connection').textContent.includes('Связь'));
  return p.evaluate(()=>document.querySelector('#persona').textContent);
}
async function request(p,path,method='GET',fields){
  return p.evaluate(async({path,method,fields,id})=>{
    // The QA bot can issue commands faster than its SSE callback. Read the real
    // snapshot for each envelope, never invent or advance a local revision.
    const state=fields?await fetch('/api/v1/snapshot',{credentials:'same-origin'}).then(r=>r.json()):window.stationLiveState;
    const body=fields?{request_id:id,run_id:state.run_id,expected_input_revision:state.input_revision,...fields}:undefined;
    const r=await fetch(`/api/v1${path}`,{method,credentials:'same-origin',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    return {status:r.status,body:r.status===204?null:await r.json()};
  },{path,method,fields,id:randomUUID()});
}
async function job(p,id){
  const until=Date.now()+7000;
  while(Date.now()<until){
    const r=await request(p,`/replans/${id}`);
    check(r.status===200,'job read');
    if(!['queued','running'].includes(r.body.job.status)){
      // Await the second, measured post-publication timing frame, not only CPU completion.
      await p.waitForFunction(id=>window.stationMetrics.frames.some(f=>f.job?.id===id&&f.kind==='replan_updated'&&f.job.status!=='running'&&f.job.status!=='queued'),id,{timeout:3000});
      await sleep(80);
      return (await request(p,`/replans/${id}`)).body;
    }
    await sleep(40);
  }
  throw new Error('job deadline exceeded');
}
async function reset(scenario='demo_main_v1',seed=42){
  const old=await a.evaluate(()=>window.stationLiveState.run_id);
  const r=await request(a,'/runs','POST',{scenario_id:scenario,seed});
  check(r.status===201,`new run ${r.status}`);
  const run=r.body.result.new_run_id;
  check(old!==run,'new run id');
  await Promise.all(pages.map(p=>p.waitForFunction(run=>window.stationLiveState?.run_id===run&&window.stationLiveState?.last_replan,run)));
  const id=await a.evaluate(()=>window.stationLiveState.last_replan.id);
  await job(a,id);
  return {run_id:run,old_run_id:old};
}
async function control(action,speed){
  const r=await request(a,'/simulation/control','POST',{action,...(speed?{speed}: {})});
  check(r.status===200,`control ${action} ${r.status}`);
}
async function dumpClients(){
  return Promise.all(pages.map(p=>p.evaluate(()=>({url:location.href,ua:navigator.userAgent,visibility:document.visibilityState,
    viewport:{width:innerWidth,height:innerHeight},client_id:window.stationMetrics.render.client_id,
    frames:window.stationMetrics.frames,render:window.stationMetrics.render,state:window.stationLiveState,
    visibility_changes:window.h18VisibilityChanges??[]}))));
}
const result={started_at:new Date().toISOString(),hardware:'AMD Ryzen 7 5700U, 16 logical CPU, 14 GiB RAM',
  network:'same physical host; A real Vite proxy on 127.0.0.1:5173; B Wi-Fi LAN-interface URL 10.63.52.9:8000; not two physical machines',
  paint_proxy:'headed Chrome on user-requested workspace9; double-rAF, not hardware pixel measurement. Foreground claim only if workspace9 active throughout.',windows:[],normal:[],batches:[],flow:{}};

try{
  await Promise.all([a.goto('http://127.0.0.1:5173/tech/station'),b.goto('http://10.63.52.9:8000/tech/station')]);
  result.flow.roles=[await login(a,'admin'),await login(b,'dispatcher')];
  for(const p of pages){
    const cdp=await p.context().newCDPSession(p);
    result.windows.push(await cdp.send('Browser.getWindowForTarget'));
    await p.evaluate(()=>{window.h18VisibilityChanges=[];document.addEventListener('visibilitychange',()=>window.h18VisibilityChanges.push({wall_ms:Date.now(),visible:document.visibilityState}));});
  }
  console.log('LOGIN: two headed clients, proxy + LAN; measuring actual DOM render');
  result.workspace_proof=JSON.parse(wm('clients','-j')).filter(c=>/^alt-h18-sla-[01]$/.test(c.class)).map(c=>({address:c.address,class:c.class,workspace:c.workspace,mapped:c.mapped,hidden:c.hidden,at:c.at,size:c.size}));
  check(result.workspace_proof.length===2&&result.workspace_proof.every(c=>c.workspace.id===9),'test windows must stay on workspace9');
  for(const speed of [1,10]){
    const ids=await reset();
    await control('set_speed',speed);
    const marks=await Promise.all(pages.map(p=>p.evaluate(()=>({frames:window.stationMetrics.frames.length,reports:window.stationMetrics.render.reports.length}))));
    const started=Date.now();
    const workspaceSamples=[{wall_ms:started,id:activeWorkspace()}];
    await a.locator('#play').click();
    for(let i=0;i<8;i++){await sleep(15000);workspaceSamples.push({wall_ms:Date.now(),id:activeWorkspace()});console.log(`LIVE ${speed}x: ${Math.round((Date.now()-started)/1000)} wall seconds, workspace ${workspaceSamples.at(-1).id}`);}
    const wall=Date.now()-started;
    await control('pause');await sleep(2200);
    const metrics=(await request(a,'/metrics')).body;
    const clients=await dumpClients();
    const summary=clients.map((c,i)=>{
      const frames=c.frames.slice(marks[i].frames);
      const intervals=frames.slice(1).map((f,j)=>f.received_ms-frames[j].received_ms);
      const measurement=metrics.ui_render.find(m=>m.client_id===c.client_id);
      return {client_id:c.client_id,visibility:c.visibility,frame_count:frames.length,
        sse_hz:frames.length>1?(frames.length-1)*1000/(frames.at(-1).received_ms-frames[0].received_ms):0,
        max_frame_gap_ms:Math.max(...intervals),measurement};
    });
    const state=clients[0].state;
    result.normal.push({...ids,speed,wall_ms:wall,sim_time_s:state.sim_time_s,clients:summary,workspace_samples:workspaceSamples,
      foreground_verified:workspaceSamples.every(s=>s.id===9)&&clients.every(c=>c.visibility==='visible'&&!c.visibility_changes.some(v=>v.visible!=='visible')),
      running_operations:state.operations.filter(o=>o.status==='running').map(o=>({id:o.id,phase:o.phase,progress:o.progress})),actual:state.efficiency});
    await save(`h18-live-${speed}x-raw.json`,{metrics,clients,marks,wall_ms:wall});
    await a.screenshot({path:resolve(out,`h18-live-${speed}x.png`)});
    console.log(`MEASURED ${speed}x: `+JSON.stringify(summary));
  }
  const burst5=[
    {kind:'train_delay',target_id:'T3',duration_sim_s:600,delay_sim_s:300},
    {kind:'track_closure',target_id:'S1',duration_sim_s:600},
    {kind:'resource_loss',target_id:'I2',duration_sim_s:600},
    {kind:'destination_block',target_id:'DEST_E',duration_sim_s:600},
    {kind:'track_closure',target_id:'C2',duration_sim_s:600},
  ];
  const burst10=[...burst5,
    {kind:'train_delay',target_id:'P1',duration_sim_s:600,delay_sim_s:300},
    {kind:'track_closure',target_id:'S2',duration_sim_s:600},
    {kind:'resource_loss',target_id:'CG1',duration_sim_s:600},
    {kind:'destination_block',target_id:'DEST_W',duration_sim_s:600},
    {kind:'train_delay',target_id:'T6',duration_sim_s:600,delay_sim_s:300},
  ];
  for(const items of [burst5,burst10]){
    const ids=await reset();await control('set_speed',10);await control('play');await sleep(1200);
    const r=await request(a,'/incidents','POST',{items});check(r.status===201,'incident');
    await a.waitForFunction(n=>window.stationState.incidents.length===n,items.length);
    await a.screenshot({path:resolve(out,`h18-batch${items.length}-conflicts.png`)});
    const detail=await job(a,r.body.result.replan_id);
    await a.waitForFunction(id=>window.stationState.active_plan_id===id,detail.job.applied_plan_id);
    await a.waitForFunction(()=>window.stationDetail?.plans.length>=2);
    await sleep(2200);
    const clients=await dumpClients(),metrics=(await request(a,'/metrics')).body;
    const phases=clients[0].frames.filter(f=>f.job?.id===detail.job.id);
    result.batches.push({...ids,count:items.length,incident_ids:r.body.result.incident_ids,job:detail.job,
      feasible_alternatives:detail.plans.filter(p=>p.validator.passed&&p.validity==='feasible').length,
      conflicts_seen:phases.some(f=>f.conflicts.length>0),states:phases.map(f=>({seq:f.seq,kind:f.kind,status:f.job.status,conflicts:f.conflicts.length})),metrics});
    await save(`h18-batch${items.length}-raw.json`,{receipt:r.body,detail,clients,metrics});
    await a.screenshot({path:resolve(out,`h18-batch${items.length}-applied.png`)});
    console.log(`BATCH ${items.length}: ${detail.job.status}, ${detail.plans.length} alternatives, ${detail.job.elapsed_ms.toFixed(1)}ms`);
    await control('pause');
  }
  // Read-only replay and genuine browser CSV download.
  const live=await a.evaluate(()=>window.stationLiveState);
  await a.locator('#history-open').click();
  await a.waitForFunction(()=>window.stationHistory?.selected_seq>0);
  result.flow.replay={run_id:live.run_id,live_seq:live.event_seq,replay_seq:await a.evaluate(()=>window.stationHistory.selected_seq),
    live_unchanged:await a.evaluate(seq=>window.stationLiveState.event_seq===seq,live.event_seq)};
  await a.screenshot({path:resolve(out,'h18-replay.png')});
  await a.locator('#return-live').click();
  check(await a.evaluate(()=>window.stationState.event_seq===window.stationLiveState.event_seq),'return live');
  const download=await Promise.all([a.waitForEvent('download'),a.locator('#csv-download').click()]);
  await download[0].saveAs(resolve(out,'h18-browser-report.csv'));
  const csv=await readFile(resolve(out,'h18-browser-report.csv'),'utf8');
  check(csv.includes('efficiency-v1')&&csv.includes('sim_seconds'),'CSV raw/formula/units');
  result.flow.csv={bytes:Buffer.byteLength(csv),lines:csv.split('\n').length,formula_version:'efficiency-v1',real_download:true};
  // Outgoing-network fault injection only. Chromium may keep an already-open
  // SSE socket alive under setOffline; do NOT claim disconnect unless frames stop.
  // The separate real PostgreSQL/API restart proof covers silent SSE + recovery.
  const disconnectFrames=await b.evaluate(()=>window.stationMetrics.frames.length);
  await b.context().setOffline(true);await sleep(11000);
  result.flow.offline=await b.locator('#connection').innerText();
  result.flow.offline_sse_stopped=(await b.evaluate(()=>window.stationMetrics.frames.length))===disconnectFrames;
  await b.context().setOffline(false);
  await b.waitForFunction(()=>document.querySelector('#connection').textContent.includes('Связь'));
  result.flow.reconnected=await b.evaluate(()=>({run_id:window.stationState.run_id,seq:window.stationState.event_seq}));
  console.log('FLOW: live, incidents, alternatives/autoapply, actual KPI, replay, CSV passed; outgoing-network fault tested (hard SSE disconnect requires separate restart proof)');
  const timings=[];
  const cases={delay:[burst5[0]],closure:[burst5[1]],resource_loss:[burst5[2]],burst10};
  for(const [name,items] of Object.entries(cases)){
    for(let i=0;i<20;i++){
      const ids=await reset();await control('set_speed',10);await control('play');await sleep(120);
      const r=await request(a,'/incidents','POST',{items});check(r.status===201,`repeat ${name}`);
      const detail=await job(a,r.body.result.replan_id);
      timings.push({...ids,case:name,repeat:i+1,job:detail.job,validated_candidates:detail.plans.filter(p=>p.validator.passed).length});
      await control('pause');
    }
    await save('h18-replan-timings-raw.json',timings);
    console.log(`REPEAT ${name}: `+JSON.stringify(stats(timings.filter(t=>t.case===name).map(t=>t.job.elapsed_ms))));
  }
  result.replan={elapsed:stats(timings.map(t=>t.job.elapsed_ms)),compute:stats(timings.map(t=>t.job.compute_ms)),
    statuses:timings.reduce((all,t)=>{all[t.job.status]=(all[t.job.status]??0)+1;return all;},{}),
    deadline_exceedances:timings.filter(t=>t.job.elapsed_ms>5000).length,
    cases:Object.fromEntries(Object.keys(cases).map(c=>[c,stats(timings.filter(t=>t.case===c).map(t=>t.job.elapsed_ms))]))};
  // Config's real mutation/version + automatic replan; preserve weights/durations/topology.
  const cfg=(await request(a,'/config')).body;
  const update=await request(a,'/config','PATCH',{patch:{category_thresholds:cfg.category_thresholds}});
  check(update.status===200,'admin config patch');await job(a,update.body.result.replan_id);
  result.flow.config={before:cfg.config_version,after:update.body.result.config_version};
  const expectedAt=bad.length,errorsAt=errors.length;
  await login(a,'viewer');
  result.flow.viewer={new_run:(await request(a,'/runs','POST',{scenario_id:'demo_main_v1',seed:42})).status,
    config_patch:(await request(a,'/config','PATCH',{patch:{category_thresholds:cfg.category_thresholds}})).status,
    control:(await request(a,'/simulation/control','POST',{action:'play'})).status};
  check(Object.values(result.flow.viewer).every(s=>s===403),'viewer permissions');
  result.expected_denials=bad.slice(expectedAt);bad.splice(expectedAt);errors.splice(errorsAt);
  await login(a,'admin');
  const previous=await a.evaluate(()=>window.stationLiveState.run_id);
  const newIds=await reset('manual-control-v1');
  result.flow.new_run={...newIds,old_history_status:(await request(a,`/history?run_id=${previous}`)).status};
  await control('set_speed',10);await control('play');
  console.log('MANUAL: waiting for real minimum operation duration (36 wall seconds)');
  await a.waitForFunction(()=>window.stationLiveState.operations.some(o=>o.id==='op-T1-inspection'&&o.can_complete),null,{timeout:50000});
  await control('pause');
  await login(b,'operator');
  const foreignAt=bad.length,foreignErrors=errors.length;
  result.flow.manual={foreign_status:(await request(b,'/operations/op-T2-inspection/complete','POST',{})).status,
    before:await b.evaluate(()=>window.stationLiveState.operations.find(o=>o.id==='op-T1-inspection'))};
  check(result.flow.manual.foreign_status===403,'foreign manual403');
  result.expected_denials.push(...bad.slice(foreignAt));bad.splice(foreignAt);errors.splice(foreignErrors);
  await b.locator('[data-command="complete"][data-operation-id="op-T1-inspection"]').click();
  await b.waitForFunction(()=>window.stationLiveState.operations.find(o=>o.id==='op-T1-inspection').status==='completed');
  result.flow.manual.after=await b.evaluate(()=>window.stationLiveState.operations.find(o=>o.id==='op-T1-inspection'));
  await b.screenshot({path:resolve(out,'h18-manual-operator.png')});
  const id=await b.evaluate(()=>window.stationLiveState.last_replan.id);await job(b,id);
  await control('play');await b.waitForFunction(()=>window.stationLiveState.operations.find(o=>o.id==='op-T1-departure').actual_start_sim_s!=null);await control('pause');
  result.flow.manual.dependent_started=await b.evaluate(()=>window.stationLiveState.operations.find(o=>o.id==='op-T1-departure'));
  await login(b,'dispatcher');
  const finalIds=await reset();
  const final=(await request(a,'/snapshot')).body;
  const historical=(await request(a,`/history/snapshot?run_id=${final.run_id}&seq=${final.event_seq}`)).body;
  result.restart_anchor={...finalIds,seq:historical.event_seq,hash:hash(historical),config_version:historical.config_version,scenario_id:historical.scenario_id};
  await save('h18-restart-anchor.json',result.restart_anchor);
  await a.screenshot({path:resolve(out,'h18-final-ready.png')});
  result.finished_at=new Date().toISOString();result.console_page_errors=errors;result.bad_responses=bad;
  result.request_failures=failures;
  await save('h18-acceptance.json',result);
  console.log('ACCEPTANCE SAVED: '+JSON.stringify({normal:result.normal.map(r=>({speed:r.speed,clients:r.clients})),replan:result.replan,flow:result.flow,errors,bad}));
}catch(error){
  result.failure={message:error.message,stack:error.stack};result.console_page_errors=errors;result.bad_responses=bad;result.request_failures=failures;
  await save('h18-acceptance-partial.json',result);console.error('ACCEPTANCE FAILURE: '+error.message);process.exitCode=1;
}finally{
  // These are our explicitly launched QA instances, never the user's browser.
  for(const context of contexts)await context.close();
}
