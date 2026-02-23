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
  if(pct===null||pct===undefined) return <span style={{color:'#4A5568',fontSize:11}}>N/A</span>;
  const color=pct>50?C.green:pct>20?C.yellow:C.red;
  return <div style={{display:'flex',alignItems:'center',gap:6}}>
    <div style={{width:36,height:10,borderRadius:3,background:'#1E3A5F',
      border:`1px solid ${color}44`,overflow:'hidden'}}>
      <div style={{width:`${pct}%`,height:'100%',background:color,borderRadius:3}}/>
    </div>
    <span style={{fontSize:11,color,fontWeight:700}}>{pct}%</span>
  </div>;
}

function StatusDot({secsAgo}){
  const active=secsAgo!==null&&secsAgo<60;
  const stale=secsAgo!==null&&secsAgo<300;
  const color=active?C.green:stale?C.yellow:C.red;
  return <div style={{display:'flex',alignItems:'center',gap:5}}>
    <div style={{width:8,height:8,borderRadius:'50%',background:color,
      boxShadow:active?`0 0 6px ${color}`:undefined}}/>
    <span style={{fontSize:11,color}}>{fmt(secsAgo)}</span>
  </div>;
}

// ── ASSIGN MODAL ─────────────────────────────────────────────
function AssignModal({tag, students, teachers, token, onClose, onSaved}){
  const [mode,    setMode]   = useState('STUDENT');
  const [selId,   setSelId]  = useState('');
  const [label,   setLabel]  = useState(tag.label||'');
  const [saving,  setSaving] = useState(false);
  const [error,   setError]  = useState('');

  const save = async()=>{
    if(!selId && mode!=='UNASSIGN') return setError('Please select a person');
    setSaving(true); setError('');
    try{
      const body = mode==='UNASSIGN'
        ? { student_id: null, label: null, assigned_to: null }
        : mode==='STUDENT'
        ? { student_id: selId, label, assigned_to: 'STUDENT' }
        : { student_id: null,  label, assigned_to: 'TEACHER', teacher_id: selId };

      const r = await fetch(`/api/tags/${tag.id}/assign`,{
        method:'PUT',
        headers:auth(token),
        body:JSON.stringify(body)
      });
      const d = await r.json();
      if(!r.ok) throw new Error(d.error||'Save failed');
      onSaved();
      onClose();
    }catch(e){ setError(e.message); }
    finally{ setSaving(false); }
  };

  return <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.75)',
    display:'flex',alignItems:'center',justifyContent:'center',zIndex:3000}}>
    <div style={{background:C.card,border:`2px solid ${C.blue}`,borderRadius:18,
      padding:28,width:420,boxShadow:'0 20px 60px rgba(0,0,0,0.5)'}}>

      <div style={{display:'flex',justifyContent:'space-between',marginBottom:20}}>
        <div>
          <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>
            🏷️ Assign Tag
          </div>
          <div style={{fontSize:11,color:C.muted,fontFamily:'monospace',marginTop:2}}>
            {tag.mac_address}
          </div>
        </div>
        <button onClick={onClose} style={{background:'none',border:'none',
          color:C.muted,fontSize:20,cursor:'pointer'}}>✕</button>
      </div>

      {/* Label */}
      <div style={{marginBottom:14}}>
        <label style={{fontSize:11,color:C.muted,fontWeight:600,
          textTransform:'uppercase',display:'block',marginBottom:6}}>
          Tag Label
        </label>
        <input value={label} onChange={e=>setLabel(e.target.value)}
          placeholder="e.g. Jane Smith"
          style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,
            borderRadius:8,padding:'8px 12px',color:'#E4E4E7',
            fontFamily:'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/>
      </div>

      {/* Mode selector */}
      <div style={{marginBottom:14}}>
        <label style={{fontSize:11,color:C.muted,fontWeight:600,
          textTransform:'uppercase',display:'block',marginBottom:6}}>
          Assign To
        </label>
        <div style={{display:'flex',gap:0,border:`1px solid ${C.border}`,
          borderRadius:8,overflow:'hidden'}}>
          {[['STUDENT','👶 Student'],['TEACHER','👩‍🏫 Teacher'],['UNASSIGN','🗑️ Unassign']].map(([id,lbl])=>
            <div key={id} onClick={()=>{setMode(id);setSelId('');}}
              style={{flex:1,padding:'8px 4px',textAlign:'center',cursor:'pointer',
                fontSize:12,fontWeight:700,
                background:mode===id?C.blue:'transparent',
                color:mode===id?'#fff':C.muted}}>
              {lbl}
            </div>)}
        </div>
      </div>

      {/* Person selector */}
      {mode==='STUDENT'&&<div style={{marginBottom:14}}>
        <label style={{fontSize:11,color:C.muted,fontWeight:600,
          textTransform:'uppercase',display:'block',marginBottom:6}}>
          Select Student
        </label>
        <select value={selId} onChange={e=>setSelId(e.target.value)}
          style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,
            borderRadius:8,padding:'8px 12px',color:'#E4E4E7',
            fontFamily:'inherit',fontSize:13,outline:'none'}}>
          <option value=''>— Select student —</option>
          {students.map(s=><option key={s.id} value={s.id}>
            {s.first_name} {s.last_name}
            {s.tag_mac?' (has tag)':''}
          </option>)}
        </select>
      </div>}

      {mode==='TEACHER'&&<div style={{marginBottom:14}}>
        <label style={{fontSize:11,color:C.muted,fontWeight:600,
          textTransform:'uppercase',display:'block',marginBottom:6}}>
          Select Teacher
        </label>
        <select value={selId} onChange={e=>{
            setSelId(e.target.value);
            const t=teachers.find(t=>t.id===e.target.value);
            if(t) setLabel(t.full_name||t.username);
          }}
          style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,
            borderRadius:8,padding:'8px 12px',color:'#E4E4E7',
            fontFamily:'inherit',fontSize:13,outline:'none'}}>
          <option value=''>— Select teacher —</option>
          {teachers.map(t=><option key={t.id} value={t.id}>
            {t.full_name||t.username}
          </option>)}
        </select>
      </div>}

      {mode==='UNASSIGN'&&<div style={{background:`${C.red}11`,border:`1px solid ${C.red}44`,
        borderRadius:8,padding:12,marginBottom:14,fontSize:12,color:C.red}}>
        ⚠️ This will remove the tag assignment and return it to Inventory.
      </div>}

      {error&&<div style={{color:C.red,fontSize:12,marginBottom:10}}>{error}</div>}

      <div style={{display:'flex',gap:10,justifyContent:'flex-end'}}>
        <button onClick={onClose} style={{padding:'8px 20px',borderRadius:8,
          background:'none',border:`1px solid ${C.border}`,
          color:C.muted,cursor:'pointer',fontSize:13}}>
          Cancel
        </button>
        <button onClick={save} disabled={saving}
          style={{padding:'8px 24px',borderRadius:8,border:'none',
            background:mode==='UNASSIGN'?C.red:C.blue,
            color:'#fff',cursor:'pointer',fontSize:13,fontWeight:700,
            opacity:saving?0.6:1}}>
          {saving?'Saving...':(mode==='UNASSIGN'?'Unassign':'Save Assignment')}
        </button>
      </div>
    </div>
  </div>;
}

// ── MAIN COMPONENT ────────────────────────────────────────────
export default function TagInventory({token, compact=false}){
  const [data,     setData]    = useState(null);
  const [filter,   setFilter]  = useState('ALL');
  const [loading,  setLoading] = useState(true);
  const [students, setStudents]= useState([]);
  const [teachers, setTeachers]= useState([]);
  const [assignTag,setAssignTag]= useState(null);

  const load = useCallback(async()=>{
    try{
      const r = await fetch('/api/tags/inventory',{headers:auth(token)});
      setData(await r.json());
    }catch(e){}finally{setLoading(false);}
  },[token]);

  const loadPeople = useCallback(async()=>{
    try{
      const [sr,ur] = await Promise.all([
        fetch('/api/admin/students',{headers:auth(token)}),
        fetch('/api/admin/users',{headers:auth(token)}),
      ]);
      const sd = await sr.json();
      const ud = await ur.json();
      setStudents(Array.isArray(sd)?sd:sd.students||[]);
      setTeachers((Array.isArray(ud)?ud:ud.users||[])
        .filter(u=>u.role==='TEACHER'));
    }catch(e){}
  },[token]);

  useEffect(()=>{ load(); loadPeople(); },[load,loadPeople]);
  useEffect(()=>{
    const iv=setInterval(load,15000);
    return()=>clearInterval(iv);
  },[load]);

  if(loading) return <div style={{color:C.muted,padding:20,fontSize:13}}>Loading tags...</div>;

  const summary = data?.summary||{};
  const allTags = data?.tags||[];

  const filtered = filter==='ALL'      ? allTags
    : filter==='ASSIGNED'  ? allTags.filter(t=>t.status==='ASSIGNED')
    : filter==='INVENTORY' ? allTags.filter(t=>t.status==='INVENTORY')
    : filter==='ACTIVE'    ? allTags.filter(t=>t.secs_ago!==null&&t.secs_ago<60)
    : filter==='LOW_BAT'   ? allTags.filter(t=>t.battery_pct!==null&&t.battery_pct<20)
    : filter==='TEACHERS'  ? allTags.filter(t=>t.assigned_to==='TEACHER')
    : filter==='STUDENTS'  ? allTags.filter(t=>t.assigned_to==='STUDENT')
    : allTags;

  const FILTERS=[
    {id:'ALL',       label:`All (${summary.total||0})`},
    {id:'ACTIVE',    label:`🟢 Active (${summary.active_now||0})`},
    {id:'ASSIGNED',  label:`Assigned (${summary.assigned||0})`},
    {id:'INVENTORY', label:`⚠️ Unassigned (${summary.inventory||0})`},
    {id:'TEACHERS',  label:`👩‍🏫 Teachers (${summary.teachers||0})`},
    {id:'STUDENTS',  label:`👶 Students (${summary.students||0})`},
    {id:'LOW_BAT',   label:`🔋 Low Battery (${summary.low_battery||0})`},
  ];

  return <div>
    {assignTag&&<AssignModal tag={assignTag} students={students}
      teachers={teachers} token={token}
      onClose={()=>setAssignTag(null)}
      onSaved={()=>{ load(); loadPeople(); }}/>}

    {/* Summary cards */}
    <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10,marginBottom:16}}>
      {[
        {label:'Total Tags',  value:summary.total||0,       color:'#E4E4E7'},
        {label:'Active Now',  value:summary.active_now||0,  color:C.green},
        {label:'Assigned',    value:summary.assigned||0,    color:C.blue},
        {label:'Unassigned',  value:summary.inventory||0,   color:summary.inventory>0?C.orange:'#4A5568'},
      ].map(s=><div key={s.label} style={{background:C.dark,
        border:`1px solid ${s.value>0&&s.label==='Unassigned'?C.orange:C.border}`,
        borderRadius:10,padding:'10px 12px',textAlign:'center'}}>
        <div style={{fontSize:22,fontWeight:800,color:s.color}}>{s.value}</div>
        <div style={{fontSize:10,color:C.muted,fontWeight:600,marginTop:2}}>{s.label}</div>
      </div>)}
    </div>

    {/* Unassigned banner */}
    {summary.inventory>0&&<div style={{background:`${C.orange}11`,
      border:`1px solid ${C.orange}44`,borderRadius:10,padding:'10px 16px',
      marginBottom:14,display:'flex',alignItems:'center',justifyContent:'space-between'}}>
      <div style={{fontSize:13,color:C.orange,fontWeight:700}}>
        ⚠️ {summary.inventory} unassigned tag{summary.inventory>1?'s':''} detected
      </div>
      <button onClick={()=>setFilter('INVENTORY')}
        style={{background:C.orange,border:'none',borderRadius:8,
          color:'#fff',padding:'5px 14px',cursor:'pointer',fontSize:12,fontWeight:700}}>
        View & Assign →
      </button>
    </div>}

    {/* Filter tabs */}
    <div style={{display:'flex',gap:0,borderBottom:`1px solid ${C.border}`,
      marginBottom:14,flexWrap:'wrap'}}>
      {FILTERS.map(f=><div key={f.id} onClick={()=>setFilter(f.id)}
        style={{padding:'7px 14px',cursor:'pointer',fontSize:11,fontWeight:700,
          color:filter===f.id?C.blue:C.muted,whiteSpace:'nowrap',
          borderBottom:`2px solid ${filter===f.id?C.blue:'transparent'}`}}>
        {f.label}
      </div>)}
      <div style={{marginLeft:'auto',display:'flex',alignItems:'center',padding:'0 8px'}}>
        <button onClick={load} style={{background:'none',border:`1px solid ${C.border}`,
          borderRadius:6,color:C.muted,padding:'4px 10px',cursor:'pointer',fontSize:11}}>
          🔄 Refresh
        </button>
      </div>
    </div>

    {/* Tag rows */}
    <div style={{display:'flex',flexDirection:'column',gap:6}}>
      {filtered.length===0&&<div style={{color:C.muted,fontSize:13,
        padding:20,textAlign:'center'}}>No tags match filter</div>}

      {filtered.map(t=>{
        const isActive  = t.secs_ago!==null&&t.secs_ago<60;
        const isLowBat  = t.battery_pct!==null&&t.battery_pct<20;
        const unassigned= t.status==='INVENTORY';
        return <div key={t.id} style={{
          background:C.card,
          border:`1.5px solid ${unassigned?C.orange:isActive?`${C.green}44`:C.border}`,
          borderLeft:`4px solid ${unassigned?C.orange:t.assigned_to==='TEACHER'?C.purple:C.blue}`,
          borderRadius:10,padding:'10px 14px',
          display:'grid',
          gridTemplateColumns:'2fr 2fr 1fr 1fr 1fr 1fr 80px',
          gap:12,alignItems:'center'}}>

          <div>
            <div style={{fontSize:12,fontWeight:800,color:'#E4E4E7',
              fontFamily:'monospace'}}>{t.mac_address}</div>
            <div style={{fontSize:11,color:C.muted,marginTop:2}}>{t.label||'—'}</div>
            <div style={{display:'flex',gap:4,marginTop:4,flexWrap:'wrap'}}>
              <span style={{fontSize:10,padding:'1px 6px',borderRadius:8,fontWeight:700,
                background:unassigned?`${C.orange}22`:`${C.blue}22`,
                color:unassigned?C.orange:C.blue}}>
                {t.status}
              </span>
              {t.assigned_to&&t.assigned_to!=='NONE'&&
                <span style={{fontSize:10,padding:'1px 6px',borderRadius:8,fontWeight:700,
                  background:t.assigned_to==='TEACHER'?`${C.purple}22`:`${C.teal}22`,
                  color:t.assigned_to==='TEACHER'?C.purple:C.teal}}>
                  {t.assigned_to==='TEACHER'?'👩‍🏫':'👶'} {t.assigned_to}
                </span>}
            </div>
          </div>

          <div>
            {t.student_name&&<div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>
              👤 {t.student_name}
            </div>}
            {t.teacher_name&&<div style={{fontSize:12,fontWeight:700,color:C.purple}}>
              👩‍🏫 {t.teacher_name}
            </div>}
            {!t.student_name&&!t.teacher_name&&
              <div style={{fontSize:11,color:unassigned?C.orange:'#4A5568',
                fontStyle:'italic'}}>
                {unassigned?'⚠️ Tap Assign →':'Unassigned'}
              </div>}
            {t.zone_name&&<div style={{fontSize:10,color:C.muted,marginTop:2}}>
              📍 {t.zone_name}
            </div>}
          </div>

          <div style={{textAlign:'center'}}>
            {t.gateway_short_id
              ?<div style={{fontSize:11,fontWeight:700,color:C.blue,
                  padding:'2px 8px',borderRadius:8,background:`${C.blue}22`}}>
                📡 {t.gateway_short_id}
              </div>
              :<div style={{fontSize:11,color:'#4A5568'}}>—</div>}
          </div>

          <div style={{textAlign:'center'}}>
            {t.last_rssi
              ?<div>
                <div style={{fontSize:12,fontWeight:700,
                  color:t.last_rssi>-50?C.green:t.last_rssi>-70?C.yellow:C.red}}>
                  {t.last_rssi} dBm
                </div>
                <div style={{fontSize:10,color:'#4A5568'}}>{t.hits_5min}/5m</div>
              </div>
              :<span style={{fontSize:11,color:'#4A5568'}}>—</span>}
          </div>

          <div><BatteryBar pct={t.battery_pct}/></div>
          <div><StatusDot secsAgo={t.secs_ago}/></div>

          {/* Assign button */}
          <div>
            <button onClick={()=>setAssignTag(t)}
              style={{width:'100%',padding:'6px 0',borderRadius:8,border:'none',
                background:unassigned?C.orange:C.border,
                color:unassigned?'#fff':C.muted,
                cursor:'pointer',fontSize:11,fontWeight:700}}>
              {unassigned?'⚡ Assign':'✏️ Edit'}
            </button>
          </div>
        </div>;
      })}
    </div>
  </div>;
}
