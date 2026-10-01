// Non-domain instrumentation: five clock probes and double-rAF of actual rendered State.
export function createRenderTelemetry(api, uuid) {
  const clientId=sessionStorage.getItem('station_client_id')||uuid();
  sessionStorage.setItem('station_client_id',clientId);
  let calibration=null, timer=null, active=false, generation=0;
  const seen=new Set(), reports=[], probes=[], inflight=new Set();
  const diagnostic={client_id:clientId,reports,probes,calibration:null,errors:0};
  async function send(path,options) {
    const promise=api(path,options);inflight.add(promise);
    try{return await promise;}finally{inflight.delete(promise);}
  }
  async function calibrate() {
    const own=generation, samples=[];
    for(let i=0;i<5;i++) {
      if(own!==generation||!active)return;
      const t0=Date.now(),p0=performance.now();
      const result=await send('/api/v1/time');
      const t3=Date.now(),p3=performance.now();
      const rtt=(t3-t0)-(result.server_sent_ms-result.server_received_ms);
      const sample={t0,t3,...result,rtt_ms:rtt,offset_ms:((result.server_received_ms-t0)+(result.server_sent_ms-t3))/2,
        uncertainty_ms:Math.max(0,rtt/2),clock_jump:Math.abs((t3-t0)-(p3-p0))>50};
      samples.push(sample);probes.push(sample);if(probes.length>200)probes.shift();
    }
    if(own!==generation)return;
    const valid=samples.filter(s=>!s.clock_jump&&s.rtt_ms>=-2).sort((a,b)=>a.rtt_ms-b.rtt_ms);
    calibration=valid.length?{...valid[0],wall_anchor:Date.now(),mono_anchor:performance.now()}:null;
    diagnostic.calibration=calibration;
  }
  return {
    clientId,diagnostic,
    async start(){active=true;const own=++generation;seen.clear();await calibrate();if(own!==generation||!active)return;clearInterval(timer);timer=setInterval(()=>calibrate().catch(()=>diagnostic.errors++),30000);},
    stop(){active=false;generation++;clearInterval(timer);timer=null;calibration=null;seen.clear();return Promise.allSettled([...inflight]);},
    rendered(value, cause, receivedMs, actuallyVisible) {
      const key=`${value.run_id}:${value.event_seq}`;
      if(!active||!cause||cause.kind==='heartbeat'||seen.has(key)||!actuallyVisible)return;
      seen.add(key);if(seen.size>3000)seen.delete(seen.values().next().value);
      const own=generation;
      requestAnimationFrame(()=>requestAnimationFrame(()=>{
        if(own!==generation||!active)return;
        const rendered=Date.now(),c=calibration;
        const jump=!c||Math.abs((rendered-c.wall_anchor)-(performance.now()-c.mono_anchor))>50;
        const report={run_id:value.run_id,event_seq:value.event_seq,client_id:clientId,received_client_ms:receivedMs,
          rendered_client_ms:rendered,offset_ms:c?.offset_ms??0,uncertainty_ms:jump?51:c.uncertainty_ms,
          visible:document.visibilityState==='visible'};
        const record={...report,ingested_at:cause.ingested_at,cause_kind:cause.kind};
        reports.push(record);if(reports.length>3000)reports.shift();
        send('/api/v1/telemetry/ui-render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(report)})
          .then(()=>{record.accepted=true;}).catch(()=>{diagnostic.errors++;record.accepted=false;});
        if(jump)calibrate().catch(()=>diagnostic.errors++);
      }));
    }
  };
}
