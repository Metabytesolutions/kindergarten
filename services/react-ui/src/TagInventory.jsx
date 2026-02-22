
import { useState, useEffect, useCallback } from 'react';
const auth = t=>({'Content-Type':'application/json',Authorization:`Bearer ${t}`});
const C={blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',
  orange:'#E67E22',purple:'#8E44AD',teal:'#16A085',
  dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA'};

function fmt(s){
  if(s===null||s===undefined) return 'Never';
  if(s<60)  return `${s}s ago`;
  if(s<3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}

function BatteryBar({pct}){
  if(pct===null||pct===undefined) return <span style={{color:'#4A5568',fontSize:11}}>—</span>;
  const color=pct>50?C.green:pct>20?C.yellow:C.red;
  return <div style={{display:'flex',alignItems:'center',gap:6}}>
    <div style={{width:36,height:10,borderRadius:3,background:'#1E3A5F',
      border:`1px solid ${color}44`,overflow:'hidden'}}>
      <div style={{width:`${pct}%`,height:'100%',background:color,
        borderRadius:3,transition:'width 0.3s'}}/>
    </div>
    <span style={{fontSize:11,color,fontWeight:700}}>{pct}%</span>
  </div>;
}

function StatusDot({secsAgo}){
  const active = secsAgo!==null && secsAgo<60;
  const stale  = secsAgo!==null && secsAgo<300;
  const color  = active?C.green:stale?C.yellow:C.red;
  return <div style={{display:'flex',alignItems:'center',gap:5}}>
    <div style={{width:8,height:8,borderRadius:'50%',background:color,
      boxShadow:active?`0 0 6px ${color}`:undefined}}/>
    <span style={{fontSize:11,color}}>{fmt(secsAgo)}</span>
  </div>;
}

export default function TagInventory({token, compact=false}){
  const [data,    setData]   = useState(null);
  const [filter,  setFilter] = useState('ALL');
  const [loading, setLoading]= useState(true);

  const load = useCallback(async()=>{
    try{
      const r = await fetch('/api/tags/inventory',{headers:auth(token)});
      setData(await r.json());
    }catch(e){}finally{setLoading(false);}
  },[token]);

  useEffect(()=>{ load(); },[load]);
  useEffect(()=>{
    const iv=setInterval(load, 15000);
    return()=>clearInterval(iv);
  },[load]);

  if(loading) return <div style={{color:C.muted,padding:20,fontSize:13}}>Loading tags...</div>;

  const summary = data?.summary||{};
  const allTags = data?.tags||[];

  const filtered = filter==='ALL'     ? allTags
    : filter==='ASSIGNED'  ? allTags.filter(t=>t.status==='ASSIGNED')
    : filter==='INVENTORY' ? allTags.filter(t=>t.status==='INVENTORY')
    : filter==='ACTIVE'    ? allTags.filter(t=>t.secs_ago!==null&&t.secs_ago<60)
    : filter==='LOW_BAT'   ? allTags.filter(t=>t.battery_pct!==null&&t.battery_pct<20)
    : filter==='TEACHERS'  ? allTags.filter(t=>t.assigned_to==='TEACHER')
    : filter==='STUDENTS'  ? allTags.filter(t=>t.assigned_to==='STUDENT')
    : allTags;

  const FILTERS=[
    {id:'ALL',      label:`All (${summary.total||0})`},
    {id:'ACTIVE',   label:`🟢 Active (${summary.active_now||0})`},
    {id:'ASSIGNED', label:`Assigned (${summary.assigned||0})`},
    {id:'INVENTORY',label:`Inventory (${summary.inventory||0})`},
    {id:'TEACHERS', label:`👩‍🏫 Teachers (${summary.teachers||0})`},
    {id:'STUDENTS', label:`👶 Students (${summary.students||0})`},
    {id:'LOW_BAT',  label:`🔋 Low Battery (${summary.low_battery||0})`},
  ];

  return <div>
    {/* Summary cards */}
    <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10,marginBottom:16}}>
      {[
        {label:'Total Tags',  value:summary.total||0,      color:'#E4E4E7'},
        {label:'Active Now',  value:summary.active_now||0, color:C.green},
        {label:'Assigned',    value:summary.assigned||0,   color:C.blue},
        {label:'Low Battery', value:summary.low_battery||0,color:C.red},
      ].map(s=><div key={s.label} style={{background:C.dark,border:`1px solid ${C.border}`,
        borderRadius:10,padding:'10px 12px',textAlign:'center'}}>
        <div style={{fontSize:22,fontWeight:800,color:s.color}}>{s.value}</div>
        <div style={{fontSize:10,color:C.muted,fontWeight:600,marginTop:2}}>{s.label}</div>
      </div>)}
    </div>

    {/* Filter tabs */}
    <div style={{display:'flex',gap:0,borderBottom:`1px solid ${C.border}`,
      marginBottom:14,flexWrap:'wrap'}}>
      {FILTERS.map(f=><div key={f.id} onClick={()=>setFilter(f.id)}
        style={{padding:'7px 14px',cursor:'pointer',fontSize:11,fontWeight:700,
          color:filter===f.id?C.blue:C.muted,whiteSpace:'nowrap',
          borderBottom:`2px solid ${filter===f.id?C.blue:'transparent'}`}}>
        {f.label}
      </div>)}
      <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:6,padding:'0 8px'}}>
        <button onClick={load} style={{background:'none',border:`1px solid ${C.border}`,
          borderRadius:6,color:C.muted,padding:'4px 10px',cursor:'pointer',fontSize:11}}>
          🔄 Refresh
        </button>
      </div>
    </div>

    {/* Tag table */}
    <div style={{display:'flex',flexDirection:'column',gap:6}}>
      {filtered.length===0&&<div style={{color:C.muted,fontSize:13,padding:20,textAlign:'center'}}>
        No tags match filter</div>}
      {filtered.map(t=>{
        const isActive = t.secs_ago!==null&&t.secs_ago<60;
        const isLowBat = t.battery_pct!==null&&t.battery_pct<20;
        return <div key={t.id} style={{
          background:C.card,
          border:`1.5px solid ${isLowBat?C.red:isActive?`${C.green}44`:C.border}`,
          borderLeft:`4px solid ${t.status==='ASSIGNED'?C.blue:C.muted}`,
          borderRadius:10,padding:'10px 14px',
          display:'grid',
          gridTemplateColumns:'2fr 2fr 1fr 1fr 1fr 1fr',
          gap:12,alignItems:'center'}}>

          {/* Tag identity */}
          <div>
            <div style={{fontSize:12,fontWeight:800,color:'#E4E4E7',
              fontFamily:'monospace'}}>{t.mac_address}</div>
            <div style={{fontSize:11,color:C.muted,marginTop:2}}>
              {t.label||<span style={{fontStyle:'italic'}}>Unlabeled</span>}
            </div>
            <div style={{display:'flex',gap:4,marginTop:4}}>
              <span style={{fontSize:10,padding:'1px 6px',borderRadius:8,fontWeight:700,
                background:t.status==='ASSIGNED'?`${C.blue}22`:`${C.muted}22`,
                color:t.status==='ASSIGNED'?C.blue:C.muted}}>
                {t.status}
              </span>
              {t.assigned_to&&t.assigned_to!=='NONE'&&
                <span style={{fontSize:10,padding:'1px 6px',borderRadius:8,fontWeight:700,
                  background:t.assigned_to==='TEACHER'?`${C.purple}22`:`${C.teal}22`,
                  color:t.assigned_to==='TEACHER'?C.purple:C.teal}}>
                  {t.assigned_to==='TEACHER'?'👩‍🏫 TEACHER':'👶 STUDENT'}
                </span>}
            </div>
          </div>

          {/* Person assigned */}
          <div>
            {t.student_name&&<div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>
              👤 {t.student_name}
            </div>}
            {t.teacher_name&&<div style={{fontSize:11,color:C.purple}}>
              👩‍🏫 {t.teacher_name}
            </div>}
            {!t.student_name&&!t.teacher_name&&
              <div style={{fontSize:11,color:'#4A5568',fontStyle:'italic'}}>Unassigned</div>}
            {t.zone_name&&<div style={{fontSize:10,color:C.muted,marginTop:2}}>
              📍 {t.zone_name}
            </div>}
          </div>

          {/* Gateway */}
          <div style={{textAlign:'center'}}>
            {t.gateway_short_id
              ? <div style={{fontSize:11,fontWeight:700,color:C.blue,
                  padding:'2px 8px',borderRadius:8,background:`${C.blue}22`}}>
                  📡 {t.gateway_short_id}
                </div>
              : <div style={{fontSize:11,color:'#4A5568'}}>—</div>}
          </div>

          {/* Signal */}
          <div style={{textAlign:'center'}}>
            {t.last_rssi
              ? <div>
                  <div style={{fontSize:12,fontWeight:700,
                    color:t.last_rssi>-50?C.green:t.last_rssi>-70?C.yellow:C.red}}>
                    {t.last_rssi} dBm
                  </div>
                  <div style={{fontSize:10,color:'#4A5568'}}>{t.hits_5min} hits/5m</div>
                </div>
              : <span style={{fontSize:11,color:'#4A5568'}}>—</span>}
          </div>

          {/* Battery */}
          <div><BatteryBar pct={t.battery_pct}/></div>

          {/* Last seen */}
          <div><StatusDot secsAgo={t.secs_ago}/></div>
        </div>;
      })}
    </div>
  </div>;
}
