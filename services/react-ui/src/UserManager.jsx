import { useState, useEffect } from 'react';
const API  = '/api/admin/users';
const ZAPI = '/api/admin/zones';
const auth = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
const ROLES = { IT:{icon:'🔧',color:'#8E44AD',label:'IT Admin'}, TEACHER:{icon:'👩‍🏫',color:'#2E86AB',label:'Teacher'}, DIRECTOR:{icon:'👔',color:'#F39C12',label:'Director'} };
const Card=({children,style={}})=><div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:20,...style}}>{children}</div>;
function Btn({onClick,children,color=C.blue,disabled,small,outline}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',gap:6,opacity:disabled?0.5:1}}>{children}</button>;}
function Fld({label,value,onChange,placeholder,type='text',mono}){return <div style={{marginBottom:12}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>{label}</div><input type={type} value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 12px',color:'#E4E4E7',fontFamily:mono?'monospace':'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/></div>;}
function Sel({label,value,onChange,options}){return <div style={{marginBottom:12}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>{label}</div><select value={value} onChange={e=>onChange(e.target.value)} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none'}}><option value="">— Select —</option>{options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></div>;}

function UserForm({token,user,onSaved,onCancel}){
  const [username,setUsername]=useState(user?.username||'');
  const [fullName,setFullName]=useState(user?.full_name||'');
  const [email,setEmail]=useState(user?.email||'');
  const [phone,setPhone]=useState(user?.phone||'');
  const [role,setRole]=useState(user?.role||'TEACHER');
  const [zoneId,setZoneId]=useState(user?.zone_id||'');
  const [password,setPassword]=useState('');
  const [zones,setZones]=useState([]);
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');
  const isEdit=!!user;
  useEffect(()=>{fetch(ZAPI,{headers:auth(token)}).then(r=>r.json()).then(setZones).catch(()=>{});},[]);
  const save=async()=>{
    if(!isEdit&&!password)return setErr('Password required for new user');
    if(!isEdit&&password.length<8)return setErr('Password must be 8+ characters');
    setSaving(true);setErr('');
    try{
      const body=isEdit
        ?{full_name:fullName,email,phone,role,zone_id:zoneId||null}
        :{username,email,password,role,full_name:fullName,phone,zone_id:zoneId||null};
      const r=await fetch(isEdit?`${API}/${user.id}`:API,{method:isEdit?'PUT':'POST',headers:auth(token),body:JSON.stringify(body)});
      const d=await r.json();
      if(!r.ok)throw new Error(d.error);
      onSaved(d);
    }catch(e){setErr(e.message);}
    finally{setSaving(false);}
  };
  return <Card style={{marginBottom:20,borderColor:C.blue}}>
    <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:16}}>{isEdit?`✏️ Edit — ${user.username}`:'➕ Create New User'}</h3>
    <div style={{marginBottom:14}}>
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Role</div>
      <div style={{display:'flex',gap:10}}>
        {Object.entries(ROLES).map(([k,v])=>(
          <div key={k} onClick={()=>setRole(k)} style={{flex:1,background:role===k?'#0D2137':C.dark,border:`2px solid ${role===k?v.color:C.border}`,borderRadius:10,padding:'12px 8px',cursor:'pointer',textAlign:'center'}}>
            <div style={{fontSize:24,marginBottom:4}}>{v.icon}</div>
            <div style={{fontSize:12,fontWeight:700,color:role===k?v.color:'#E4E4E7'}}>{v.label}</div>
          </div>
        ))}
      </div>
    </div>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
      {!isEdit&&<Fld label="Username" value={username} onChange={setUsername} placeholder="teacher02"/>}
      <Fld label="Full Name" value={fullName} onChange={setFullName} placeholder="Jane Smith"/>
      <Fld label="Email" value={email} onChange={setEmail} placeholder="jane@school.edu" type="email"/>
      <Fld label="Phone (optional)" value={phone} onChange={setPhone} placeholder="+1 555 0100"/>
      {!isEdit&&<Fld label="Password" value={password} onChange={setPassword} placeholder="Min 8 chars" type="password"/>}
    </div>
    <Sel label="Assign to Zone (optional)" value={zoneId} onChange={setZoneId} options={zones.map(z=>({value:z.id,label:`${z.name} (${z.zone_type})`}))}/>
    {err&&<div style={{color:C.red,fontSize:12,marginBottom:10}}>❌ {err}</div>}
    <div style={{display:'flex',gap:10}}>
      <Btn onClick={save} disabled={saving} color={C.green}>{saving?'⏳ Saving...':isEdit?'✓ Save Changes':'✓ Create User'}</Btn>
      <Btn onClick={onCancel} outline color='#4A5568'>Cancel</Btn>
    </div>
  </Card>;
}

function ResetModal({token,user,onClose}){
  const [pw,setPw]=useState('');const [saving,setSaving]=useState(false);const [msg,setMsg]=useState('');
  const reset=async()=>{
    if(pw.length<8)return setMsg('Min 8 characters');
    setSaving(true);
    try{const r=await fetch(`${API}/${user.id}/reset-password`,{method:'POST',headers:auth(token),body:JSON.stringify({password:pw})});
    const d=await r.json();if(!r.ok)throw new Error(d.error);setMsg('✓ Password reset');setTimeout(onClose,1200);}
    catch(e){setMsg('❌ '+e.message);}finally{setSaving(false);}
  };
  return <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.7)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:1000}}>
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:16,padding:28,width:380}}>
      <h3 style={{color:C.yellow,fontSize:15,marginTop:0,marginBottom:16}}>🔑 Reset Password — {user.username}</h3>
      <Fld label="New Password" value={pw} onChange={setPw} placeholder="Min 8 characters" type="password"/>
      {msg&&<div style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red,marginBottom:10}}>{msg}</div>}
      <div style={{display:'flex',gap:10}}>
        <Btn onClick={reset} disabled={saving} color={C.yellow}>{saving?'⏳...':'🔑 Reset'}</Btn>
        <Btn onClick={onClose} outline color='#4A5568'>Cancel</Btn>
      </div>
    </div>
  </div>;
}

export default function UserManager({token}){
  const [users,setUsers]=useState([]);const [loading,setLoading]=useState(true);const [showForm,setShowForm]=useState(false);const [editing,setEditing]=useState(null);const [resetting,setResetting]=useState(null);const [deactivating,setDeactivating]=useState(null);const [msg,setMsg]=useState('');const [filter,setFilter]=useState('ALL');
  const load=async()=>{setLoading(true);try{const r=await fetch(API,{headers:auth(token)});setUsers(await r.json());}finally{setLoading(false);}};
  useEffect(()=>{load();},[]);
  const deactivate=async id=>{try{const r=await fetch(`${API}/${id}`,{method:'DELETE',headers:auth(token)});const d=await r.json();if(!r.ok)throw new Error(d.error);setMsg('✓ User deactivated');load();}catch(e){setMsg('❌ '+e.message);}setDeactivating(null);setTimeout(()=>setMsg(''),4000);};
  const onSaved=()=>{setShowForm(false);setEditing(null);load();};
  const filtered=filter==='ALL'?users:users.filter(u=>u.role===filter);
  return <div>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <div><h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>User Management</h2><p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>{users.length} user{users.length!==1?'s':''} · {users.filter(u=>u.is_active).length} active</p></div>
      {!showForm&&!editing&&<Btn onClick={()=>setShowForm(true)} color={C.blue}>+ Create User</Btn>}
    </div>
    {msg&&<div style={{marginBottom:14,padding:'10px 14px',borderRadius:8,fontSize:13,fontWeight:600,background:msg.startsWith('✓')?'#0D2B1A':'#2B0D0D',border:`1px solid ${msg.startsWith('✓')?C.green:C.red}`,color:msg.startsWith('✓')?C.green:C.red}}>{msg}</div>}
    {showForm&&<UserForm token={token} onSaved={onSaved} onCancel={()=>setShowForm(false)}/>}
    {editing&&<UserForm token={token} user={editing} onSaved={onSaved} onCancel={()=>setEditing(null)}/>}
    {resetting&&<ResetModal token={token} user={resetting} onClose={()=>{setResetting(null);load();}}/>}
    <div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:20}}>
      {['ALL','IT','TEACHER','DIRECTOR'].map(f=>{const meta=ROLES[f];return(
        <div key={f} onClick={()=>setFilter(f)} style={{padding:'6px 14px',borderRadius:20,cursor:'pointer',fontSize:12,fontWeight:700,background:filter===f?(meta?.color||C.blue):'transparent',border:`1.5px solid ${filter===f?(meta?.color||C.blue):C.border}`,color:filter===f?'#fff':C.muted}}>
          {f==='ALL'?'All Users':`${meta?.icon} ${meta?.label}`}
        </div>
      );})}
    </div>
    {loading&&<div style={{color:C.muted,fontSize:13}}>Loading...</div>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(320px,1fr))',gap:16}}>
      {filtered.map(u=>{const meta=ROLES[u.role]||{icon:'👤',color:C.blue};const isDel=deactivating===u.id;return(
        <Card key={u.id} style={{opacity:u.is_active?1:0.5}}>
          <div style={{display:'flex',alignItems:'center',gap:14,marginBottom:14}}>
            <div style={{width:48,height:48,borderRadius:12,background:`${meta.color}22`,border:`2px solid ${meta.color}44`,display:'flex',alignItems:'center',justifyContent:'center',fontSize:24}}>{meta.icon}</div>
            <div style={{flex:1}}>
              <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{u.full_name||u.username}</div>
              <div style={{fontSize:11,color:C.muted,fontFamily:'monospace'}}>@{u.username}</div>
              <div style={{display:'flex',alignItems:'center',gap:6,marginTop:4}}>
                <span style={{fontSize:11,fontWeight:700,padding:'2px 8px',borderRadius:20,background:`${meta.color}22`,color:meta.color,border:`1px solid ${meta.color}44`}}>{meta.icon} {meta.label}</span>
                {!u.is_active&&<span style={{fontSize:11,color:C.red,fontWeight:700}}>● INACTIVE</span>}
              </div>
            </div>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:14}}>
            {[['Email',u.email],['Phone',u.phone||'—'],['Zone',u.zone_name||'Unassigned'],['Last Login',u.last_login_at?new Date(u.last_login_at).toLocaleDateString():'Never']].map(([k,v])=>(
              <div key={k} style={{background:C.dark,borderRadius:8,padding:'7px 10px'}}><div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>{k}</div><div style={{fontSize:12,color:'#E4E4E7',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{v}</div></div>
            ))}
          </div>
          {isDel?<div style={{background:'#2B0D0D',border:`1px solid ${C.red}44`,borderRadius:8,padding:12,marginBottom:10}}>
            <div style={{fontSize:12,color:C.red,fontWeight:700,marginBottom:8}}>Deactivate this user?</div>
            <div style={{display:'flex',gap:8}}><Btn small color={C.red} onClick={()=>deactivate(u.id)}>Yes</Btn><Btn small outline color='#4A5568' onClick={()=>setDeactivating(null)}>Cancel</Btn></div>
          </div>:<div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
            <Btn small outline color={C.blue} onClick={()=>{setEditing(u);setShowForm(false);}}>✏️ Edit</Btn>
            <Btn small outline color={C.yellow} onClick={()=>setResetting(u)}>🔑 Reset PW</Btn>
            {u.is_active&&<Btn small outline color={C.red} onClick={()=>setDeactivating(u.id)}>⊘ Deactivate</Btn>}
          </div>}
        </Card>
      );})}
    </div>
  </div>;
}
