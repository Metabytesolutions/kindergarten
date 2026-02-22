import { useState, useEffect } from 'react';
const API  = '/api/custody';
const UAPI = '/api/admin/users';
const ZAPI = '/api/admin/zones';
const auth = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
const Card=({children,style={}})=><div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:20,...style}}>{children}</div>;
function Btn({onClick,children,color=C.blue,disabled,small,outline}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',gap:6,opacity:disabled?0.5:1}}>{children}</button>;}

const ZONE_ICONS = {CLASSROOM:'🏫',CORRIDOR:'🚶',ENTRANCE:'🚪',EXIT:'🚨',LOBBY:'🏛️',OUTDOOR:'🌳',NURSE:'🏥',GYM:'🏋️',OFFICE:'💼',HALLWAY:'🚶',CAFETERIA:'🍽️',LIBRARY:'📚'};

function TeacherZoneEditor({token, teacher, onSaved, onCancel}){
  const [zones, setZones]       = useState([]);
  const [assigned, setAssigned] = useState([]);
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState('');

  useEffect(()=>{
    Promise.all([
      fetch(ZAPI, {headers:auth(token)}).then(r=>r.json()),
      fetch(`${API}/teacher-zones/${teacher.id}`, {headers:auth(token)}).then(r=>r.json()),
    ]).then(([z,a])=>{ setZones(z); setAssigned(a.map(x=>({zone_id:x.id, zone_role:x.zone_role}))); });
  },[]);

  const toggle = (zoneId, role) => {
    setAssigned(prev => {
      const exists = prev.find(a=>a.zone_id===zoneId);
      if(exists) {
        if(exists.zone_role===role) return prev.filter(a=>a.zone_id!==zoneId);
        return prev.map(a=>a.zone_id===zoneId?{...a,zone_role:role}:a);
      }
      return [...prev, {zone_id:zoneId, zone_role:role}];
    });
  };

  const save = async () => {
    setSaving(true); setMsg('');
    try {
      // Update teacher primary_zone_id to their PRIMARY zone
      const primary = assigned.find(a=>a.zone_role==='PRIMARY');
      await fetch(`${UAPI}/${teacher.id}`, {method:'PUT', headers:auth(token),
        body: JSON.stringify({primary_zone_id: primary?.zone_id||null})});
      // Save teacher zones
      const r = await fetch(`${API}/teacher-zones/${teacher.id}`, {method:'PUT', headers:auth(token),
        body: JSON.stringify({zones: assigned})});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg('✓ Saved');
      setTimeout(onSaved, 800);
    } catch(e) { setMsg('❌ '+e.message); }
    finally { setSaving(false); }
  };

  const primaryCount   = assigned.filter(a=>a.zone_role==='PRIMARY').length;
  const secondaryCount = assigned.filter(a=>a.zone_role==='SECONDARY').length;

  return <Card style={{marginBottom:20,borderColor:C.blue}}>
    <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:4}}>
      🏫 Zone Assignment — {teacher.full_name||teacher.username}
    </h3>
    <p style={{fontSize:12,color:C.muted,marginTop:0,marginBottom:16}}>
      Set PRIMARY (home classroom) and SECONDARY (can supervise) zones.
      Each teacher should have exactly one PRIMARY zone.
    </p>

    {/* Summary */}
    <div style={{display:'flex',gap:10,marginBottom:16}}>
      {[{label:'Primary',count:primaryCount,color:C.green,warn:primaryCount!==1},
        {label:'Secondary',count:secondaryCount,color:C.blue}].map(({label,count,color,warn})=>(
        <div key={label} style={{flex:1,background:C.dark,border:`1px solid ${warn?C.yellow:C.border}`,borderRadius:8,padding:'8px 12px',textAlign:'center'}}>
          <div style={{fontSize:20,fontWeight:800,color:warn?C.yellow:color}}>{count}</div>
          <div style={{fontSize:11,color:C.muted}}>{label} zone{count!==1?'s':''}</div>
          {warn&&<div style={{fontSize:10,color:C.yellow,marginTop:2}}>⚠ Should be 1</div>}
        </div>
      ))}
    </div>

    {/* Zone grid */}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:8,marginBottom:16}}>
      {zones.map(z=>{
        const a = assigned.find(x=>x.zone_id===z.id);
        const role = a?.zone_role;
        const icon = ZONE_ICONS[z.zone_type]||'📍';
        return <div key={z.id} style={{background:C.dark,border:`2px solid ${role==='PRIMARY'?C.green:role==='SECONDARY'?C.blue:C.border}`,borderRadius:10,padding:12}}>
          <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:8}}>
            <span style={{fontSize:18}}>{icon}</span>
            <div>
              <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>{z.name}</div>
              <div style={{fontSize:10,color:C.muted}}>{z.zone_type}</div>
            </div>
          </div>
          <div style={{display:'flex',gap:6}}>
            <div onClick={()=>toggle(z.id,'PRIMARY')} style={{flex:1,padding:'4px 0',borderRadius:6,textAlign:'center',cursor:'pointer',fontSize:11,fontWeight:700,background:role==='PRIMARY'?C.green:'transparent',color:role==='PRIMARY'?'#fff':C.muted,border:`1px solid ${role==='PRIMARY'?C.green:C.border}`}}>PRIMARY</div>
            <div onClick={()=>toggle(z.id,'SECONDARY')} style={{flex:1,padding:'4px 0',borderRadius:6,textAlign:'center',cursor:'pointer',fontSize:11,fontWeight:700,background:role==='SECONDARY'?C.blue:'transparent',color:role==='SECONDARY'?'#fff':C.muted,border:`1px solid ${role==='SECONDARY'?C.blue:C.border}`}}>2ND</div>
          </div>
        </div>;
      })}
    </div>

    {msg&&<div style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red,marginBottom:10}}>{msg}</div>}
    <div style={{display:'flex',gap:10}}>
      <Btn onClick={save} disabled={saving} color={C.green}>{saving?'⏳ Saving...':'✓ Save Zone Assignment'}</Btn>
      <Btn onClick={onCancel} outline color='#4A5568'>Cancel</Btn>
    </div>
  </Card>;
}

function SettingsPanel({token}){
  const [settings, setSettings] = useState({});
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState('');
  useEffect(()=>{fetch(`${API}/settings`,{headers:auth(token)}).then(r=>r.json()).then(setSettings).catch(()=>{});},[]);
  const save=async()=>{setSaving(true);try{const r=await fetch(`${API}/settings`,{method:'PUT',headers:auth(token),body:JSON.stringify(settings)});const d=await r.json();if(!r.ok)throw new Error(d.error);setMsg('✓ Saved');}catch(e){setMsg('❌ '+e.message);}finally{setSaving(false);}setTimeout(()=>setMsg(''),3000);};
  return <Card style={{marginBottom:20}}>
    <h3 style={{color:C.purple,fontSize:14,marginTop:0,marginBottom:14}}>⚙️ School Settings</h3>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
      {[
        {key:'school_name',label:'School Name',placeholder:'Prosper School'},
        {key:'custody_transfer_timeout_minutes',label:'Transfer Timeout (minutes)',placeholder:'5'},
        {key:'missing_alert_grace_seconds',label:'Missing Alert Grace (seconds)',placeholder:'60'},
        {key:'school_uuid',label:'BLE Beacon UUID',placeholder:'7777772E-...'},
      ].map(({key,label,placeholder})=>(
        <div key={key}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>{label}</div>
          <input value={settings[key]||''} onChange={e=>setSettings(s=>({...s,[key]:e.target.value}))} placeholder={placeholder}
            style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/>
        </div>
      ))}
    </div>
    {msg&&<div style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red,marginTop:10}}>{msg}</div>}
    <div style={{marginTop:14}}><Btn onClick={save} disabled={saving} color={C.purple} small>{saving?'⏳...':'✓ Save Settings'}</Btn></div>
  </Card>;
}

export default function CustodyManager({token}){
  const [teachers, setTeachers] = useState([]);
  const [overview, setOverview] = useState([]);
  const [editing,  setEditing]  = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [view,     setView]     = useState('teachers'); // teachers | overview | settings

  const load = async () => {
    setLoading(true);
    try {
      const [ur, or] = await Promise.all([
        fetch(`${UAPI}?role=TEACHER`, {headers:auth(token)}).then(r=>r.json()),
        fetch(`${API}/overview`, {headers:auth(token)}).then(r=>r.json()),
      ]);
      setTeachers(ur.filter(u=>u.role==='TEACHER'));
      setOverview(or);
    } finally { setLoading(false); }
  };
  useEffect(()=>{ load(); },[]);

  // Group overview by teacher
  const byTeacher = overview.reduce((acc,s)=>{
    const key = s.teacher_name||s.teacher_username||'Unassigned';
    if(!acc[key]) acc[key]={name:key,students:[]};
    acc[key].students.push(s);
    return acc;
  },{});

  return <div>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <div><h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>Chain of Custody</h2>
        <p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>{overview.length} students · {teachers.length} teachers</p></div>
    </div>

    {/* Sub-tabs */}
    <div style={{display:'flex',gap:0,borderBottom:`1px solid ${C.border}`,marginBottom:20}}>
      {[{id:'teachers',label:'👩‍🏫 Teacher Zones'},{id:'overview',label:'📋 Custody Overview'},{id:'settings',label:'⚙️ Settings'}].map(t=>(
        <div key={t.id} onClick={()=>setView(t.id)} style={{padding:'10px 18px',cursor:'pointer',fontSize:13,fontWeight:700,color:view===t.id?C.blue:C.muted,borderBottom:`2px solid ${view===t.id?C.blue:'transparent'}`,transition:'all 0.15s'}}>{t.label}</div>
      ))}
    </div>

    {loading&&<div style={{color:C.muted,fontSize:13}}>Loading...</div>}

    {/* Teacher zones view */}
    {view==='teachers'&&!loading&&<div>
      {editing&&<TeacherZoneEditor token={token} teacher={editing} onSaved={()=>{setEditing(null);load();}} onCancel={()=>setEditing(null)}/>}
      {!editing&&<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))',gap:16}}>
        {teachers.map(t=>{
          const myStudents = overview.filter(s=>s.current_teacher_id===t.id);
          return <Card key={t.id}>
            <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:14}}>
              <div style={{width:44,height:44,borderRadius:10,background:`${C.blue}22`,border:`2px solid ${C.blue}44`,display:'flex',alignItems:'center',justifyContent:'center',fontSize:22}}>👩‍🏫</div>
              <div>
                <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{t.full_name||t.username}</div>
                <div style={{fontSize:11,color:C.muted}}>@{t.username}</div>
              </div>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:14}}>
              {[['Students in custody',myStudents.length],['Zone',t.zone_name||'Not set']].map(([k,v])=>(
                <div key={k} style={{background:C.dark,borderRadius:8,padding:'7px 10px'}}>
                  <div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>{k}</div>
                  <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>{v}</div>
                </div>
              ))}
            </div>
            <Btn small color={C.blue} onClick={()=>setEditing(t)}>🏫 Assign Zones</Btn>
          </Card>;
        })}
      </div>}
    </div>}

    {/* Custody overview */}
    {view==='overview'&&!loading&&<div>
      {Object.entries(byTeacher).map(([name,group])=>(
        <div key={name} style={{marginBottom:24}}>
          <div style={{fontSize:13,fontWeight:700,color:C.blue,marginBottom:10,display:'flex',alignItems:'center',gap:8}}>
            <span>👩‍🏫 {name}</span>
            <span style={{fontSize:11,color:C.muted,fontWeight:400}}>({group.students.length} students)</span>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(220px,1fr))',gap:10}}>
            {group.students.map(s=>{
              const state = s.presence_state||'UNKNOWN';
              const stateColor = {CONFIRMED_PRESENT:C.green,PROBABLE_PRESENT:C.yellow,ROAMING:C.blue,MISSING:'#E74C3C',UNKNOWN:'#4A5568'}[state]||'#4A5568';
              return <div key={s.id} style={{background:C.dark,border:`1px solid ${C.border}`,borderRadius:10,padding:12}}>
                <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:6}}>
                  <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>{s.first_name} {s.last_name}</div>
                  <div style={{fontSize:10,fontWeight:700,color:stateColor,padding:'2px 6px',borderRadius:10,background:`${stateColor}22`}}>{state.replace('_',' ')}</div>
                </div>
                <div style={{fontSize:11,color:C.muted}}>{s.zone_name||'No zone'}</div>
                {s.tag_mac&&<div style={{fontSize:10,color:'#4A5568',fontFamily:'monospace',marginTop:4}}>{s.tag_mac}</div>}
              </div>;
            })}
          </div>
        </div>
      ))}
      {overview.length===0&&<Card style={{textAlign:'center',padding:40}}>
        <div style={{fontSize:40,marginBottom:12}}>📋</div>
        <div style={{fontSize:14,color:C.muted}}>No custody records yet — assign teachers to students first</div>
      </Card>}
    </div>}

    {/* Settings */}
    {view==='settings'&&<SettingsPanel token={token}/>}
  </div>;
}
