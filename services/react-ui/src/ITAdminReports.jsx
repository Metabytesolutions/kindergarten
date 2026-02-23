import {useState,useEffect} from 'react';
const C={dark:'#0F1117',card:'#1A1A2E',border:'rgba(255,255,255,0.08)',
  green:'#22C55E',red:'#EF4444',orange:'#F59E0B',blue:'#3B82F6',
  purple:'#8B5CF6',muted:'#71717A'};
function auth(t){return{'Authorization':`Bearer ${t}`,'Content-Type':'application/json'};}

export default function ITAdminReports({token}){
  const [view,setView]=useState('health');
  const [health,setHealth]=useState(null);
  const [reportDate,setReportDate]=useState(new Date().toISOString().split('T')[0]);
  const [reportData,setReportData]=useState(null);
  const [eodRunning,setEodRunning]=useState(false);
  const [eodResult,setEodResult]=useState(null);
  const [msg,setMsg]=useState('');

  const loadHealth=async()=>{
    try{const r=await fetch('/api/reports/health',{headers:auth(token)});
    setHealth(await r.json());}catch(e){}};

  const runCheck=async()=>{
    try{await fetch('/api/reports/health/check',{method:'POST',headers:auth(token)});
    setMsg('⏳ Running...');setTimeout(()=>{loadHealth();setMsg('');},4000);}catch(e){}};

  const loadReport=async()=>{
    try{const r=await fetch(`/api/reports/attendance?date=${reportDate}`,{headers:auth(token)});
    setReportData(await r.json());}catch(e){}};

  const triggerEOD=async()=>{
    if(!window.confirm('Run EOD now? This archives today and resets custody.')) return;
    setEodRunning(true);
    try{const r=await fetch('/api/reports/eod/trigger',{method:'POST',headers:auth(token)});
    const d=await r.json(); setEodResult(d);
    setMsg(d.success?'✅ EOD complete':'❌ '+d.error);}catch(e){}
    setEodRunning(false);};

  useEffect(()=>{loadHealth();},[]);

  const tabs=[{id:'health',label:'🔍 Health'},{id:'reports',label:'📊 Reports'},
              {id:'eod',label:'🌙 EOD'}];

  return <div style={{padding:'0 8px',color:'#E4E4E7',
    fontFamily:"'Instrument Sans',system-ui,sans-serif"}}>
    <div style={{display:'flex',gap:4,marginBottom:16,
      borderBottom:`1px solid ${C.border}`,paddingBottom:8}}>
      {tabs.map(t=><button key={t.id} onClick={()=>setView(t.id)}
        style={{background:view===t.id?C.card:'transparent',
          border:view===t.id?`1px solid ${C.border}`:'1px solid transparent',
          borderRadius:8,padding:'6px 14px',
          color:view===t.id?'#E4E4E7':'#4A5568',cursor:'pointer',
          fontSize:12,fontWeight:600}}>{t.label}</button>)}
      {msg&&<span style={{marginLeft:'auto',fontSize:11,
        color:msg.startsWith('✅')?C.green:C.red,alignSelf:'center'}}>{msg}</span>}
    </div>

    {view==='health'&&<div>
      <div style={{display:'flex',gap:10,marginBottom:16,alignItems:'center'}}>
        <span style={{fontWeight:800,fontSize:15}}>System Health</span>
        <button onClick={runCheck} style={{background:C.green,border:'none',
          borderRadius:8,padding:'6px 16px',color:'#fff',fontWeight:700,
          fontSize:12,cursor:'pointer'}}>Run Check</button>
      </div>
      {!health&&<div style={{color:C.muted,textAlign:'center',padding:40}}>Loading...</div>}
      {health?.current?.map((h,i)=><div key={i} style={{display:'flex',
        alignItems:'center',gap:12,padding:'12px 16px',marginBottom:8,
        borderRadius:10,
        background:h.status==='CRITICAL'?`${C.red}11`:h.status==='WARN'?`${C.orange}11`:`${C.green}11`,
        border:`1px solid ${h.status==='CRITICAL'?C.red:h.status==='WARN'?C.orange:C.green}33`}}>
        <span style={{fontSize:18}}>{h.status==='CRITICAL'?'🚨':h.status==='WARN'?'⚠️':'✅'}</span>
        <div style={{flex:1}}>
          <div style={{fontSize:13,fontWeight:700}}>{h.service}</div>
          <div style={{fontSize:11,color:C.muted}}>
            {Object.entries(h.detail||{}).map(([k,v])=>`${k}: ${v}`).join(' · ')}</div>
        </div>
        <div style={{fontSize:11,fontWeight:800,
          color:h.status==='CRITICAL'?C.red:h.status==='WARN'?C.orange:C.green}}>
          {h.status}</div>
      </div>)}
    </div>}

    {view==='reports'&&<div>
      <div style={{display:'flex',gap:10,marginBottom:16,alignItems:'center',flexWrap:'wrap'}}>
        <span style={{fontWeight:800,fontSize:15}}>Attendance Reports</span>
        <input type="date" value={reportDate}
          onChange={e=>{setReportDate(e.target.value);setReportData(null);}}
          style={{background:C.card,border:`1px solid ${C.border}`,
            borderRadius:8,padding:'6px 12px',color:'#E4E4E7',fontSize:13}}/>
        <button onClick={loadReport} style={{background:C.blue,border:'none',
          borderRadius:8,padding:'7px 16px',color:'#fff',fontWeight:700,
          fontSize:13,cursor:'pointer'}}>Load</button>
      </div>
      {!reportData&&<div style={{color:C.muted,textAlign:'center',padding:40}}>
        Select a date and click Load</div>}
      {reportData&&<>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(110px,1fr))',
          gap:8,marginBottom:16}}>
          {[{l:'Present',v:reportData.summary?.present||0,c:C.green},
            {l:'Absent',v:reportData.summary?.absent||0,c:C.muted},
            {l:'Checked Out',v:reportData.summary?.checked_out||0,c:C.blue},
            {l:'No Show',v:reportData.summary?.no_show||0,c:C.red},
            {l:'Total',v:reportData.summary?.total||0,c:'#E4E4E7'},
          ].map(s=><div key={s.l} style={{background:C.card,border:`1px solid ${C.border}`,
            borderRadius:10,padding:'10px',textAlign:'center'}}>
            <div style={{fontSize:22,fontWeight:800,color:s.c}}>{s.v}</div>
            <div style={{fontSize:10,color:C.muted,textTransform:'uppercase'}}>{s.l}</div>
          </div>)}
        </div>
        {reportData.students?.map((s,i)=><div key={i} style={{display:'flex',
          padding:'9px 14px',borderBottom:`1px solid ${C.border}`,fontSize:12}}>
          <div style={{flex:2,fontWeight:600}}>{s.student_name}</div>
          <div style={{flex:2,color:C.muted}}>{s.teacher_name}</div>
          <div style={{flex:1,fontWeight:700,
            color:s.status==='PRESENT'||s.status==='CHECKED_OUT'?C.green:
                  s.status==='ABSENT'?C.muted:C.red}}>{s.status}</div>
          <div style={{flex:1,color:C.muted}}>{s.total_minutes?`${s.total_minutes}m`:'—'}</div>
        </div>)}
      </>}
    </div>}

    {view==='eod'&&<div>
      <div style={{fontWeight:800,fontSize:15,marginBottom:12}}>End of Day</div>
      <div style={{background:`${C.orange}11`,border:`1px solid ${C.orange}33`,
        borderRadius:10,padding:'12px 16px',marginBottom:20,fontSize:12,color:C.muted}}>
        EOD runs automatically at session end + grace period.
        Use manual trigger only if auto job did not run.
        Archives today's attendance and resets custody.
      </div>
      <button onClick={triggerEOD} disabled={eodRunning}
        style={{background:eodRunning?C.muted:C.orange,border:'none',
          borderRadius:10,padding:'14px 28px',color:'#fff',fontWeight:800,
          fontSize:15,cursor:eodRunning?'not-allowed':'pointer',marginBottom:20}}>
        {eodRunning?'⏳ Running...':'🌙 Trigger EOD Now'}
      </button>
      {eodResult&&<div style={{background:eodResult.success?`${C.green}11`:`${C.red}11`,
        border:`1px solid ${eodResult.success?C.green:C.red}33`,
        borderRadius:10,padding:'14px 16px'}}>
        {eodResult.success?<>
          <div style={{color:C.green,fontWeight:700,marginBottom:8}}>✅ EOD Complete</div>
          <div style={{fontSize:12,color:C.muted,lineHeight:1.8}}>
            Archived: {eodResult.archived} records · Present: {eodResult.summary?.present} ·
            Absent: {eodResult.summary?.absent} · Checked Out: {eodResult.summary?.checked_out} ·
            No Show: {eodResult.summary?.no_show}
          </div>
        </>:<div style={{color:C.red}}>❌ {eodResult.error}</div>}
      </div>}
    </div>}
  </div>;
}
