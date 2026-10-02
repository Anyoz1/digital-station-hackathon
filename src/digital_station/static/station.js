import {label, operationCaption, displayTime, locationLabel, renderStation, affectedIds, DISPATCHER_NOTE} from './railway.js';
import {createRenderTelemetry} from './render-telemetry.js';

// This page only renders authoritative REST/SSE data. It has no railway executor.
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const n=document.createElement(tag); if(text!=null)n.textContent=String(text); if(cls)n.className=cls; return n; };
let state=null, user=null, stream=null, retryTimer=null, retries=0, lastReceived=0, connection='OFFLINE', pending=false;
let selected=null, detail=null, explanation=null, detailKey=null, generation=0;
let liveState=null, replayMode=false, replayGeneration=0, replayTimer=null, historyWindow=null, historyItems=[];
const events=[], observed=new Set();
const FACTOR_NAMES={throughput:'Выполнение отправлений',delay:'Среднее опоздание',occupancy:'Физическая загрузка путей',conflicts:'Нерешённые конфликты',resource_idle:'Простой при готовой работе'};
window.stationMetrics={frames:[]};
const renderTelemetry=createRenderTelemetry(api,uuid);
window.stationMetrics.render=renderTelemetry.diagnostic;
const clock = s => s==null?'—':displayTime(state,s).split(' · ')[0];
const minutes = s => s==null?'—':`${(s/60).toFixed(1)} мин`;
const destination = id => ({DEST_W:'Следующая станция (запад)',DEST_E:'Следующая станция (восток)'})[id] ?? id;
function showError(error) {
  $('error').hidden=false; $('error').replaceChildren(node('div',error.body?.error?.message ?? error.message));
  const more=node('details');more.append(node('summary','Технические подробности'),node('pre',JSON.stringify(error.body??{},null,2)));$('error').append(more);
}
function clearError(){ $('error').hidden=true; }
async function api(path,options={}) {
  const response=await fetch(path,{credentials:'same-origin',...options});
  const body=response.status===204?null:await response.json();
  if(!response.ok){ const err=new Error(body?.error?.message??`Ошибка запроса ${response.status}`);err.status=response.status;err.body=body;throw err; }
  return body;
}
function connectedWrite(){return !!(user&&state&&!replayMode&&connection==='LIVE'&&performance.now()-lastReceived<3000&&!pending);}
function writable(){ return connectedWrite()&&['dispatcher','admin'].includes(user.role); }
function canConfirm(op){return connectedWrite()&&op.can_complete&&(user.role==='admin'||user.role==='operator'&&op.assigned_user_id===user.id);}
function controls(){
  for(const id of ['play','pause','set-speed','replan','create-incident'])$(id).disabled=!writable();
  $('play').disabled ||= state?.mode==='running';$('pause').disabled ||= state?.mode==='paused';
  document.querySelectorAll('[data-command]').forEach(b=>{const op=state?.operations.find(o=>o.id===b.dataset.operationId);b.disabled=b.dataset.command==='complete'?!(op&&canConfirm(op)):!writable()||(b.dataset.command==='apply'&&b.dataset.canApply!=='true');});
  $('save-config').disabled=!(connectedWrite()&&user.role==='admin');
  $('new-run').disabled=!(connectedWrite()&&user.role==='admin');
  $('history-open').disabled=!user||!liveState||pending;
  $('csv-download').disabled=!user||!state;
}
function auth(value){user=value;if(!value)renderTelemetry.stop();$('persona').textContent=user?label('role',user.role):'Вход не выполнен';$('persona').title=DISPATCHER_NOTE;$('login-form').hidden=!!user;$('logout').hidden=!user;$('workspace').hidden=!user;$('save-config').hidden=user?.role!=='admin';$('new-run-controls').hidden=user?.role!=='admin';$('config-json').readOnly=user?.role!=='admin';controls();}
function status(){
  const age=lastReceived?performance.now()-lastReceived:Infinity;
  const text=!user?'Нет связи':lastReceived&&age>10000?'Нет связи':lastReceived&&age>3000?'Данные устарели':({LIVE:'● Связь есть',CONNECTING:'Подключение…',RECONNECTING:'Восстановление связи…'})[connection]??'Нет связи';
  $('connection').textContent=text;$('workspace').classList.toggle('stale',!!user&&(connection!=='LIVE'||age>3000));controls();
  // A reverse proxy may leave a half-open SSE response after upstream restart.
  // Heartbeats are <=1s; no frame for3s invalidates transport, not domain State.
  if(user&&stream&&connection==='LIVE'&&age>3000&&!pending)retryConnection(generation);
}
function select(id){selected=id;renderAll();}
function button(text,fn){const b=node('button',text);b.type='button';b.addEventListener('click',fn);return b;}
function latestDeparture(train){
  const active=explanation?.plans.find(p=>p.plan_id===state.active_plan_id);
  return active?.departures.find(d=>d.train_id===train.id);
}
function renderTrains(){
  const target=$('train-list');target.replaceChildren();const affected=affectedIds(state);
  state.trains.forEach(t=>{
    const card=node('article',null,`train-card ${selected===t.id?'selected':''} ${affected.has(t.id)?'affected':''}`);card.dataset.trainId=t.id;card.tabIndex=0;card.addEventListener('click',()=>select(t.id));card.addEventListener('keydown',e=>{if(e.key==='Enter')select(t.id);});
    const top=node('div',null,'train-title');top.append(node('strong',`${t.number} / ${t.id}`),node('span',label('trainType',t.type),`tag ${t.type==='PASSENGER'?'passenger':''}`));card.append(top);
    const profile=node('p',label('profile',t.service_profile_id),'subtle');profile.title=t.service_profile_id;card.append(profile,node('p',`${label('trainStatus',t.status)} · ${locationLabel(t.location,state)}`,'train-position'));
    const dep=state.operations.find(o=>o.train_id===t.id&&o.kind==='departure');const delay=latestDeparture(t);
    const grid=node('div',null,'train-grid');
    for(const line of [label('direction',t.direction),`Приоритет: ${label('priority',t.priority)}`,`ETA входа: ${clock(t.expected_arrival_sim_s)}`,`Срок отправления: ${clock(t.due_departure_sim_s)}`,`План отправления: ${clock(dep?.end_sim_s)}`,`Назначенный путь: ${t.planned_track_id??'—'}`,`Задержка (${delay?.basis==='actual'?'факт':'прогноз'}): ${delay?minutes(delay.delay_sim_s):'нет актуального прогноза'}`,destination(t.destination_id)])grid.append(node('span',line));card.append(grid);
    const op=state.operations.find(o=>o.train_id===t.id&&o.status==='running');card.append(node('p',op?`${label('operationKind',op.kind)}${op.phase?` · ${label('phase',op.phase)}`:''}`:t.status==='departed'?'Работа завершена':'Ожидание следующей операции','train-operation'));target.append(card);
  });
}
function renderSelection(){
  const target=$('selection');target.replaceChildren();
  if(!selected){target.textContent='Выберите поезд, путь, группу или локомотив на схеме.';return;}
  const obj=[...state.tracks,...state.trains,...state.wagon_groups,...state.resources,...state.operations,...state.zones].find(o=>o.id===selected);
  if(!obj){target.textContent='Объект больше не представлен в этом состоянии.';return;}
  let text=selected;
  if(state.tracks.includes(obj))text+=` · ${label('trackKind',obj.kind)} · факт ${obj.occupied_length_m}/${obj.length_m} м · ${label('availability',obj.availability)} · назначен ${obj.assigned_train_id??'никому'} · блокируют ${obj.active_operation_ids.map(id=>state.operations.find(o=>o.id===id)?.train_id??id).join(', ')||'нет операций'}`;
  else if(state.operations.includes(obj))text+=` · ${label('operationKind',obj.kind)} · ${label('operationStatus',obj.status)} · ${obj.source_track_id??'граница'} → ${obj.target_track_id??'граница'} · ресурсы ${obj.resource_ids.join(', ')} · ${clock(obj.start_sim_s)}–${clock(obj.end_sim_s)}`;
  else if(state.zones.includes(obj))text+=` · Горловина · ${obj.active_operation_id?'занята операцией '+obj.active_operation_id:'не занята операцией'}`;
  else text+=` · ${locationLabel(obj.location,state)}${obj.wagon_count?` · ${obj.wagon_count} вагонов · ${label('cargoState',obj.cargo_state)}`:''}${obj.group_ids?` · группы ${obj.group_ids.join(', ')||'неделимый состав'}`:''}`;
  target.append(node('strong',text));
}
function renderOperations(){
  renderTimeline();
  $('operations').replaceChildren();
  state.trains.forEach(t=>{
    const row=node('div',null,'ops-row');row.append(button(t.id,()=>select(t.id)));const chain=node('div',null,'chain');
    state.operations.filter(o=>o.train_id===t.id).forEach(o=>{
      const card=node('div',null,`op ${o.status} ${selected===o.id?'selected':''}`);card.dataset.operationId=o.id;card.title=`${o.id}\n${o.source_track_id??'граница'} → ${o.target_track_id??'граница'}\n${o.resource_ids.join(', ')}`;card.tabIndex=0;card.addEventListener('click',()=>select(o.id));card.addEventListener('keydown',e=>{if(e.key==='Enter')select(o.id);});
      card.append(node('strong',operationCaption(o,state)),node('span',label('operationStatus',o.status)),node('span',`${clock(o.start_sim_s)}–${clock(o.end_sim_s)}`));
      if(o.status==='running'){const p=node('progress');p.max=1;p.value=o.progress;card.append(p,node('span',`${Math.round(o.progress*100)}%`));}chain.append(card);
      if(o.execution_mode==='manual'&&o.status!=='completed'){
        card.append(node('span',`Ручное подтверждение · ${o.assigned_user_id??'не назначен'} · минимум до ${o.actual_start_sim_s==null?'начала операции':clock(o.actual_start_sim_s+o.duration_sim_s)}`));
        if(user?.role==='admin'||user?.role==='operator'&&o.assigned_user_id===user.id){const complete=button('Подтвердить завершение',()=>command(`/api/v1/operations/${encodeURIComponent(o.id)}/complete`,{},'POST','complete'));complete.className='manual-complete';complete.dataset.command='complete';complete.dataset.operationId=o.id;card.append(complete);}
      }
    });row.append(chain);$('operations').append(row);
  });
  $('shunting').replaceChildren();const running=state.operations.filter(o=>o.kind==='shunt_transfer'&&o.status==='running');
  if(!running.length)$('shunting').append(node('p','Маневровая перестановка сейчас не выполняется.','subtle'));
  running.forEach(o=>{
    const card=node('div',null,'shunt-card');card.append(node('strong',`${o.train_id} · ${o.group_ids.join(', ')} · ${o.source_track_id} → H → ${o.target_track_id}`),node('p',`${label('phase',o.phase)}${o.phase==='push_to_target'?` ${o.target_track_id}`:''}`),node('p',`L1: ${locationLabel(state.resources.find(r=>r.id==='L1').location,state)} · Бригада: ${o.resource_ids.filter(id=>id!=='L1').join(', ')} · W: ${state.zones.find(z=>z.id==='W').active_operation_id?'занята':'свободна'}`));
    const phases=node('div',null,'phase-strip');['empty_to_source','couple','pull_to_lead','reverse','push_to_target','uncouple','return_to_depot'].forEach(p=>phases.append(node('span',label('phase',p),p===o.phase?'current':'')));card.append(phases);$('shunting').append(card);
  });
}
function renderTimeline(){
  const ns='http://www.w3.org/2000/svg',svg=$('operation-timeline');svg.replaceChildren();
  const element=(tag,attrs,text)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!=null)n.textContent=text;return n;};
  const end=Math.max(1,state.sim_time_s,...state.operations.map(o=>o.end_sim_s??0));
  const x=s=>65+920*s/end;
  svg.setAttribute('viewBox',`0 0 1000 ${state.trains.length*30+45}`);
  for(let i=0;i<=6;i++){const t=end*i/6;svg.append(element('line',{x1:x(t),x2:x(t),y1:25,y2:state.trains.length*30+33,stroke:'#dbe4ed'}),element('text',{x:x(t),y:15,'text-anchor':i===6?'end':'middle'},clock(t)));}
  state.trains.forEach((train,index)=>{
    const y=32+index*30;svg.append(element('text',{x:5,y:y+15},train.id));
    state.operations.filter(o=>o.train_id===train.id).forEach(o=>{
      const from=o.actual_start_sim_s??o.start_sim_s,to=o.actual_end_sim_s??o.end_sim_s;if(from==null||to==null)return;
      const bar=element('rect',{x:x(from),y,width:Math.max(2,x(to)-x(from)),height:20,rx:3,class:`timeline-op ${o.status}`,'data-operation-id':o.id,tabindex:0});
      bar.append(element('title',{},`${train.id} · ${operationCaption(o,state)} · ${label('operationStatus',o.status)}\n${clock(from)}–${clock(to)}${o.execution_mode==='manual'?' · минимальное время, фактический конец подтверждается исполнителем':''}`));
      bar.addEventListener('click',()=>select(o.id));bar.addEventListener('keydown',e=>{if(e.key==='Enter')select(o.id);});svg.append(bar);
    });
  });
  svg.append(element('line',{x1:x(state.sim_time_s),x2:x(state.sim_time_s),y1:22,y2:state.trains.length*30+36,stroke:'#b93824','stroke-width':2}));
}
function renderResources(){
  $('resources').replaceChildren();$('train-resources').replaceChildren();const affected=affectedIds(state);
  state.resources.forEach(r=>{
    const box=node('div',null,`resource ${affected.has(r.id)?'affected':''}`);box.dataset.resourceId=r.id;const a=node('div');a.append(node('strong',`${r.id} · ${label('resourceKind',r.kind)}`));if(r.location)a.append(node('div',locationLabel(r.location,state),'detail'));
    const b=node('div');b.append(node('span',label('resourceStatus',r.status),r.status));const op=state.operations.find(o=>o.id===r.active_operation_id);if(op)b.append(node('div',`${op.train_id} · ${label('operationKind',op.kind)}`,'detail'));box.append(a,b);(['train_locomotive','self_propelled_unit','train_crew'].includes(r.kind)?$('train-resources'):$('resources')).append(box);
  });
}
function incidentText(i){
  const until=clock(i.ends_sim_s);const pending=i.status==='pending'?` · после завершения текущей операции (${clock(i.starts_sim_s)})`:'';
  return ({track_closure:`Путь ${i.target_id} ${i.status==='pending'?'закроется':'закрыт'} до ${until}`,resource_loss:`Ресурс ${i.target_id} ${i.status==='pending'?'станет недоступен':'недоступен'} до ${until}`,train_delay:`Поезд ${i.target_id}: ETA сдвинуто на ${minutes(i.delay_sim_s)}`,destination_block:`${destination(i.target_id)} не принимает до ${until}. Проверяется прибытие после учебного перегона, а не момент отправления.`})[i.kind]+pending;
}
function renderConflicts(){
  $('conflicts').replaceChildren();$('incidents').replaceChildren();
  if(!state.conflicts.length)$('conflicts').append(node('p','Нерешённых конфликтов действующего плана нет.','subtle'));
  state.conflicts.forEach(c=>{
    const related=state.incidents.find(i=>c.entity_ids.includes(i.id));const box=node('div',null,'conflict');box.dataset.conflictId=c.id;
    const affected=[...new Set(c.operation_ids.map(id=>state.operations.find(o=>o.id===id)?.train_id).filter(Boolean))];
    box.append(node('strong',label('conflictKind',c.kind)),node('p',related?incidentText(related):c.message),node('p',`Затронуты поезда: ${affected.join(', ')||'см. объекты'}. Требуется новый допустимый план.`));$('conflicts').append(box);
  });
  state.incidents.filter(i=>i.status!=='resolved').forEach(i=>{
    const box=node('div',null,'incident');box.dataset.incidentId=i.id;box.append(node('strong',`${label('incidentStatus',i.status)} · ${incidentText(i)}`),node('p',`Затронуто операций: ${i.affected_operation_ids.length}`));
    const b=button('Снять ограничение',()=>command(`/api/v1/incidents/${encodeURIComponent(i.id)}/resolve`,{}));b.dataset.command='resolve';box.append(b);$('incidents').append(box);
  });
}
function renderPlans(){
  const job=state.last_replan;$('replan-status').textContent=job?`${label('replanStatus',job.status)} · ${(job.elapsed_ms/1000).toFixed(2)} с`:'Расчётов пока нет';$('plans').replaceChildren();
  if(replayMode){state.plans.forEach(p=>{const card=node('article',null,'plan');card.append(node('strong',`${label('planStatus',p.status)} · ${p.strategy}`),node('p',`Сохранённый прогноз: ${p.forecast.score??'нет данных'} / 100 · J ${p.objective_value??'—'}`));$('plans').append(card);});$('plans').append(node('p','Историческое состояние. Применение планов отключено.'));return;}
  if(!detail||detail.job.id!==job?.id){$('plans').textContent='Ожидаем детали текущего расчёта…';return;}
  const current=new Map(state.plans.map(p=>[p.id,p]));
  if(explanation?.available===false)$('plans').append(node('p',explanation.reason,'subtle'));
  detail.plans.forEach((saved,index)=>{
    const p={...saved,...current.get(saved.id)};const exp=explanation?.plans.find(x=>x.plan_id===p.id);const valid=p.validity==='feasible'&&saved.validator.passed;
    const card=node('article',null,`plan ${p.id===state.active_plan_id?'active':''}`);card.dataset.planId=p.id;
    card.append(node('h3',`Вариант ${String.fromCharCode(65+index)}${p.id===job.applied_plan_id?' · рекомендован расчётом':''}`),node('span',`${label('planStatus',p.status)} · ${valid?'✓ Независимая проверка пройдена':'⚠ Нет актуального подтверждения допустимости'}`,'tag'));
    const metrics=node('div',null,'plan-metrics');for(const [value,name] of [[valid?p.forecast.score??'—':'—','Прогноз индекса / 100'],[p.objective_value?.toFixed(3)??'—','Общая цель J · меньше лучше'],[valid?p.forecast.factors.find(f=>f.key==='conflicts')?.raw??'—':'—','Конфликты прогноза']]){const item=node('div',null,'metric');item.append(node('strong',value),node('span',name));metrics.append(item);}card.append(metrics);
    const order=saved.operations.filter(o=>o.kind==='arrival'&&o.train_id==='P1'||o.kind==='shunt_transfer'&&o.train_id==='T2'&&o.id==='op-T2-shunt-out');
    const list=node('ul');order.forEach(o=>list.append(node('li',`${o.train_id} · ${label('operationKind',o.kind)}: ${clock(o.start_sim_s)}–${clock(o.end_sim_s)}`)));card.append(list);
    if(exp){card.append(node('p',exp.reason,'reason'));const changes=node('details');changes.open=true;changes.append(node('summary',`Изменения относительно плана до расчёта: ${exp.changes.length}`));const lines=node('ul');exp.changes.forEach(c=>lines.append(node('li',`${c.train_id} · ${c.label}: ${clock(c.before_start_sim_s)} → ${clock(c.after_start_sim_s)}${c.shift_sim_s==null?'':` (${c.shift_sim_s>=0?'+':''}${minutes(c.shift_sim_s)})`}; путь ${c.before_track_id??'—'} → ${c.after_track_id??'—'}; ресурсы ${c.before_resource_ids.join(', ')} → ${c.after_resource_ids.join(', ')}`)));changes.append(lines);card.append(changes);}
    const factors=node('details');factors.append(node('summary','Факторы прогнозного индекса'));const table=node('table');const factorNames=FACTOR_NAMES;
    p.forecast.factors.forEach(f=>{const tr=node('tr');tr.append(node('td',factorNames[f.key]),node('td',`${f.raw==null?'—':f.raw.toFixed(3)} ${f.unit==='sim_seconds'?'с':f.unit==='ratio'?'доля':'шт.'}`),node('td',`−${f.contribution?.toFixed(1)??'—'} п.`));table.append(tr);});factors.append(table);card.append(factors,node('p',`Окно прогноза: ${clock(p.forecast.window_start_sim_s)}–${clock(p.forecast.window_end_sim_s)}. Фактический индекс показан отдельно.`,'fact-note'));
    const apply=button('Применить вариант',()=>command(`/api/v1/plans/${encodeURIComponent(p.id)}/apply`,{}));apply.dataset.command='apply';apply.dataset.canApply=String(p.can_apply&&state.mode==='paused');card.append(apply);if(!p.can_apply)card.append(node('p','Для ручного выбора: пауза → новый расчёт → актуальная альтернатива.','fact-note'));$('plans').append(card);
  });
}
function renderAll(){
  if(!state)return;
  $('mode').textContent=replayMode?'ИСТОРИЯ · ПРОСМОТР':state.mode==='running'?'LIVE · РАБОТАЕТ':'ПАУЗА';$('sim-clock').textContent=clock(state.sim_time_s);$('sim-clock').title=displayTime(state);$('speed-label').textContent=`${state.speed}× · учебное время`;
  $('return-live').hidden=!replayMode;$('view-note').textContent=replayMode?'Сохранённое состояние; LIVE обновляется в фоне':'Текущее состояние станции';renderActual();
  const active=state.incidents.filter(i=>i.status!=='resolved').length;const job=state.last_replan;const planning=['queued','running'].includes(job?.status);
  $('alert-strip').className=`alert-strip ${active||state.conflicts.length?'warning':''}`;
  $('alert-strip').textContent=planning?'Перепланирование: новые операции ожидают проверенный план; уже начатые продолжаются.':`${active?`Действует ограничений: ${active}. `:''}Нерешённых конфликтов: ${state.conflicts.length}. ${job?.applied_plan_id===state.active_plan_id?'Проверенный план применён.':job?label('replanStatus',job.status):'Станция готова.'}`;
  window.stationDiagram=renderStation($('station-svg'),state,select,selected);renderSelection();renderTrains();renderOperations();renderResources();renderConflicts();renderPlans();$('raw-state').textContent=JSON.stringify(state,null,2);controls();
}
async function getDetails(){
  if(replayMode)return;
  const job=state?.last_replan;if(!job)return;const key=`${state.run_id}:${job.id}:${job.status}`;if(key===detailKey)return;detailKey=key;
  if(['queued','running'].includes(job.status)){detail=null;explanation=null;return;}
  const currentGeneration=generation;
  try{const [d,e]=await Promise.all([api(`/api/v1/replans/${encodeURIComponent(job.id)}`),api(`/api/v1/replans/${encodeURIComponent(job.id)}/explanation`)]);if(replayMode||generation!==currentGeneration||state?.last_replan?.id!==job.id)return;detail=d;explanation=e;window.stationDetail=d;window.stationExplanation=e;renderAll();}
  catch(error){if(generation===currentGeneration){detailKey=null;showError(error);}}
}
function accept(value,reset=false){
  if(!reset&&liveState?.run_id===value.run_id&&value.event_seq<liveState.event_seq)return;
  liveState=value;window.stationLiveState=value;
  if(replayMode)return;
  if(state?.run_id!==value.run_id){selected=null;detail=null;explanation=null;detailKey=null;events.length=0;observed.clear();}
  if(detail?.job.id!==value.last_replan?.id || ['queued','running'].includes(value.last_replan?.status)){detail=null;explanation=null;}
  state=value;window.stationState=state;renderAll();getDetails();
}
function stopStream(){stream?.close();stream=null;clearTimeout(retryTimer);retryTimer=null;}
function receive(event){
  try{
    const receivedWall=Date.now();
    const data=JSON.parse(event.data),value=data.state;lastReceived=performance.now();connection='LIVE';retries=0;
    const frames=window.stationMetrics.frames;frames.push({received_ms:lastReceived,seq:value.event_seq,sim_time_s:value.sim_time_s,mode:value.mode,kind:data.cause?.kind??data.reason,job:value.last_replan,occupied:value.tracks.filter(t=>t.occupied_length_m).map(t=>[t.id,t.occupied_length_m]),locomotive:value.resources.find(r=>r.id==='L1')?.location,running:value.operations.filter(o=>o.status==='running').map(o=>({id:o.id,phase:o.phase,progress:o.progress})),conflicts:value.conflicts.map(c=>c.id)});if(frames.length>2000)frames.shift();
    const key=`${value.run_id}:${value.event_seq}`;
    if(!observed.has(key)&&data.cause?.kind!=='heartbeat'){
      observed.add(key);if(observed.size>1000)observed.delete(observed.values().next().value);
      const names={tick:'Ход исполнения',incident_batch:'Зарегистрирован инцидент',incident_resolved:'Ограничение снято',operation_started:'Начата операция',operation_completed:'Завершена операция',plan_applied:'Применён проверенный план',replan_updated:'Состояние расчёта',simulation_control:'Режим симуляции',recovery:'Восстановление станции'};
      if(data.cause?.kind!=='tick'){events.unshift(`${displayTime(value).split(' · ')[0]} · ${names[data.cause?.kind]??'Изменение станции'}${value.last_replan?` · ${label('replanStatus',value.last_replan.status)}`:''}${value.conflicts.length?` · конфликтов ${value.conflicts.length}`:''}`);if(events.length>80)events.pop();}
    }
    if(event.type==='reset'||!liveState||liveState.run_id!==value.run_id||value.event_seq>liveState.event_seq)accept(value,event.type==='reset');
    $('event-log').replaceChildren(...events.map(s=>node('li',s)));status();
    renderTelemetry.rendered(value,data.cause,receivedWall,!replayMode);
  }catch(error){showError(error);}
}
function connect(){
  stopStream();if(!user||!state)return;connection='CONNECTING';status();const ownGeneration=generation;
  const current=liveState??state;stream=new EventSource(`/api/v1/stream?after=${encodeURIComponent(`${current.run_id}:${current.event_seq}`)}&client_id=${renderTelemetry.clientId}`);for(const kind of ['state','reset'])stream.addEventListener(kind,event=>{if(ownGeneration===generation)receive(event);});
  stream.onerror=()=>retryConnection(ownGeneration);
}
function retryConnection(ownGeneration){
  if(ownGeneration!==generation||!user)return;
  stopStream();connection='RECONNECTING';status();
  retryTimer=setTimeout(async()=>{if(ownGeneration!==generation||!user)return;try{await api('/api/v1/auth/me');connect();}catch(error){if(error.status===401){clearSession();showError(error);}else connect();}},Math.min(10000,1000*2**retries++)*(.8+Math.random()*.4));
}
async function snapshot(){const ownGeneration=generation;const value=await api('/api/v1/snapshot');if(ownGeneration!==generation||!user)return;accept(value);fillTargets();await renderTelemetry.start();if(ownGeneration!==generation||!user)return;connect();loadConfig();loadScenarios();}
async function loadScenarios(){const own=generation;try{const result=await api('/api/v1/scenarios');if(own!==generation||!user)return;const previous=$('run-scenario').value;$('run-scenario').replaceChildren(...result.items.map(item=>{const option=node('option',item.name);option.value=item.id;return option;}));if(result.items.some(item=>item.id===previous))$('run-scenario').value=previous;}catch(error){if(own===generation)showError(error);}}
function uuid(){if(crypto.randomUUID)return crypto.randomUUID();const b=crypto.getRandomValues(new Uint8Array(16));b[6]=b[6]&15|64;b[8]=b[8]&63|128;const s=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;}
async function command(path,fields,method='POST',permission='dispatcher'){
  const allowed=permission==='complete'?connectedWrite()&&['operator','admin'].includes(user.role):permission==='admin'?connectedWrite()&&user.role==='admin':writable();
  if(!allowed)return;clearError();pending=true;controls();
  try{return await api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:uuid(),run_id:state.run_id,expected_input_revision:state.input_revision,...fields})});}
  catch(error){showError(error);if(error.status===409)await snapshot();if(error.status===401)clearSession();}
  finally{pending=false;controls();}
}
function fillTargets(){
  if(!state)return;const kind=$('incident-kind').value;const previous=$('incident-target').value;
  const choices=kind==='track_closure'?state.tracks.map(t=>[t.id,`${t.id} · ${label('trackKind',t.kind)}`]):kind==='resource_loss'?state.resources.map(r=>[r.id,`${r.id} · ${label('resourceKind',r.kind)}`]):kind==='train_delay'?state.trains.map(t=>[t.id,`${t.id} · ${label('trainStatus',t.status)}`]):[...new Set(state.trains.map(t=>t.destination_id))].map(id=>[id,destination(id)]);
  $('incident-target').replaceChildren(...choices.map(([id,text])=>{const o=node('option',text);o.value=id;return o;}));if(choices.some(([id])=>id===previous))$('incident-target').value=previous;$('delay-field').hidden=kind!=='train_delay';
}
function clearSession(){generation++;replayGeneration++;clearTimeout(replayTimer);stopStream();state=null;liveState=null;replayMode=false;historyItems=[];historyWindow=null;$('history-panel').hidden=true;$('return-live').hidden=true;detail=null;explanation=null;detailKey=null;selected=null;lastReceived=0;events.length=0;observed.clear();window.stationState=null;window.stationLiveState=null;window.stationHistory=null;window.stationDetail=null;window.stationExplanation=null;window.stationMetrics.frames.length=0;connection='OFFLINE';for(const id of ['station-svg','raw-state','train-list','selection','shunting','operations','resources','train-resources','conflicts','incidents','plans','event-log','actual-kpi','history-events'])$(id).replaceChildren();$('config-json').value='';auth(null);status();}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();clearError();const submit=$('login-form').querySelector('button');submit.disabled=true;const ownGeneration=++generation;try{const result=await api('/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('username').value,password:$('password').value})});if(ownGeneration!==generation)return;auth(result.user);await snapshot();}catch(error){if(ownGeneration===generation)showError(error);}finally{$('password').value='';submit.disabled=false;}});
$('logout').addEventListener('click',async()=>{generation++;stopStream();pending=true;controls();try{await renderTelemetry.stop();await api('/api/v1/auth/logout',{method:'POST'});clearSession();}catch(error){showError(error);if(user)try{await snapshot();}catch(reconnectError){showError(reconnectError);}}finally{pending=false;controls();}});
$('play').addEventListener('click',()=>command('/api/v1/simulation/control',{action:'play'}));$('pause').addEventListener('click',()=>command('/api/v1/simulation/control',{action:'pause'}));$('set-speed').addEventListener('click',()=>command('/api/v1/simulation/control',{action:'set_speed',speed:Number($('speed').value)}));$('replan').addEventListener('click',()=>command('/api/v1/replans',{reason:'manual'}));
$('new-run').addEventListener('click',async()=>{if(!confirm('Начать новый учебный прогон? Предыдущая история сохранится.'))return;await command('/api/v1/runs',{scenario_id:$('run-scenario').value,seed:Number($('run-seed').value)},'POST','admin');});
$('load-metrics').addEventListener('click',async()=>{try{$('metrics-json').textContent=JSON.stringify(await api('/api/v1/metrics'),null,2);}catch(error){showError(error);}});
$('incident-kind').addEventListener('change',fillTargets);
$('incident-form').addEventListener('submit',event=>{event.preventDefault();const item={kind:$('incident-kind').value,target_id:$('incident-target').value,duration_sim_s:Number($('incident-duration').value)*60};if(item.kind==='train_delay')item.delay_sim_s=Number($('incident-delay').value)*60;command('/api/v1/incidents',{items:[item]});});
setInterval(status,250);
api('/health/ready').then(()=>{$('health').textContent='Backend готов';}).catch(e=>{$('health').textContent='Backend недоступен';showError(e);});

function renderActual(){
  const index=state.efficiency;$('actual-window').textContent=`${clock(index.window_start_sim_s)}–${clock(index.window_end_sim_s)} · учебное время`;$('actual-kpi').replaceChildren();
  const score=node('div',null,'metric');score.append(node('strong',index.score??'—'),node('span',index.category?label('efficiencyCategory',index.category):'Недостаточно данных'),node('p',`Факт · ${index.formula_version}`,'subtle'));$('actual-kpi').append(score);
  const table=node('table');const header=node('tr');['Фактор','Фактическое значение','Штраф 0..1','Потеря пунктов'].forEach(s=>header.append(node('th',s)));table.append(header);
  index.factors.forEach(f=>{const row=node('tr');row.title=f.reason;const units={ratio:'доля',sim_seconds:'с',count:'шт.'};row.append(node('td',FACTOR_NAMES[f.key]),node('td',`${f.raw==null?'—':f.raw.toFixed(3)} ${units[f.unit]??f.unit}`),node('td',f.norm_penalty?.toFixed(3)??'—'),node('td',f.contribution==null?'—':`−${f.contribution.toFixed(2)}`));table.append(row);});$('actual-kpi').append(table);
}
async function loadConfig(){
  const own=generation;try{const cfg=await api('/api/v1/config');if(own!==generation||!user)return;$('config-json').value=JSON.stringify({weights:cfg.weights,category_thresholds:cfg.category_thresholds,planner:cfg.planner},null,2);$('config-note').textContent=`Текущая конфигурация LIVE · версия ${cfg.config_version}. Изменяет только администратор.`;}catch(error){if(own===generation)showError(error);}
}
function wallLabel(value){return new Date(value).toLocaleString('ru-RU',{timeZone:'Asia/Almaty'});}
async function showHistory(index){
  const token=++replayGeneration,own=generation,item=historyItems[index];if(!item)return;
  try{const saved=await api(`/api/v1/history/snapshot?run_id=${encodeURIComponent(historyWindow.run_id)}&seq=${item.seq}`);if(!replayMode||token!==replayGeneration||own!==generation)return;state=saved;selected=null;detail=null;explanation=null;window.stationState=state;window.stationHistory={window:historyWindow,items:historyItems,selected_seq:item.seq};$('history-events').value=String(index);$('history-slider').value=String(index);$('history-selected').textContent=`${wallLabel(item.server_time)} · ${clock(saved.sim_time_s)} · ${item.message} · seq ${item.seq}`;renderAll();}catch(error){if(own===generation)showError(error);}
}
async function openHistory(){
  if(!liveState||!user)return;clearError();const own=generation,to=new Date().toISOString(),from=new Date(Date.now()-900000).toISOString();const range={run_id:liveState.run_id,from_wall_time:from,to_wall_time:to};
  $('history-open').disabled=true;
  try{let rows=[],cursor=0,page;do{const query=new URLSearchParams({...range,from_seq:String(cursor),limit:'500'});page=await api(`/api/v1/history?${query}`);rows.push(...page.items);cursor=page.next_from_seq;}while(page.has_more&&own===generation);
    if(own!==generation||!user)return;
    const anchor=await api(`/api/v1/history/snapshot?run_id=${encodeURIComponent(range.run_id)}&seq=${page.anchor_seq}`);
    if(own!==generation||!user)return;
    historyWindow=range;historyItems=[{run_id:range.run_id,seq:page.anchor_seq,server_time:anchor.server_time,sim_time_s:anchor.sim_time_s,message:'Опорное сохранённое состояние'},...rows.filter(r=>r.seq!==page.anchor_seq)];replayMode=true;detail=null;explanation=null;$('history-panel').hidden=false;
    $('history-range').textContent=`Запрошены последние 15 wall-минут: ${wallLabel(from)}–${wallLabel(to)}. Доступная история: ${wallLabel(page.available_from_wall_time)}–${wallLabel(page.available_to_wall_time)}. Новый run может быть короче.`;
    $('history-events').replaceChildren(...historyItems.map((item,i)=>{const o=node('option',`${wallLabel(item.server_time)} · ${item.message} · ${item.sim_time_s} sim-с`);o.value=String(i);return o;}));$('history-slider').max=String(historyItems.length-1);await showHistory(0);
  }catch(error){if(own===generation)showError(error);}finally{controls();}
}
function returnLive(){replayMode=false;replayGeneration++;clearTimeout(replayTimer);$('history-panel').hidden=true;state=liveState;selected=null;detail=null;explanation=null;detailKey=null;window.stationState=state;window.stationHistory=null;renderAll();getDetails();fillTargets();}
async function downloadCsv(){
  if(!state||!user)return;clearError();const range=replayMode?historyWindow:{run_id:state.run_id,from_wall_time:new Date(Date.now()-900000).toISOString(),to_wall_time:new Date().toISOString()};
  try{const response=await fetch(`/api/v1/reports.csv?${new URLSearchParams(range)}`,{credentials:'same-origin'});if(!response.ok){const error=new Error('Не удалось выгрузить CSV');error.body=await response.json();throw error;}const blob=await response.blob(),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download='station-report.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}catch(error){showError(error);}
}
$('history-open').addEventListener('click',openHistory);$('return-live').addEventListener('click',returnLive);$('history-events').addEventListener('change',()=>showHistory(Number($('history-events').value)));
$('history-slider').addEventListener('input',()=>{clearTimeout(replayTimer);replayTimer=setTimeout(()=>showHistory(Number($('history-slider').value)),120);});$('csv-download').addEventListener('click',downloadCsv);$('load-config').addEventListener('click',loadConfig);
$('save-config').addEventListener('click',async()=>{try{const patch=JSON.parse($('config-json').value);const receipt=await command('/api/v1/config',{patch},'PATCH','admin');if(receipt)$('config-note').textContent=`Сохранена версия ${receipt.result.config_version}; запрос расчёта зарегистрирован.`;}catch(error){showError(error);}});
