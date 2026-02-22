import { useState, useEffect, useCallback } from 'react';
const API  = '/api/custody';
const auth = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
function Btn({onClick,children,color=C.blue,disabled,small,outline,full}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',justifyContent:'center',gap:6,opacity:disabled?0.5:1,width:full?'100%':'auto'}}>{children}</button>;}

function CountdownBadge({expiresAt}){
  const [secs,setSecs]=useState(0);
  useEffect(()=>{
    const tick=()=>setSecs(Math.max(0,Math.round((new Date(expiresAt)-Date.now())/1000)));
    tick(); const iv=setInterval(tick,1000); return ()=>clearInterval(iv);
  },[expiresAt]);
  const color=secs>120?C.green:secs>30?C.yellow:C.red;
  const m=Math.floor(secs/60), s=secs%60;
  return <span style={{fontSize:11,fontWeight:700,color,padding:'2px 8px',borderRadius:10,background:`${color}22`,border:`1px solid ${color}44`}}>⏱ {m}:{String(s).padStart(2,'0')}</span>;
}

// ── INCOMING REQUESTS BANNER ──────────────────────────────────────────────────
export function IncomingCustodyBanner({token, onAccepted}){
  const [incoming, setIncoming] = useState([]);
  const [acting, setActing]     = useState(null);
  const [msg, setMsg]           = useState('');

  const poll = useCallback(async()=>{
    try {
      const r = await fetch(`${API}/incoming`,{headers:auth(token)});
      const d = await r.json();
      // Group by transfer_group
      const groups = d.reduce((acc,t)=>{
        if(!acc[t.transfer_group]) acc[t.transfer_group]={
          group:t.transfer_group, from:t.from_teacher_name||t.from_teacher_username,
          zone:t.to_zone_name, expires:t.expires_at, students:[]};
        acc[t.transfer_group].students.push({id:t.student_id,name:`${t.first_name} ${t.last_name}`});
        return acc;
      },{});
      setIncoming(Object.values(groups));
    } catch(e){}
  },[token]);

  useEffect(()=>{ poll(); const iv=setInterval(poll,8000); return ()=>clearInterval(iv); },[poll]);

  const respond = async(group, action) => {
    setActing(group);
    try {
      const r = await fetch(`${API}/transfer/${group}/${action}`,{method:'POST',headers:auth(token)});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg(action==='accept'?`✓ Accepted ${d.accepted} student(s)`:`✓ Rejected`);
      poll(); if(onAccepted) onAccepted();
    } catch(e){setMsg('❌ '+e.message);}
    finally{setActing(null); setTimeout(()=>setMsg(''),4000);}
  };

  if(!incoming.length) return null;

  return <div style={{marginBottom:20}}>
    {msg&&<div style={{padding:'10px 14px',borderRadius:8,fontSize:13,fontWeight:600,background:msg.startsWith('✓')?'#0D2B1A':'#2B0D0D',border:`1px solid ${msg.startsWith('✓')?C.green:C.red}`,color:msg.startsWith('✓')?C.green:C.red,marginBottom:10}}>{msg}</div>}
    {incoming.map(g=>(
      <div key={g.group} style={{background:'#0D1F0D',border:`2px solid ${C.green}`,borderRadius:14,padding:16,marginBottom:12}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10}}>
          <div style={{fontSize:14,fontWeight:800,color:C.green}}>🔔 Incoming Custody Request</div>
          <CountdownBadge expiresAt={g.expires}/>
        </div>
        <div style={{fontSize:13,color:'#E4E4E7',marginBottom:8}}>
          <span style={{color:C.muted}}>From:</span> <strong>{g.from}</strong>
          {' → '}
          <span style={{color:C.blue}}>🏫 {g.zone}</span>
        </div>
        <div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:12}}>
          {g.students.map(s=>(
            <span key={s.id} style={{fontSize:12,padding:'3px 10px',borderRadius:20,background:`${C.blue}22`,color:'#E4E4E7',border:`1px solid ${C.border}`}}>👤 {s.name}</span>
          ))}
        </div>
        <div style={{fontSize:12,color:C.muted,marginBottom:10}}>{g.students.length} student{g.students.length!==1?'s':''} — you will become their custodian</div>
        <div style={{display:'flex',gap:10}}>
          <Btn color={C.green} disabled={acting===g.group} onClick={()=>respond(g.group,'accept')}>✓ Accept All ({g.students.length})</Btn>
          <Btn outline color={C.red} disabled={acting===g.group} onClick={()=>respond(g.group,'reject')}>✗ Reject</Btn>
        </div>
      </div>
    ))}
  </div>;
}

// ── SEND TRANSFER MODAL ───────────────────────────────────────────────────────
export function TransferModal({token, students, onClose, onSent}){
  const [teachersZones, setTeachersZones] = useState([]);
  const [selectedTeacher, setSelectedTeacher] = useState('');
  const [selectedZone, setSelectedZone]     = useState('');
  const [selectedStudents, setSelectedStudents] = useState(students.map(s=>s.id));
  const [notes, setNotes]   = useState('');
  const [sending, setSending] = useState(false);
  const [msg, setMsg]         = useState('');

  useEffect(()=>{
    fetch(`${API}/teachers-zones`,{headers:auth(token)}).then(r=>r.json()).then(setTeachersZones).catch(()=>{});
  },[]);

  const teacher   = teachersZones.find(t=>t.id===selectedTeacher);
  const availZones = teacher?.zones||[];

  const toggleStudent = id => setSelectedStudents(p=>p.includes(id)?p.filter(x=>x!==id):[...p,id]);

  const send = async () => {
    if(!selectedStudents.length) return setMsg('Select at least one student');
    if(!selectedTeacher||!selectedZone) return setMsg('Select teacher and destination zone');
    setSending(true); setMsg('');
    try {
      const r = await fetch(`${API}/transfer`,{method:'POST',headers:auth(token),
        body:JSON.stringify({student_ids:selectedStudents,to_teacher_id:selectedTeacher,to_zone_id:selectedZone,notes})});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg(`✓ Transfer request sent for ${d.count} student(s)`);
      setTimeout(()=>{onSent&&onSent();onClose();},1500);
    } catch(e){setMsg('❌ '+e.message);}
    finally{setSending(false);}
  };

  return <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.75)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:2000,padding:20}}>
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:18,padding:24,width:'100%',maxWidth:480,maxHeight:'90vh',overflowY:'auto'}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:16}}>
        <h3 style={{color:C.blue,fontSize:16,margin:0}}>📤 Transfer Students</h3>
        <button onClick={onClose} style={{background:'none',border:'none',color:C.muted,fontSize:20,cursor:'pointer'}}>✕</button>
      </div>

      {/* Student selector */}
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Select Students</div>
      <div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:16}}>
        {students.map(s=>{
          const sel=selectedStudents.includes(s.id);
          return <div key={s.id} onClick={()=>toggleStudent(s.id)}
            style={{padding:'6px 12px',borderRadius:20,cursor:'pointer',fontSize:12,fontWeight:600,
              background:sel?`${C.blue}33`:'transparent',color:sel?'#E4E4E7':C.muted,
              border:`1.5px solid ${sel?C.blue:C.border}`}}>
            {sel?'✓ ':''}{s.first_name} {s.last_name}
          </div>;
        })}
      </div>

      {/* Teacher selector */}
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Send To Teacher</div>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:16}}>
        {teachersZones.map(t=>(
          <div key={t.id} onClick={()=>{setSelectedTeacher(t.id);setSelectedZone('');}}
            style={{background:selectedTeacher===t.id?`${C.blue}22`:C.dark,border:`2px solid ${selectedTeacher===t.id?C.blue:C.border}`,borderRadius:10,padding:12,cursor:'pointer'}}>
            <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>{t.full_name||t.username}</div>
            <div style={{fontSize:11,color:C.muted,marginTop:2}}>{t.zones?.length||0} zone(s)</div>
          </div>
        ))}
        {teachersZones.length===0&&<div style={{fontSize:12,color:C.muted,gridColumn:'span 2'}}>No other teachers available</div>}
      </div>

      {/* Zone selector — only shows selected teacher zones */}
      {selectedTeacher&&<>
        <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Destination Zone</div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:16}}>
          {availZones.map(z=>(
            <div key={z.zone_id} onClick={()=>setSelectedZone(z.zone_id)}
              style={{background:selectedZone===z.zone_id?`${C.green}22`:C.dark,border:`2px solid ${selectedZone===z.zone_id?C.green:C.border}`,borderRadius:10,padding:10,cursor:'pointer'}}>
              <div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>{z.zone_name}</div>
              <div style={{fontSize:10,color:z.zone_role==='PRIMARY'?C.green:C.blue,marginTop:2,fontWeight:600}}>{z.zone_role}</div>
            </div>
          ))}
        </div>
      </>}

      {/* Notes */}
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>Notes (optional)</div>
      <input value={notes} onChange={e=>setNotes(e.target.value)} placeholder="e.g. Art class until 11am"
        style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none',boxSizing:'border-box',marginBottom:16}}/>

      {msg&&<div style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red,marginBottom:12,fontWeight:600}}>{msg}</div>}
      <div style={{display:'flex',gap:10}}>
        <Btn onClick={send} disabled={sending||!selectedStudents.length||!selectedTeacher||!selectedZone} color={C.green} full>
          {sending?'⏳ Sending...':selectedStudents.length?`📤 Send ${selectedStudents.length} Student(s)`:'Select Students'}
        </Btn>
      </div>
    </div>
  </div>;
}
