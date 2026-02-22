
import { useState, useEffect, useCallback, useRef } from 'react';
const SAPI = '/api/session';
const CAPI = '/api/custody';
const auth = t=>({'Content-Type':'application/json',Authorization:`Bearer ${t}`});
const C={blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',
  orange:'#E67E22',purple:'#8E44AD',teal:'#16A085',
  dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA',navy:'#0D1F3C'};

const SEV_COLOR={CRITICAL:C.red,WARNING:C.orange,INFO:C.green};
const SEV_ICON ={CRITICAL:'🚨',WARNING:'🟠',INFO:'🟢'};

function fmt(ts){
  if(!ts) return '—';
  const d=new Date(ts),now=new Date(),diff=Math.floor((now-d)/1000);
  if(diff<60)   return `${diff}s ago`;
  if(diff<3600) return `${Math.floor(diff/60)}m ago`;
  return d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
}

function Btn({onClick,children,color=C.blue,disabled,small,outline,full,danger}){
  const bg=outline?'transparent':disabled?'#1E3A5F':danger?C.red:color;
  return <button onClick={onClick} disabled={disabled} style={{
    background:bg,color:disabled?'#4A5568':outline?(danger?C.red:color):'#fff',
    border:`1.5px solid ${disabled?'#1E3A5F':danger?C.red:color}`,
    borderRadius:8,padding:small?'6px 12px':'10px 20px',
    fontFamily:'inherit',fontSize:small?11:13,fontWeight:700,
    cursor:disabled?'not-allowed':'pointer',display:'inline-flex',
    alignItems:'center',justifyContent:'center',gap:6,
    opacity:disabled?0.5:1,width:full?'100%':'auto',
    transition:'all 0.15s'}}>{children}</button>;
}

// ── INCOMING TRANSFER BANNER ─────────────────────────────────────────────────
function IncomingBanner({request, token, onResponded}){
  const [secs,setSecs]=useState(request.seconds_remaining||300);
  const [acting,setActing]=useState(null);
  const [msg,setMsg]=useState('');

  useEffect(()=>{
    const iv=setInterval(()=>setSecs(s=>Math.max(0,s-1)),1000);
    return()=>clearInterval(iv);
  },[]);

  const respond=async(action)=>{
    setActing(action);
    try{
      const r=await fetch(`${CAPI}/transfer/${request.transfer_group}/${action}`,
        {method:'POST',headers:auth(token)});
      const d=await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg(action==='accept'?`✓ Accepted ${d.accepted} student(s)`:'Rejected');
      setTimeout(onResponded,1000);
    }catch(e){setMsg('❌ '+e.message);}
    finally{setActing(null);}
  };

  const m=Math.floor(secs/60),s=secs%60;
  const urgColor=secs>120?C.green:secs>30?C.yellow:C.red;

  return <div style={{background:'#0A2010',border:`2px solid ${C.green}`,
    borderRadius:12,padding:'14px 18px',marginBottom:12}}>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
      <div style={{fontSize:15,fontWeight:800,color:C.green}}>🔔 Incoming Custody Request</div>
      <span style={{fontSize:12,fontWeight:700,color:urgColor,padding:'3px 10px',
        borderRadius:10,background:`${urgColor}22`,border:`1px solid ${urgColor}44`}}>
        ⏱ {m}:{String(s).padStart(2,'0')}
      </span>
    </div>
    <div style={{fontSize:13,color:'#E4E4E7',marginBottom:6}}>
      <strong>{request.from_teacher_name}</strong> is sending you students →
      <span style={{color:C.blue,fontWeight:700}}> {request.to_zone_name}</span>
    </div>
    <div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:10}}>
      {(request.students||[]).map(s=><span key={s.id} style={{fontSize:12,
        padding:'3px 10px',borderRadius:14,background:`${C.blue}22`,
        color:'#E4E4E7',border:`1px solid ${C.border}`}}>👤 {s.first_name} {s.last_name}</span>)}
    </div>
    {request.notes&&<div style={{fontSize:11,color:C.muted,marginBottom:8,
      fontStyle:'italic'}}>"{request.notes}"</div>}
    {msg?<div style={{fontSize:12,fontWeight:700,color:msg.startsWith('✓')?C.green:C.red}}>{msg}</div>:
    <div style={{display:'flex',gap:10}}>
      <Btn color={C.green} disabled={!!acting} onClick={()=>respond('accept')}>
        {acting==='accept'?'⏳ Accepting...':'✓ Accept All'}
      </Btn>
      <Btn outline danger disabled={!!acting} onClick={()=>respond('reject')}>
        ✗ Reject
      </Btn>
    </div>}
  </div>;
}

// ── TRANSFER MODAL ───────────────────────────────────────────────────────────
function TransferModal({token, student, onClose, onSent}){
  const [teachers,setTeachers]=useState([]);
  const [selTeacher,setSelTeacher]=useState('');
  const [selZone,setSelZone]=useState('');
  const [notes,setNotes]=useState('');
  const [sending,setSending]=useState(false);
  const [msg,setMsg]=useState('');

  useEffect(()=>{
    fetch(`${CAPI}/teachers-zones`,{headers:auth(token)})
      .then(r=>r.json()).then(setTeachers).catch(()=>{});
  },[]);

  const teacher=teachers.find(t=>t.id===selTeacher);

  const send=async()=>{
    if(!selTeacher||!selZone) return setMsg('Select teacher and zone');
    setSending(true);setMsg('');
    try{
      const r=await fetch(`${CAPI}/transfer`,{method:'POST',headers:auth(token),
        body:JSON.stringify({student_ids:[student.id],to_teacher_id:selTeacher,
          to_zone_id:selZone,notes})});
      const d=await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg('✓ Transfer request sent');
      setTimeout(()=>{onSent&&onSent();onClose();},1000);
    }catch(e){setMsg('❌ '+e.message);}
    finally{setSending(false);}
  };

  return <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.8)',
    display:'flex',alignItems:'center',justifyContent:'center',zIndex:3000,padding:20}}>
    <div style={{background:C.card,border:`2px solid ${C.blue}`,borderRadius:18,
      padding:24,width:'100%',maxWidth:420,maxHeight:'85vh',overflowY:'auto'}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:14}}>
        <div>
          <h3 style={{color:C.blue,fontSize:15,margin:0}}>📤 Transfer Student</h3>
          <div style={{fontSize:12,color:C.muted,marginTop:2}}>
            {student.first_name} {student.last_name}
          </div>
        </div>
        <button onClick={onClose} style={{background:'none',border:'none',
          color:C.muted,fontSize:20,cursor:'pointer'}}>✕</button>
      </div>

      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',
        letterSpacing:'0.06em',marginBottom:8}}>Send To Teacher</div>
      <div style={{display:'flex',flexDirection:'column',gap:6,marginBottom:14}}>
        {teachers.map(t=><div key={t.id} onClick={()=>{setSelTeacher(t.id);setSelZone('');}}
          style={{padding:'10px 12px',borderRadius:10,cursor:'pointer',
            background:selTeacher===t.id?`${C.blue}22`:C.dark,
            border:`2px solid ${selTeacher===t.id?C.blue:C.border}`}}>
          <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>
            {t.full_name||t.username}
            {t.teacher_type==='SUBSTITUTE'&&
              <span style={{fontSize:10,color:C.orange,marginLeft:6}}>🔄 SUB</span>}
          </div>
          <div style={{fontSize:11,color:C.muted}}>{(t.zones||[]).length} zone(s) available</div>
        </div>)}
      </div>

      {selTeacher&&<><div style={{fontSize:11,color:C.muted,fontWeight:600,
        textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Destination Zone</div>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:14}}>
        {(teacher?.zones||[]).map(z=><div key={z.zone_id}
          onClick={()=>setSelZone(z.zone_id)}
          style={{padding:'10px',borderRadius:10,cursor:'pointer',textAlign:'center',
            background:selZone===z.zone_id?`${C.green}22`:C.dark,
            border:`2px solid ${selZone===z.zone_id?C.green:C.border}`}}>
          <div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>{z.zone_name}</div>
          <div style={{fontSize:10,color:z.zone_role==='PRIMARY'?C.green:C.blue,
            fontWeight:700}}>{z.zone_role}</div>
        </div>)}
      </div></>}

      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',
        letterSpacing:'0.06em',marginBottom:4}}>Note (optional)</div>
      <input value={notes} onChange={e=>setNotes(e.target.value)}
        placeholder="e.g. Art class until 11am"
        style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,
          borderRadius:8,padding:'9px 12px',color:'#E4E4E7',fontFamily:'inherit',
          fontSize:13,outline:'none',boxSizing:'border-box',marginBottom:14}}/>

      {msg&&<div style={{fontSize:12,fontWeight:600,marginBottom:10,
        color:msg.startsWith('✓')?C.green:C.red}}>{msg}</div>}
      <Btn onClick={send} disabled={sending||!selTeacher||!selZone} color={C.green} full>
        {sending?'⏳ Sending...':'📤 Send Transfer Request'}
      </Btn>
    </div>
  </div>;
}

// ── STUDENT ROW ──────────────────────────────────────────────────────────────
function StudentRow({student, isOwn, token, onAction}){
  const sess   = student.session_status||'EXPECTED';
  const state  = student.presence_state||'UNKNOWN';
  const isMine = isOwn;

  const statusConfig={
    EXPECTED:         {color:'#4A5568', label:'Expected',         bg:'#1A1A2E'},
    ACCEPTED:         {color:C.green,   label:'Present',          bg:`${C.green}0D`},
    CHECKOUT_PENDING: {color:C.orange,  label:'Checkout Pending', bg:`${C.orange}0D`},
    CHECKED_OUT:      {color:'#374151', label:'Checked Out',      bg:'transparent'},
  };
  const presConfig={
    CONFIRMED_PRESENT:{color:C.green,  dot:'🟢'},
    PROBABLE_PRESENT: {color:C.yellow, dot:'🟡'},
    ROAMING:          {color:C.blue,   dot:'🔵'},
    MISSING:          {color:C.red,    dot:'🔴'},
    UNKNOWN:          {color:'#4A5568',dot:'⚫'},
    EXIT_CONFIRMED:   {color:C.red,    dot:'🚨'},
  };

  const sc = statusConfig[sess]||statusConfig.EXPECTED;
  const pc = presConfig[state]||presConfig.UNKNOWN;
  const battery = student.battery_mv ? Math.min(100,Math.round((student.battery_mv-3000)/12)) : null;
  const batColor = battery>50?C.green:battery>20?C.yellow:C.red;

  return <div style={{display:'flex',alignItems:'center',gap:10,padding:'10px 12px',
    borderRadius:10,background:sc.bg,
    border:`1.5px solid ${sess==='EXPECTED'?C.border:sc.color+'44'}`,
    opacity:sess==='CHECKED_OUT'?0.5:1,marginBottom:6}}>

    {/* Presence dot */}
    <span style={{fontSize:14,flexShrink:0}}>{pc.dot}</span>

    {/* Name + info */}
    <div style={{flex:1,minWidth:0}}>
      <div style={{fontSize:13,fontWeight:800,color:'#E4E4E7',
        display:'flex',alignItems:'center',gap:6}}>
        {student.first_name} {student.last_name}
        {!isMine&&sess==='ACCEPTED'&&
          <span style={{fontSize:10,color:C.teal,fontWeight:700,padding:'1px 6px',
            borderRadius:8,background:`${C.teal}22`,border:`1px solid ${C.teal}44`}}>
            WITH {(student.custody_teacher_name||'').split(' ')[0]?.toUpperCase()}
          </span>}
        {student.transfer_pending_out>0&&
          <span style={{fontSize:10,color:C.orange,fontWeight:700,padding:'1px 6px',
            borderRadius:8,background:`${C.orange}22`}}>TRANSFER PENDING</span>}
      </div>
      <div style={{fontSize:11,color:C.muted,marginTop:1}}>
        {isMine
          ? (student.custody_zone_name||student.home_zone_name||'No zone')
          : `→ ${student.custody_zone_name||'?'}`}
        {student.last_rssi&&` · ${student.last_rssi}dBm`}
        {student.last_seen_at&&` · ${fmt(student.last_seen_at)}`}
      </div>
    </div>

    {/* Battery */}
    {battery!==null&&<div style={{textAlign:'right',flexShrink:0}}>
      <div style={{fontSize:11,fontWeight:700,color:batColor}}>{battery}%</div>
      <div style={{fontSize:9,color:'#4A5568'}}>batt</div>
    </div>}

    {/* Actions */}
    <div style={{flexShrink:0,display:'flex',gap:6}}>
      {sess==='EXPECTED'&&
        <Btn small color={C.green} onClick={()=>onAction('accept',student)}>✓ Accept</Btn>}
      {sess==='ACCEPTED'&&isMine&&parseInt(student.transfer_pending_out||0)===0&&
        <Btn small outline color={C.blue} onClick={()=>onAction('transfer',student)}>📤</Btn>}
      {sess==='ACCEPTED'&&isMine&&parseInt(student.transfer_pending_out||0)===0&&
        <Btn small outline color={C.orange} onClick={()=>onAction('checkout',student)}>🚪</Btn>}
      {sess==='CHECKOUT_PENDING'&&
        <span style={{fontSize:11,color:C.orange,fontWeight:700}}>Awaiting EXIT...</span>}
    </div>
  </div>;
}

// ── ALERT PANEL ──────────────────────────────────────────────────────────────
function AlertPanel({alerts, token, onAck}){
  const [acking,setAcking]=useState(null);
  if(!alerts.length) return <div style={{textAlign:'center',padding:'40px 20px',color:C.muted}}>
    <div style={{fontSize:36,marginBottom:8}}>✅</div>
    <div style={{fontSize:13}}>No alerts</div>
  </div>;

  const ack=async(id)=>{
    setAcking(id);
    try{
      await fetch(`/api/events/${id}/acknowledge`,{method:'POST',headers:auth(token)});
      onAck();
    }catch(e){}finally{setAcking(null);}
  };

  return <div style={{display:'flex',flexDirection:'column',gap:8}}>
    {alerts.map(a=>{
      const color=SEV_COLOR[a.severity]||C.muted;
      const isOpen=a.requires_ack&&!a.acked_at;
      return <div key={a.id} style={{background:isOpen?`${color}0D`:C.dark,
        border:`1.5px solid ${isOpen?color:C.border}`,borderLeft:`4px solid ${color}`,
        borderRadius:10,padding:'10px 12px'}}>
        <div style={{display:'flex',alignItems:'flex-start',gap:6,marginBottom:4}}>
          <span style={{fontSize:14}}>{SEV_ICON[a.severity]}</span>
          <div style={{flex:1}}>
            <div style={{fontSize:11,fontWeight:800,color,textTransform:'uppercase',
              letterSpacing:'0.04em'}}>{a.event_type.replace(/_/g,' ')}</div>
            <div style={{fontSize:12,color:'#E4E4E7',marginTop:2,fontWeight:600}}>
              {a.title}
            </div>
            {(a.students||[]).length>0&&<div style={{display:'flex',gap:4,
              flexWrap:'wrap',marginTop:4}}>
              {a.students.map(s=><span key={s.id} style={{fontSize:10,padding:'1px 6px',
                borderRadius:10,background:`${C.blue}22`,color:C.muted}}>
                {s.first_name} {s.last_name}
              </span>)}
            </div>}
          </div>
          <div style={{fontSize:10,color:'#4A5568',flexShrink:0}}>{fmt(a.created_at)}</div>
        </div>
        {isOpen&&<Btn small color={color} disabled={acking===a.id} onClick={()=>ack(a.id)}>
          {acking===a.id?'⏳':'✓ Ack'}
        </Btn>}
        {a.acked_at&&<div style={{fontSize:10,color:C.green}}>✓ Acknowledged</div>}
      </div>;
    })}
  </div>;
}

// ── MAIN TEACHER VIEW ────────────────────────────────────────────────────────
export default function TeacherView({token}){
  const [data,      setData]      = useState(null);
  const [alerts,    setAlerts]    = useState([]);
  const [incoming,  setIncoming]  = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [acting,    setActing]    = useState(null);
  const [msg,       setMsg]       = useState('');
  const [transfer,  setTransfer]  = useState(null); // student being transferred
  const wsRef = useRef(null);

  const load=useCallback(async()=>{
    try{
      const [roster,alrts,inc]=await Promise.all([
        fetch(`${SAPI}/roster`,{headers:auth(token)}).then(r=>r.json()),
        fetch(`${SAPI}/my-alerts`,{headers:auth(token)}).then(r=>r.json()),
        fetch(`${SAPI}/incoming`,{headers:auth(token)}).then(r=>r.json()),
      ]);
      setData(roster);
      setAlerts(Array.isArray(alrts)?alrts:[]);
      setIncoming(Array.isArray(inc)?inc:[]);
    }catch(e){console.error(e);}
    finally{setLoading(false);}
  },[token]);

  useEffect(()=>{ load(); },[load]);

  // Poll every 15s
  useEffect(()=>{
    const iv=setInterval(load,15000);
    return()=>clearInterval(iv);
  },[load]);

  // WebSocket for real-time CRITICAL push
  useEffect(()=>{
    const proto=window.location.protocol==='https:'?'wss':'ws';
    const ws=new WebSocket(`${proto}://${window.location.host}/ws?token=${token}`);
    wsRef.current=ws;
    ws.onmessage=(msg)=>{
      try{
        const d=JSON.parse(msg.data);
        if(d.type==='DIRECTOR_EVENT'&&d.event?.severity==='CRITICAL'){
          setAlerts(prev=>[d.event,...prev.slice(0,29)]);
        }
      }catch(e){}
    };
    return()=>ws.close();
  },[token]);

  const doAccept=async(student)=>{
    setActing(student.id);setMsg('');
    try{
      const r=await fetch(`${SAPI}/accept/${student.id}`,
        {method:'POST',headers:auth(token)});
      const d=await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg(`✓ ${student.first_name} accepted`);
      await load();
    }catch(e){setMsg('❌ '+e.message);}
    finally{setActing(null);setTimeout(()=>setMsg(''),3000);}
  };

  const doCheckout=async(student)=>{
    if(!window.confirm(`Check out ${student.first_name} ${student.last_name}?\n\nSystem will watch for EXIT zone confirmation.`)) return;
    setActing(student.id);setMsg('');
    try{
      const r=await fetch(`${SAPI}/checkout/${student.id}`,
        {method:'POST',headers:auth(token)});
      const d=await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg(`🚪 ${student.first_name} checkout initiated — watching for EXIT (${d.timeout_minutes}min)`);
      await load();
    }catch(e){setMsg('❌ '+e.message);}
    finally{setActing(null);setTimeout(()=>setMsg(''),6000);}
  };

  const handleAction=(action,student)=>{
    if(action==='accept')   doAccept(student);
    if(action==='checkout') doCheckout(student);
    if(action==='transfer') setTransfer(student);
  };

  if(loading) return <div style={{display:'flex',alignItems:'center',
    justifyContent:'center',height:'100vh',color:C.muted,fontSize:14}}>
    Loading your classroom...</div>;

  const teacher    = data?.teacher||{};
  const students   = data?.students||[];
  const summary    = data?.summary||{};
  const myStudents = students.filter(s=>s.current_teacher_id===teacher.id||s.session_status==='EXPECTED');
  const elsewhere  = students.filter(s=>s.current_teacher_id!==teacher.id&&s.session_status==='ACCEPTED');
  const critAlerts = alerts.filter(a=>a.severity==='CRITICAL'&&!a.acked_at).length;

  return <div style={{height:'100vh',display:'flex',flexDirection:'column',
    background:C.dark,overflow:'hidden'}}>

    {/* Header */}
    <div style={{background:C.navy,borderBottom:`1px solid ${C.border}`,
      padding:'10px 20px',display:'flex',alignItems:'center',
      justifyContent:'space-between',flexShrink:0}}>
      <div style={{display:'flex',alignItems:'center',gap:12}}>
        <div style={{fontSize:24}}>👩‍🏫</div>
        <div>
          <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>
            {teacher.full_name||teacher.username}
          </div>
          <div style={{fontSize:11,color:C.muted}}>
            {teacher.zone_name||'No zone'} ·
            <span style={{color:C.green,fontWeight:700}}> {summary.in_custody||0} in custody</span>
            {summary.expected>0&&<span style={{color:C.yellow,fontWeight:700}}> · {summary.expected} expected</span>}
            {summary.missing>0&&<span style={{color:C.red,fontWeight:700}}> · {summary.missing} MISSING</span>}
          </div>
        </div>
      </div>
      {msg&&<div style={{fontSize:12,fontWeight:700,padding:'6px 14px',borderRadius:8,
        background:msg.startsWith('✓')||msg.startsWith('🚪')?`${C.green}22`:`${C.red}22`,
        color:msg.startsWith('✓')||msg.startsWith('🚪')?C.green:C.red,
        border:`1px solid ${msg.startsWith('✓')||msg.startsWith('🚪')?C.green:C.red}44`}}>
        {msg}
      </div>}
      <div style={{fontSize:11,color:C.muted,textAlign:'right'}}>
        <div style={{color:'#27AE60',fontWeight:700}}>● Live</div>
        <div>{new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</div>
      </div>
    </div>

    {/* Incoming transfer banners */}
    {incoming.length>0&&<div style={{padding:'10px 20px 0',flexShrink:0}}>
      {incoming.map(r=><IncomingBanner key={r.transfer_group} request={r}
        token={token} onResponded={load}/>)}
    </div>}

    {/* Transfer modal */}
    {transfer&&<TransferModal token={token} student={transfer}
      onClose={()=>setTransfer(null)} onSent={load}/>}

    {/* Main split layout */}
    <div style={{flex:1,display:'flex',overflow:'hidden',gap:0}}>

      {/* LEFT — Students */}
      <div style={{flex:'0 0 58%',borderRight:`1px solid ${C.border}`,
        overflowY:'auto',padding:'16px 20px'}}>

        {/* My custody */}
        <div style={{fontSize:12,fontWeight:700,color:C.blue,textTransform:'uppercase',
          letterSpacing:'0.06em',marginBottom:10}}>
          In My Custody ({myStudents.filter(s=>s.session_status!=='EXPECTED'&&s.current_teacher_id===teacher.id).length})
        </div>
        {myStudents.filter(s=>s.session_status!=='EXPECTED'&&s.current_teacher_id===teacher.id)
          .map(s=><StudentRow key={s.id} student={s} isOwn={true}
            token={token} onAction={handleAction}/>)}

        {/* Expected (not yet accepted) */}
        {myStudents.filter(s=>s.session_status==='EXPECTED').length>0&&<>
          <div style={{fontSize:12,fontWeight:700,color:C.yellow,textTransform:'uppercase',
            letterSpacing:'0.06em',margin:'16px 0 10px'}}>
            Awaiting Acceptance ({myStudents.filter(s=>s.session_status==='EXPECTED').length})
          </div>
          {myStudents.filter(s=>s.session_status==='EXPECTED')
            .map(s=><StudentRow key={s.id} student={s} isOwn={true}
              token={token} onAction={handleAction}/>)}
        </>}

        {/* My students with other teachers */}
        {elsewhere.length>0&&<>
          <div style={{fontSize:12,fontWeight:700,color:C.teal,textTransform:'uppercase',
            letterSpacing:'0.06em',margin:'16px 0 10px'}}>
            My Students Elsewhere ({elsewhere.length})
          </div>
          <div style={{fontSize:11,color:C.muted,marginBottom:8}}>
            You accepted these students this morning — still tracking
          </div>
          {elsewhere.map(s=><StudentRow key={s.id} student={s} isOwn={false}
            token={token} onAction={handleAction}/>)}
        </>}

        {students.length===0&&<div style={{textAlign:'center',padding:'60px 20px',color:C.muted}}>
          <div style={{fontSize:48,marginBottom:12}}>👶</div>
          <div style={{fontSize:14}}>No students assigned to your class</div>
        </div>}
      </div>

      {/* RIGHT — Alerts */}
      <div style={{flex:'0 0 42%',overflowY:'auto',padding:'16px 20px'}}>
        <div style={{fontSize:12,fontWeight:700,textTransform:'uppercase',
          letterSpacing:'0.06em',marginBottom:12,display:'flex',
          alignItems:'center',gap:8}}>
          <span style={{color:critAlerts>0?C.red:C.muted}}>
            {critAlerts>0?'🚨':'📋'} Alerts
          </span>
          {critAlerts>0&&<span style={{fontSize:11,fontWeight:700,color:C.red,
            padding:'2px 8px',borderRadius:10,background:`${C.red}22`}}>
            {critAlerts} critical
          </span>}
          <span style={{marginLeft:'auto',fontSize:11,color:C.muted}}>
            ({alerts.length} today)
          </span>
        </div>
        <AlertPanel alerts={alerts} token={token} onAck={load}/>
      </div>
    </div>
  </div>;
}
