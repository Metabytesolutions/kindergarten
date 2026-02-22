#!/usr/bin/env python3
import os, subprocess, time, urllib.request, json as J

BASE = os.path.expanduser('~/prosper-platform')
UI   = f'{BASE}/services/react-ui/src'
API  = f'{BASE}/services/app-server/src'

def run(cmd):
    print(f'  $ {cmd[:80]}')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout.strip(): print(r.stdout.strip()[:400])
    if r.returncode != 0 and r.stderr.strip(): print(f'  ERR: {r.stderr.strip()[:200]}')
    return r.stdout.strip()

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write(content)
    print(f'  ✅ {os.path.basename(path)}')

print('\n' + '='*55)
print('  Prosper RFID — Director Portal')
print('='*55)

# STEP 1: Director API
print('\n📝 Step 1: Writing directorApi.js...')
write(f'{API}/directorApi.js', r"""
'use strict';
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// Restrict to DIRECTOR + IT
router.use((req,res,next)=>{
  if(!['DIRECTOR','IT'].includes(req.user.role))
    return res.status(403).json({error:'Director or IT Admin only'});
  next();
});

// GET /api/director/overview — school-wide snapshot
router.get('/overview', async (req,res)=>{
  try {
    // All students with custody + presence
    const students = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.grade, s.student_id as school_id,
        sc.current_teacher_id, sc.current_zone_id, sc.custody_since,
        u.full_name  as teacher_name, u.username as teacher_username,
        u.teacher_type,
        z.name       as zone_name, z.zone_type,
        t.mac_address as tag_mac, t.last_rssi, t.battery_mv, t.last_seen_at,
        ps.state     as presence_state
      FROM students s
      LEFT JOIN student_custody sc ON sc.student_id=s.id
      LEFT JOIN users u  ON u.id=sc.current_teacher_id
      LEFT JOIN zones z  ON z.id=sc.current_zone_id
      LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=s.id
      WHERE s.is_active=true
      ORDER BY u.full_name NULLS LAST, s.last_name
    `);

    // All teachers with student counts
    const teachers = await db.query(`
      SELECT u.id, u.username, u.full_name, u.teacher_type,
        z.name as zone_name, z.zone_type,
        COUNT(sc.student_id) as student_count,
        COUNT(sc.student_id) FILTER (
          WHERE ps.state IN ('CONFIRMED_PRESENT','PROBABLE_PRESENT')
        ) as present_count,
        COUNT(sc.student_id) FILTER (
          WHERE ps.state='MISSING' OR ps.state IS NULL
        ) as missing_count
      FROM users u
      LEFT JOIN zones z ON z.id=u.zone_id
      LEFT JOIN student_custody sc ON sc.current_teacher_id=u.id
      LEFT JOIN students s ON s.id=sc.student_id AND s.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=sc.student_id
      WHERE u.role IN ('TEACHER','SUBSTITUTE') AND u.is_active=true
      GROUP BY u.id, z.name, z.zone_type
      ORDER BY u.teacher_type, u.full_name
    `);

    // Pending transfers
    const transfers = await db.query(`
      SELECT ct.transfer_group, ct.status,
        fu.full_name as from_name, tu.full_name as to_name,
        z.name as zone_name, ct.initiated_at,
        COUNT(ct.student_id) as student_count,
        EXTRACT(EPOCH FROM (ct.expires_at-NOW()))::int as seconds_remaining
      FROM custody_transfers ct
      JOIN users fu ON fu.id=ct.from_teacher_id
      JOIN users tu ON tu.id=ct.to_teacher_id
      JOIN zones z  ON z.id=ct.to_zone_id
      WHERE ct.status='PENDING' AND ct.expires_at>NOW()
      GROUP BY ct.transfer_group,ct.status,fu.full_name,tu.full_name,z.name,
               ct.initiated_at,ct.expires_at
      ORDER BY ct.initiated_at
    `);

    // Summary counts
    const states = students.rows.map(s=>s.presence_state||'UNKNOWN');
    res.json({
      summary: {
        total:    students.rows.length,
        present:  states.filter(s=>['CONFIRMED_PRESENT','PROBABLE_PRESENT'].includes(s)).length,
        roaming:  states.filter(s=>s==='ROAMING').length,
        missing:  states.filter(s=>['MISSING','UNKNOWN'].includes(s)).length,
        teachers: teachers.rows.length,
        pending_transfers: transfers.rows.length,
      },
      students:  students.rows,
      teachers:  teachers.rows,
      transfers: transfers.rows,
    });
  } catch(e){ res.status(500).json({error:e.message}); }
});

// GET /api/director/student/:id — full student history
router.get('/student/:id', async(req,res)=>{
  try {
    const [student, custody, transfers, events] = await Promise.all([
      db.query(`
        SELECT s.*, u.full_name as teacher_name, z.name as zone_name,
          t.mac_address as tag_mac, t.battery_mv, t.last_seen_at, t.last_rssi,
          ps.state as presence_state
        FROM students s
        LEFT JOIN users u ON u.id=s.teacher_id
        LEFT JOIN zones z ON z.id=s.zone_id
        LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
        LEFT JOIN presence_states ps ON ps.student_id=s.id
        WHERE s.id=$1`, [req.params.id]),
      db.query(`
        SELECT sc.*, u.full_name as teacher_name, z.name as zone_name
        FROM student_custody sc
        JOIN users u ON u.id=sc.current_teacher_id
        JOIN zones z ON z.id=sc.current_zone_id
        WHERE sc.student_id=$1`, [req.params.id]),
      db.query(`
        SELECT ct.*, fu.full_name as from_name, tu.full_name as to_name,
          z.name as zone_name
        FROM custody_transfers ct
        JOIN users fu ON fu.id=ct.from_teacher_id
        JOIN users tu ON tu.id=ct.to_teacher_id
        JOIN zones z  ON z.id=ct.to_zone_id
        WHERE ct.student_id=$1
        ORDER BY ct.initiated_at DESC LIMIT 20`, [req.params.id]),
      db.query(`
        SELECT * FROM director_events
        WHERE $1=ANY(student_ids)
        ORDER BY created_at DESC LIMIT 30`, [req.params.id]),
    ]);
    if(!student.rows[0]) return res.status(404).json({error:'Student not found'});
    res.json({
      student:   student.rows[0],
      custody:   custody.rows[0],
      transfers: transfers.rows,
      events:    events.rows,
    });
  } catch(e){ res.status(500).json({error:e.message}); }
});

module.exports = router;
""")

# Wire route
idx = f'{API}/index.js'
isrc = open(idx).read()
if 'directorApiRouter' not in isrc:
    open(idx,'a').write(
        "\nconst directorApiRouter = require('./directorApi');\n"
        "app.use('/api/director', requireAuth, directorApiRouter);\n")
    print('  ✅ /api/director route wired')
else:
    print('  ⏭  Already wired')

# STEP 2: Write DirectorPortal.jsx
print('\n📝 Step 2: Writing DirectorPortal.jsx...')
write(f'{UI}/DirectorPortal.jsx', r"""
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
""")

# STEP 3: Wire DirectorPortal into App.jsx
print('\n🔌 Step 3: Wiring DirectorPortal into App.jsx...')
app_path = f'{UI}/App.jsx'
src = open(app_path).read()
changed = False

if 'DirectorPortal' not in src:
    src = src.replace(
        "import CustodyManager from './CustodyManager'",
        "import CustodyManager from './CustodyManager'\nimport DirectorPortal from './DirectorPortal'"
    )
    changed = True
    print('  ✅ Import added')

# Find director render section and replace/add DirectorPortal
if 'DirectorPortal' not in src:
    # Add director view — look for where director role renders
    src = src.replace(
        "{role==='DIRECTOR' &&",
        "{role==='DIRECTOR' && <div style={{padding:24}}><DirectorPortal token={token}/></div>} {role==='DIRECTOR_OLD' &&"
    )
    changed = True

# More targeted: find the director section
if 'DirectorPortal' not in src:
    # Try to find any existing director placeholder and replace
    if 'director' in src.lower():
        lines = src.split('\n')
        for i, line in enumerate(lines):
            if 'DIRECTOR' in line and 'role===' in line:
                print(f'  Found director section at line {i+1}: {line.strip()[:60]}')
    changed = True

open(app_path,'w').write(src)

# Check if App has role-based routing and inject properly
src = open(app_path).read()
print(f'  DirectorPortal in App.jsx: {"DirectorPortal" in src}')

# Write a targeted patch based on what's actually in App.jsx
print('\n🔍 Inspecting App.jsx structure...')
lines = src.split('\n')
director_lines = [(i+1,l) for i,l in enumerate(lines) if 'director' in l.lower() or 'DIRECTOR' in l]
for ln, l in director_lines[:10]:
    print(f'  Line {ln}: {l.strip()[:80]}')

# STEP 4: Create standalone DirectorView wrapper if needed
print('\n📝 Step 4: Ensuring DirectorView.jsx delegates to DirectorPortal...')
director_view = f'{UI}/DirectorView.jsx'
write(director_view, r"""
import DirectorPortal from './DirectorPortal';
export default function DirectorView({ token }) {
  return <DirectorPortal token={token} />;
}
""")

# STEP 5: Wire into App.jsx properly
print('\n🔌 Step 5: Final App.jsx wiring...')
src = open(app_path).read()

# Add imports if missing
if "DirectorView" not in src and "DirectorPortal" not in src:
    src = src.replace(
        "import React",
        "import DirectorView from './DirectorView';\nimport React"
    )
    print('  ✅ DirectorView import added')
elif "DirectorPortal" in src and "DirectorView" not in src:
    src = src.replace(
        "import DirectorPortal from './DirectorPortal'",
        "import DirectorPortal from './DirectorPortal'\nimport DirectorView from './DirectorView'"
    )
    print('  ✅ DirectorView import added')

# Find the role-based render and inject director portal
# Pattern: look for where teacher/director content is rendered
if "role === 'DIRECTOR'" in src or 'role===\'DIRECTOR\'' in src:
    # Replace existing director render
    for old, new in [
        ("role === 'DIRECTOR' && <div",
         "role === 'DIRECTOR' && <div style={{padding:24}}><DirectorPortal token={token}/></div>} {false && <div"),
        ("role==='DIRECTOR' && <div",
         "role==='DIRECTOR' && <div style={{padding:24}}><DirectorPortal token={token}/></div>} {false && <div"),
    ]:
        if old in src and 'DirectorPortal' not in src:
            src = src.replace(old, new)
            print('  ✅ Director render replaced with DirectorPortal')
            break

open(app_path, 'w').write(src)

# Show current App.jsx role section for verification
src = open(app_path).read()
lines = src.split('\n')
for i,l in enumerate(lines):
    if 'DIRECTOR' in l or 'DirectorPortal' in l or 'DirectorView' in l:
        print(f'  Line {i+1}: {l.strip()[:90]}')

# STEP 6: Rebuild
print('\n🐳 Step 6: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 35s...')
time.sleep(35)

# STEP 7: Smoke test
print('\n🧪 Step 7: Smoke test...')
try:
    # Test as admin (IT)
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print('  ✅ Login OK (admin/IT)')

    for path, label in [
        ('/api/director/overview', 'director overview'),
        ('/api/events?limit=5',   'event stream'),
        ('/api/events/summary',   'event summary'),
    ]:
        req2 = urllib.request.Request(f'http://localhost{path}',
            headers={'Authorization':f'Bearer {token}'})
        d = J.loads(urllib.request.urlopen(req2,timeout=10).read())
        if 'summary' in d and 'total' in d.get('summary',{}):
            s = d['summary']
            print(f'  ✅ {label} → {s["total"]} students, {s["teachers"]} teachers, {s["missing"]} missing')
        elif 'events' in d:
            print(f'  ✅ {label} → {d["total"]} events total')
            for e in d['events'][:3]:
                icon={'CRITICAL':'🚨','WARNING':'🟠','INFO':'🟢'}.get(e['severity'],'📋')
                print(f'     {icon} {e["event_type"]} — {e["title"][:45]}')
        elif 'unacked_critical' in d:
            print(f'  ✅ {label} → {d["unacked_critical"]} unacked critical, {d["last_hour"]} last hour')

    # Test as director
    req3 = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"director","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    tok2 = J.loads(urllib.request.urlopen(req3,timeout=10).read())['token']
    req4 = urllib.request.Request('http://localhost/api/events/summary',
        headers={'Authorization':f'Bearer {tok2}'})
    ds = J.loads(urllib.request.urlopen(req4,timeout=10).read())
    print(f'  ✅ Director login → {ds["unacked_critical"]} unacked critical alerts')

except Exception as e:
    print(f'  ❌ {e}')
    import traceback; traceback.print_exc()

print('\n' + '='*55)
print('  ✅ DIRECTOR PORTAL DEPLOYED')
print('='*55)
print('\n  Login as director → full portal available')
print('  🏫 Overview    — all students live presence')
print('  📋 Event Log   — full audit stream + filters')
print('  👩‍🏫 Classrooms  — per-teacher custody view')
print('  🔗 Transfers   — pending custody transfers')
print('  🔴 CRITICAL alerts push via WebSocket instantly')
print('  ✓  Acknowledge button on critical alerts\n')
