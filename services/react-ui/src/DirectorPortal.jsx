import RawDetectionMonitor from './RawDetectionMonitor';
import TagInventory from './TagInventory';

import { useState, useEffect, useCallback, useRef } from 'react';
const EAPI = '/api/events';
const DAPI = '/api/director';
const auth = t=>({'Content-Type':'application/json',Authorization:`Bearer ${t}`});
const C={blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',
  orange:'#E67E22',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',
  border:'#1E3A5F',muted:'#8899AA',navy:'#0D1F3C'};

const SEV_COLOR  ={CRITICAL:C.red,WARNING:C.orange,INFO:C.green};
const SEV_ICON   ={CRITICAL:'🚨',WARNING:'🟠',INFO:'🟢'};
const CAT_ICON   ={CUSTODY:'🔗',ATTENDANCE:'📋',VIOLATION:'⛔',SYSTEM:'⚙️',ADMIN:'👤'};
const STATE_COLOR={CONFIRMED_PRESENT:C.green,PROBABLE_PRESENT:C.yellow,
  ROAMING:C.blue,MISSING:C.red,UNKNOWN:'#4A5568',EXIT_CONFIRMED:C.red};

function fmt(ts){
  if(!ts) return '—';
  const d=new Date(ts), now=new Date();
  const diff=Math.floor((now-d)/1000);
  if(diff<60) return `${diff}s ago`;
  if(diff<3600) return `${Math.floor(diff/60)}m ago`;
  if(diff<86400) return d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  return d.toLocaleDateString();
}

function Chip({label,color,small}){
  return <span style={{fontSize:small?10:11,fontWeight:700,padding:small?'1px 6px':'2px 8px',
    borderRadius:10,background:`${color}22`,color,border:`1px solid ${color}44`}}>{label}</span>;
}

function Card({children,style={},onClick}){
  return <div onClick={onClick} style={{background:C.card,border:`1px solid ${C.border}`,
    borderRadius:14,padding:18,cursor:onClick?'pointer':'default',...style}}>{children}</div>;
}

function Btn({onClick,children,color=C.blue,disabled,small,outline}){
  return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':
    disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',
    border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,
    padding:small?'5px 12px':'9px 20px',fontFamily:'inherit',fontSize:small?11:13,
    fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'inline-flex',
    alignItems:'center',gap:6,opacity:disabled?0.5:1}}>{children}</button>;
}

// ── SUMMARY BAR ──────────────────────────────────────────────────────────────
function SummaryBar({summary, unacked}){
  const stats=[
    {label:'PRESENT', value:summary?.present||0, color:C.green},
    {label:'ROAMING', value:summary?.roaming||0, color:C.blue},
    {label:'MISSING', value:summary?.missing||0, color:C.red},
    {label:'TOTAL',   value:summary?.total||0,   color:'#E4E4E7'},
    {label:'TEACHERS',value:summary?.teachers||0,color:C.purple},
    {label:'PENDING TRANSFERS',value:summary?.pending_transfers||0,color:C.orange},
    {label:'UNACKED CRITICAL',value:unacked||0,  color:C.red},
  ];
  return <div style={{display:'grid',gridTemplateColumns:'repeat(7,1fr)',gap:10,marginBottom:20}}>
    {stats.map(s=><div key={s.label} style={{background:C.card,border:`1px solid ${s.value>0&&s.label!=='TOTAL'&&s.label!=='TEACHERS'?s.color:C.border}`,
      borderRadius:12,padding:'12px 8px',textAlign:'center'}}>
      <div style={{fontSize:26,fontWeight:800,color:s.color}}>{s.value}</div>
      <div style={{fontSize:10,color:C.muted,fontWeight:600,textTransform:'uppercase',
        letterSpacing:'0.05em',marginTop:2}}>{s.label}</div>
    </div>)}
  </div>;
}

// ── EVENT CARD ───────────────────────────────────────────────────────────────
function EventCard({event, token, onAcked, onClick}){
  const [acking, setAcking]=useState(false);
  const sev   = event.severity;
  const color = SEV_COLOR[sev]||C.muted;
  const isOpen= event.requires_ack && !event.acked_at;

  const ack = async(e)=>{
    e.stopPropagation();
    setAcking(true);
    try{
      await fetch(`${EAPI}/${event.id}/acknowledge`,{method:'POST',headers:auth(token)});
      onAcked();
    }catch(ex){}finally{setAcking(false);}
  };

  return <div onClick={()=>onClick&&onClick(event)}
    style={{background:isOpen?`${color}0D`:C.dark,
      border:`1.5px solid ${isOpen?color:C.border}`,
      borderLeft:`4px solid ${color}`,
      borderRadius:10,padding:'12px 14px',
      cursor:onClick?'pointer':'default',
      transition:'all 0.15s',marginBottom:8}}>
    <div style={{display:'flex',alignItems:'flex-start',justifyContent:'space-between',gap:8}}>
      <div style={{flex:1}}>
        <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4,flexWrap:'wrap'}}>
          <span style={{fontSize:15}}>{SEV_ICON[sev]}</span>
          <span style={{fontSize:12,fontWeight:800,color}}>{event.event_type.replace(/_/g,' ')}</span>
          <Chip label={event.category} color={C.purple} small/>
          {isOpen&&<Chip label="NEEDS ACK" color={C.red} small/>}
          {event.acked_at&&<Chip label={`ACK'd ${event.acked_by_name||''}`} color={C.green} small/>}
        </div>
        <div style={{fontSize:13,color:'#E4E4E7',fontWeight:600,marginBottom:4}}>{event.title}</div>
        {/* Students involved */}
        {event.students?.length>0&&<div style={{display:'flex',gap:4,flexWrap:'wrap',marginBottom:4}}>
          {event.students.map(s=><span key={s.id} style={{fontSize:11,padding:'2px 8px',
            borderRadius:12,background:`${C.blue}22`,color:'#E4E4E7',border:`1px solid ${C.border}`}}>
            👤 {s.first_name} {s.last_name}
          </span>)}
        </div>}
        {/* Detail preview */}
        {event.detail&&Object.keys(event.detail).length>0&&
          <div style={{fontSize:11,color:C.muted,marginTop:2}}>
            {Object.entries(event.detail).slice(0,3).map(([k,v])=>
              `${k.replace(/_/g,' ')}: ${v}`).join('  ·  ')}
          </div>}
      </div>
      <div style={{textAlign:'right',flexShrink:0}}>
        <div style={{fontSize:11,color:C.muted,marginBottom:6}}>{fmt(event.created_at)}</div>
        {event.actor_name&&<div style={{fontSize:10,color:'#4A5568',marginBottom:6}}>
          by {event.actor_name}
        </div>}
        {isOpen&&<Btn small color={C.red} disabled={acking} onClick={ack}>
          {acking?'⏳':'✓ Acknowledge'}
        </Btn>}
      </div>
    </div>
  </div>;
}

// ── EVENT DETAIL MODAL ───────────────────────────────────────────────────────
function EventDetailModal({event, token, onClose, onAcked}){
  const [acking,setAcking]=useState(false);
  if(!event) return null;
  const color=SEV_COLOR[event.severity]||C.muted;
  const isOpen=event.requires_ack&&!event.acked_at;

  const ack=async()=>{
    setAcking(true);
    try{
      await fetch(`${EAPI}/${event.id}/acknowledge`,{method:'POST',headers:auth(token)});
      onAcked();onClose();
    }catch(e){}finally{setAcking(false);}
  };

  return <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.8)',
    display:'flex',alignItems:'center',justifyContent:'center',zIndex:3000,padding:20}}>
    <div style={{background:C.card,border:`2px solid ${color}`,borderRadius:18,
      padding:28,width:'100%',maxWidth:520,maxHeight:'85vh',overflowY:'auto'}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:16}}>
        <div style={{display:'flex',alignItems:'center',gap:10}}>
          <span style={{fontSize:28}}>{SEV_ICON[event.severity]}</span>
          <div>
            <div style={{fontSize:14,fontWeight:800,color}}>{event.event_type.replace(/_/g,' ')}</div>
            <div style={{fontSize:11,color:C.muted}}>{event.category} · {event.severity}</div>
          </div>
        </div>
        <button onClick={onClose} style={{background:'none',border:'none',color:C.muted,
          fontSize:22,cursor:'pointer'}}>✕</button>
      </div>

      <div style={{fontSize:15,fontWeight:700,color:'#E4E4E7',marginBottom:16}}>{event.title}</div>

      {/* Detail fields */}
      <div style={{background:C.dark,borderRadius:10,padding:14,marginBottom:14}}>
        <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',
          marginBottom:8}}>Event Details</div>
        {Object.entries(event.detail||{}).map(([k,v])=>(
          <div key={k} style={{display:'flex',justifyContent:'space-between',
            padding:'4px 0',borderBottom:`1px solid ${C.border}`}}>
            <span style={{fontSize:12,color:C.muted}}>{k.replace(/_/g,' ')}</span>
            <span style={{fontSize:12,color:'#E4E4E7',fontWeight:600}}>{String(v)}</span>
          </div>
        ))}
      </div>

      {/* Students */}
      {event.students?.length>0&&<div style={{marginBottom:14}}>
        <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',
          marginBottom:8}}>Students Involved ({event.students.length})</div>
        <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
          {event.students.map(s=><span key={s.id} style={{fontSize:12,padding:'4px 10px',
            borderRadius:12,background:`${C.blue}22`,color:'#E4E4E7',
            border:`1px solid ${C.border}`}}>👤 {s.first_name} {s.last_name}</span>)}
        </div>
      </div>}

      {/* Meta */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:16}}>
        {[['Time',fmt(event.created_at)],
          ['Actor',event.actor_name||'System'],
          ['Zone',event.zone_name||'—'],
          ['Status',event.acked_at?`Acknowledged by ${event.acked_by_name}`:'Open'],
        ].map(([k,v])=><div key={k} style={{background:C.dark,borderRadius:8,padding:'8px 10px'}}>
          <div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>{k}</div>
          <div style={{fontSize:12,color:'#E4E4E7',fontWeight:600}}>{v}</div>
        </div>)}
      </div>

      {isOpen&&<Btn onClick={ack} disabled={acking} color={C.red} full>
        {acking?'⏳ Acknowledging...':'✓ Acknowledge This Alert'}
      </Btn>}
    </div>
  </div>;
}

// ── CLASSROOM VIEW ───────────────────────────────────────────────────────────
function ClassroomView({teachers, students}){
  return <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))',gap:16}}>
    {teachers.map(t=>{
      const myStudents=students.filter(s=>s.current_teacher_id===t.id);
      const missing=myStudents.filter(s=>['MISSING','UNKNOWN'].includes(s.presence_state||'UNKNOWN'));
      const isSub=t.teacher_type==='SUBSTITUTE';
      return <Card key={t.id} style={{borderColor:missing.length>0?C.red:isSub?C.orange:C.border}}>
        <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:12}}>
          <div style={{width:42,height:42,borderRadius:10,background:`${isSub?C.orange:C.blue}22`,
            border:`2px solid ${isSub?C.orange:C.blue}44`,display:'flex',alignItems:'center',
            justifyContent:'center',fontSize:20}}>{isSub?'🔄':'👩‍🏫'}</div>
          <div style={{flex:1}}>
            <div style={{fontSize:14,fontWeight:800,color:'#E4E4E7'}}>{t.full_name||t.username}</div>
            <div style={{fontSize:11,color:C.muted}}>{t.zone_name||'No zone'}</div>
          </div>
          <div style={{textAlign:'right'}}>
            <div style={{fontSize:20,fontWeight:800,color:missing.length>0?C.red:C.green}}>
              {myStudents.length-missing.length}<span style={{fontSize:12,color:C.muted}}>/{myStudents.length}</span>
            </div>
            <div style={{fontSize:10,color:C.muted}}>present</div>
          </div>
        </div>
        {myStudents.length===0&&<div style={{fontSize:12,color:C.muted,textAlign:'center',
          padding:'8px 0'}}>No students in custody</div>}
        <div style={{display:'flex',flexDirection:'column',gap:4}}>
          {myStudents.map(s=>{
            const state=s.presence_state||'UNKNOWN';
            const sc=STATE_COLOR[state]||'#4A5568';
            return <div key={s.id} style={{display:'flex',alignItems:'center',gap:8,
              padding:'5px 8px',borderRadius:7,
              background:state==='MISSING'||state==='EXIT_CONFIRMED'?`${C.red}11`:'transparent',
              border:`1px solid ${state==='MISSING'?C.red:C.border}`}}>
              <div style={{width:8,height:8,borderRadius:'50%',background:sc,flexShrink:0}}/>
              <div style={{flex:1,fontSize:12,color:'#E4E4E7',fontWeight:600}}>
                {s.first_name} {s.last_name}
              </div>
              <div style={{fontSize:10,color:sc,fontWeight:700}}>{state.replace('_',' ')}</div>
              {s.last_rssi&&<div style={{fontSize:10,color:'#4A5568'}}>{s.last_rssi}dBm</div>}
            </div>;
          })}
        </div>
      </Card>;
    })}
  </div>;
}

// ── MAIN DIRECTOR PORTAL ─────────────────────────────────────────────────────
export default function DirectorPortal({token}){
  const [view,       setView]       = useState('overview');
  const [overview,   setOverview]   = useState(null);
  const [events,     setEvents]     = useState([]);
  const [evTotal,    setEvTotal]    = useState(0);
  const [summary,    setSummary]    = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [evLoading,  setEvLoading]  = useState(false);
  const [selEvent,   setSelEvent]   = useState(null);
  const [catFilter,  setCatFilter]  = useState('');
  const [sevFilter,  setSevFilter]  = useState('');
  const [unackedOnly,setUnackedOnly]= useState(false);
  const [dateFrom,   setDateFrom]   = useState('');
  const [dateTo,     setDateTo]     = useState('');
  const [evOffset,   setEvOffset]   = useState(0);
  const wsRef = useRef(null);
  const EV_LIMIT = 20;

  const loadOverview = useCallback(async()=>{
    try{
      const r=await fetch(`${DAPI}/overview`,{headers:auth(token)});
      setOverview(await r.json());
    }catch(e){}
  },[token]);

  const loadSummary = useCallback(async()=>{
    try{
      const r=await fetch(`${EAPI}/summary`,{headers:auth(token)});
      setSummary(await r.json());
    }catch(e){}
  },[token]);

  const loadEvents = useCallback(async(offset=0)=>{
    setEvLoading(true);
    try{
      const params=new URLSearchParams({limit:EV_LIMIT,offset});
      if(catFilter)    params.set('category',catFilter);
      if(sevFilter)    params.set('severity',sevFilter);
      if(unackedOnly)  params.set('unacked_only','true');
      if(dateFrom)     params.set('from',dateFrom);
      if(dateTo)       params.set('to',dateTo+'T23:59:59');
      const r=await fetch(`${EAPI}?${params}`,{headers:auth(token)});
      const d=await r.json();
      setEvents(d.events||[]);
      setEvTotal(d.total||0);
      setEvOffset(offset);
    }catch(e){}finally{setEvLoading(false);}
  },[token,catFilter,sevFilter,unackedOnly,dateFrom,dateTo]);

  // Initial load
  useEffect(()=>{
    Promise.all([loadOverview(),loadSummary(),loadEvents(0)])
      .finally(()=>setLoading(false));
  },[]);

  // Poll overview every 15s
  useEffect(()=>{
    const iv=setInterval(()=>{ loadOverview(); loadSummary(); },15000);
    return()=>clearInterval(iv);
  },[loadOverview,loadSummary]);

  // Poll events every 30s
  useEffect(()=>{
    const iv=setInterval(()=>loadEvents(evOffset),30000);
    return()=>clearInterval(iv);
  },[loadEvents,evOffset]);

  // WebSocket for real-time CRITICAL events
  useEffect(()=>{
    const proto=window.location.protocol==='https:'?'wss':'ws';
    const url=`${proto}://${window.location.host}/ws?token=${token}`;
    const ws=new WebSocket(url);
    wsRef.current=ws;
    ws.onmessage=(msg)=>{
      try{
        const d=JSON.parse(msg.data);
        if(d.type==='DIRECTOR_EVENT'&&d.event?.severity==='CRITICAL'){
          // Prepend to event list + reload summary
          setEvents(prev=>[d.event,...prev.slice(0,EV_LIMIT-1)]);
          setEvTotal(prev=>prev+1);
          loadSummary();
        }
      }catch(e){}
    };
    return()=>ws.close();
  },[token]);

  // Re-load events when filters change
  useEffect(()=>{ loadEvents(0); },[catFilter,sevFilter,unackedOnly,dateFrom,dateTo]);

  const ov = overview||{};
  const students  = ov.students||[];
  const teachers  = ov.teachers||[];
  const transfers = ov.transfers||[];
  const unacked   = summary?.unacked_critical||0;

  const tabs=[
    {id:'overview', label:'🏫 Overview'},
    {id:'events',   label:`📋 Event Log${unacked>0?` 🔴${unacked}`:''}` },
    {id:'classrooms',label:'👩‍🏫 Classrooms'},
    {id:'transfers', label:`🔗 Transfers${transfers.length>0?` (${transfers.length})`:''}`},
    {id:'tags', label:'🏷️ Tag Inventory'},
    {id:'detections', label:'📡 Live Detections'},
  ];

  return <div>
    {selEvent&&<EventDetailModal event={selEvent} token={token}
      onClose={()=>setSelEvent(null)} onAcked={()=>{loadEvents(evOffset);loadSummary();}}/>}

    {/* Header */}
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:16}}>
      <div>
        <h2 style={{fontSize:22,fontWeight:800,color:'#E4E4E7',margin:0}}>Director Portal</h2>
        <p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>
          School-wide oversight · Live
          {unacked>0&&<span style={{color:C.red,fontWeight:700}}> · {unacked} unacknowledged critical</span>}
        </p>
      </div>
      <Btn small color={C.blue} onClick={()=>{loadOverview();loadSummary();loadEvents(evOffset);}}>
        🔄 Refresh
      </Btn>
    </div>

    {/* Summary bar */}
    {!loading&&<SummaryBar summary={ov.summary} unacked={unacked}/>}

    {/* Tabs */}
    <div style={{display:'flex',gap:0,borderBottom:`1px solid ${C.border}`,marginBottom:20}}>
      {tabs.map(t=><div key={t.id} onClick={()=>setView(t.id)}
        style={{padding:'10px 18px',cursor:'pointer',fontSize:13,fontWeight:700,
          color:view===t.id?C.blue:C.muted,
          borderBottom:`2px solid ${view===t.id?C.blue:'transparent'}`,
          transition:'all 0.15s'}}>{t.label}</div>)}
    </div>

    {loading&&<div style={{color:C.muted,fontSize:13,padding:40,textAlign:'center'}}>
      Loading school data...</div>}

    {/* OVERVIEW TAB */}
    {!loading&&view==='overview'&&<div>
      {/* All students grid */}
      <h3 style={{fontSize:14,fontWeight:700,color:C.blue,marginBottom:12}}>
        All Students ({students.length})
      </h3>
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(220px,1fr))',gap:10,marginBottom:24}}>
        {students.map(s=>{
          const state=s.presence_state||'UNKNOWN';
          const sc=STATE_COLOR[state]||'#4A5568';
          const isMissing=state==='MISSING'||state==='EXIT_CONFIRMED';
          return <div key={s.id} style={{background:C.dark,
            border:`1.5px solid ${isMissing?C.red:C.border}`,
            borderRadius:10,padding:12,
            background:isMissing?`${C.red}0D`:C.dark}}>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:6}}>
              <div style={{fontSize:13,fontWeight:800,color:'#E4E4E7'}}>
                {s.first_name} {s.last_name}
              </div>
              <div style={{fontSize:10,fontWeight:700,color:sc,padding:'2px 7px',
                borderRadius:10,background:`${sc}22`}}>
                {state.replace(/_/g,' ')}
              </div>
            </div>
            <div style={{fontSize:11,color:C.muted}}>{s.teacher_name||'Unassigned'}</div>
            <div style={{fontSize:11,color:'#4A5568'}}>{s.zone_name||'No zone'}</div>
            {s.tag_mac&&<div style={{fontSize:10,color:'#374151',fontFamily:'monospace',marginTop:4}}>
              {s.tag_mac} {s.last_rssi?`· ${s.last_rssi}dBm`:''}</div>}
            <div style={{fontSize:10,color:'#4A5568',marginTop:2}}>{fmt(s.last_seen_at)}</div>
          </div>;
        })}
      </div>
    </div>}

    {/* EVENT LOG TAB */}
    {!loading&&view==='events'&&<div>
      {/* Filters */}
      <div style={{display:'flex',gap:10,marginBottom:16,flexWrap:'wrap',alignItems:'center'}}>
        {/* Category filter */}
        <select value={catFilter} onChange={e=>setCatFilter(e.target.value)}
          style={{background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,
            padding:'7px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:12,outline:'none'}}>
          <option value=''>All Categories</option>
          {['CUSTODY','ATTENDANCE','VIOLATION','SYSTEM','ADMIN'].map(c=>
            <option key={c} value={c}>{CAT_ICON[c]} {c}</option>)}
        </select>
        {/* Severity filter */}
        <select value={sevFilter} onChange={e=>setSevFilter(e.target.value)}
          style={{background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,
            padding:'7px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:12,outline:'none'}}>
          <option value=''>All Severities</option>
          {['CRITICAL','WARNING','INFO'].map(s=>
            <option key={s} value={s}>{SEV_ICON[s]} {s}</option>)}
        </select>
        {/* Date range */}
        <input type='date' value={dateFrom} onChange={e=>setDateFrom(e.target.value)}
          style={{background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,
            padding:'7px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:12,outline:'none'}}/>
        <span style={{color:C.muted,fontSize:12}}>to</span>
        <input type='date' value={dateTo} onChange={e=>setDateTo(e.target.value)}
          style={{background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,
            padding:'7px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:12,outline:'none'}}/>
        {/* Unacked only */}
        <label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:C.muted,cursor:'pointer'}}>
          <input type='checkbox' checked={unackedOnly} onChange={e=>setUnackedOnly(e.target.checked)}/>
          Unacknowledged only
        </label>
        {(catFilter||sevFilter||unackedOnly||dateFrom||dateTo)&&
          <Btn small outline color={C.muted} onClick={()=>{setCatFilter('');setSevFilter('');
            setUnackedOnly(false);setDateFrom('');setDateTo('');}}>✕ Clear</Btn>}
      </div>

      {/* Count */}
      <div style={{fontSize:12,color:C.muted,marginBottom:12}}>
        {evTotal} events {evLoading&&'· loading...'}
      </div>

      {/* Event list */}
      {events.map(e=><EventCard key={e.id} event={e} token={token}
        onAcked={()=>{loadEvents(evOffset);loadSummary();}}
        onClick={setSelEvent}/>)}

      {events.length===0&&!evLoading&&<Card style={{textAlign:'center',padding:40}}>
        <div style={{fontSize:36,marginBottom:12}}>📋</div>
        <div style={{fontSize:14,color:C.muted}}>No events match your filters</div>
      </Card>}

      {/* Pagination */}
      {evTotal>EV_LIMIT&&<div style={{display:'flex',justifyContent:'center',gap:10,marginTop:16}}>
        <Btn small outline color={C.blue} disabled={evOffset===0}
          onClick={()=>loadEvents(evOffset-EV_LIMIT)}>← Prev</Btn>
        <span style={{fontSize:12,color:C.muted,alignSelf:'center'}}>
          {evOffset+1}–{Math.min(evOffset+EV_LIMIT,evTotal)} of {evTotal}
        </span>
        <Btn small outline color={C.blue} disabled={evOffset+EV_LIMIT>=evTotal}
          onClick={()=>loadEvents(evOffset+EV_LIMIT)}>Next →</Btn>
      </div>}
    </div>}

    {/* CLASSROOMS TAB */}
    {!loading&&view==='classrooms'&&
      <ClassroomView teachers={teachers} students={students}/>}

    {/* TAGS TAB */}
    {!loading&&view==='tags'&&<TagInventory token={token}/>}

    {/* DETECTIONS TAB */}
    {!loading&&view==='detections'&&<RawDetectionMonitor token={token}/>}

    {/* TRANSFERS TAB */}
    {!loading&&view==='transfers'&&<div>
      {transfers.length===0&&<Card style={{textAlign:'center',padding:40}}>
        <div style={{fontSize:36,marginBottom:12}}>🔗</div>
        <div style={{fontSize:14,color:C.muted}}>No pending transfers</div>
      </Card>}
      {transfers.map(t=><Card key={t.transfer_group} style={{marginBottom:12,
        borderColor:C.orange}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
          <div style={{fontSize:13,fontWeight:700,color:C.orange}}>⏳ Pending Transfer</div>
          <Chip label={`${t.seconds_remaining}s remaining`}
            color={t.seconds_remaining<60?C.red:C.yellow}/>
        </div>
        <div style={{fontSize:13,color:'#E4E4E7',marginBottom:4}}>
          <strong>{t.from_name}</strong> → <strong>{t.to_name}</strong>
          <span style={{color:C.muted}}> · {t.zone_name}</span>
        </div>
        <div style={{fontSize:12,color:C.muted}}>
          {t.student_count} student{t.student_count!==1?'s':''} · initiated {fmt(t.initiated_at)}
        </div>
      </Card>)}
    </div>}
  </div>;
}
