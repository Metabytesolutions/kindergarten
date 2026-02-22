import { useState, useEffect } from 'react';
const API   = '/api/admin/zones';
const GW_API= '/api/admin/gateways';
const auth  = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
const TM = {
  CLASSROOM:{icon:'🏫',color:'#2E86AB'},CORRIDOR:{icon:'🚶',color:'#8E44AD'},
  ENTRANCE:{icon:'🚪',color:'#27AE60'},EXIT:{icon:'🚨',color:'#C0392B'},
  LOBBY:{icon:'🏛️',color:'#F39C12'},OUTDOOR:{icon:'🌳',color:'#27AE60'},
  NURSE:{icon:'🏥',color:'#E74C3C'},GYM:{icon:'🏋️',color:'#E67E22'},
  OFFICE:{icon:'💼',color:'#7F8C8D'},HALLWAY:{icon:'🚶',color:'#9B59B6'},
  CAFETERIA:{icon:'🍽️',color:'#E67E22'},LIBRARY:{icon:'📚',color:'#2980B9'},
};
const Card=({children,style={}})=><div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:20,...style}}>{children}</div>;
function Btn({onClick,children,color=C.blue,disabled,small,outline}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',gap:6,opacity:disabled?0.5:1}}>{children}</button>;}
function Fld({label,value,onChange,placeholder}){return <div style={{marginBottom:14}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:5}}>{label}</div><input value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'10px 14px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/></div>;}

function ZoneForm({token,zone,onSaved,onCancel}){
  const [name,setName]=useState(zone?.name||'');
  const [type,setType]=useState(zone?.zone_type||'CLASSROOM');
  const [desc,setDesc]=useState(zone?.description||'');
  const [floor,setFloor]=useState(zone?.floor||'1');
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');
  const isEdit=!!zone;
  const save=async()=>{
    if(!name.trim())return setErr('Name is required');
    setSaving(true);setErr('');
    try{
      const r=await fetch(isEdit?`${API}/${zone.id}`:API,{method:isEdit?'PUT':'POST',headers:auth(token),body:JSON.stringify({name:name.trim(),zone_type:type,description:desc,floor})});
      const d=await r.json();
      if(!r.ok)throw new Error(d.error);
      onSaved(d);
    }catch(e){setErr(e.message);}
    finally{setSaving(false);}
  };
  return <Card style={{marginBottom:20,borderColor:C.blue}}>
    <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:16}}>{isEdit?`✏️ Edit — ${zone.name}`:'➕ Create New Zone'}</h3>
    <div style={{marginBottom:16}}>
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Zone Type</div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:8}}>
        {Object.entries(TM).map(([k,v])=>(
          <div key={k} onClick={()=>setType(k)} style={{background:type===k?'#0D2137':C.dark,border:`2px solid ${type===k?v.color:C.border}`,borderRadius:10,padding:'10px 8px',cursor:'pointer',textAlign:'center',transition:'all 0.15s'}}>
            <div style={{fontSize:22,marginBottom:4}}>{v.icon}</div>
            <div style={{fontSize:11,fontWeight:700,color:type===k?v.color:'#E4E4E7'}}>{k.charAt(0)+k.slice(1).toLowerCase()}</div>
          </div>
        ))}
      </div>
    </div>
    <div style={{display:'grid',gridTemplateColumns:'2fr 1fr',gap:14}}>
      <Fld label="Zone Name" value={name} onChange={setName} placeholder="e.g. Classroom B"/>
      <Fld label="Floor" value={floor} onChange={setFloor} placeholder="1"/>
    </div>
    <Fld label="Description (optional)" value={desc} onChange={setDesc} placeholder="Brief description"/>
    <div style={{background:C.dark,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      <div style={{fontSize:11,color:C.muted,marginBottom:6}}>PREVIEW</div>
      <div style={{display:'flex',alignItems:'center',gap:10}}>
        <span style={{fontSize:28}}>{TM[type]?.icon||'📍'}</span>
        <div>
          <div style={{fontSize:14,fontWeight:700,color:'#E4E4E7'}}>{name||'Zone Name'}</div>
          <div style={{fontSize:11,color:TM[type]?.color||C.blue}}>{type} · Floor {floor}</div>
          {desc&&<div style={{fontSize:11,color:C.muted,marginTop:2}}>{desc}</div>}
        </div>
      </div>
    </div>
    {err&&<div style={{color:C.red,fontSize:12,marginBottom:10}}>❌ {err}</div>}
    <div style={{display:'flex',gap:10}}>
      <Btn onClick={save} disabled={saving} color={C.green}>{saving?'⏳ Saving...':isEdit?'✓ Save Changes':'✓ Create Zone'}</Btn>
      <Btn onClick={onCancel} outline color='#4A5568'>Cancel</Btn>
    </div>
  </Card>;
}

export default function ZoneManager({token}){
  const [zones,setZones]=useState([]);const [gws,setGws]=useState([]);const [loading,setLoading]=useState(true);const [showForm,setShowForm]=useState(false);const [editing,setEditing]=useState(null);const [deleting,setDeleting]=useState(null);const [msg,setMsg]=useState('');const [filter,setFilter]=useState('ALL');
  const load=async()=>{setLoading(true);try{const[zr,gr]=await Promise.all([fetch(API,{headers:auth(token)}).then(r=>r.json()),fetch(GW_API,{headers:auth(token)}).then(r=>r.json())]);setZones(zr);setGws(gr);}finally{setLoading(false);}};
  useEffect(()=>{load();},[]);
  const del=async id=>{try{const r=await fetch(`${API}/${id}`,{method:'DELETE',headers:auth(token)});const d=await r.json();if(!r.ok)throw new Error(d.error);setMsg('✓ Zone deleted');setDeleting(null);load();}catch(e){setMsg('❌ '+e.message);setDeleting(null);}setTimeout(()=>setMsg(''),4000);};
  const onSaved=()=>{setShowForm(false);setEditing(null);load();};
  const types=['ALL',...new Set(zones.map(z=>z.zone_type))];
  const filtered=filter==='ALL'?zones:zones.filter(z=>z.zone_type===filter);
  const gwCount=id=>gws.filter(g=>g.zone_id===id).length;
  const gwList=id=>gws.filter(g=>g.zone_id===id).map(g=>g.label).join(', ');
  return <div>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <div><h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>Zone Management</h2><p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>{zones.length} zone{zones.length!==1?'s':''} · {gws.length} gateways</p></div>
      {!showForm&&!editing&&<Btn onClick={()=>setShowForm(true)} color={C.blue}>+ Create Zone</Btn>}
    </div>
    {msg&&<div style={{marginBottom:14,padding:'10px 14px',borderRadius:8,fontSize:13,fontWeight:600,background:msg.startsWith('✓')?'#0D2B1A':'#2B0D0D',border:`1px solid ${msg.startsWith('✓')?C.green:C.red}`,color:msg.startsWith('✓')?C.green:C.red}}>{msg}</div>}
    {showForm&&<ZoneForm token={token} onSaved={onSaved} onCancel={()=>setShowForm(false)}/>}
    {editing&&<ZoneForm token={token} zone={editing} onSaved={onSaved} onCancel={()=>setEditing(null)}/>}
    {zones.length>0&&<div style={{display:'flex',gap:6,flexWrap:'wrap',marginBottom:20}}>
      {types.map(t=><div key={t} onClick={()=>setFilter(t)} style={{padding:'6px 14px',borderRadius:20,cursor:'pointer',fontSize:12,fontWeight:700,background:filter===t?(TM[t]?.color||C.blue):'transparent',border:`1.5px solid ${filter===t?(TM[t]?.color||C.blue):C.border}`,color:filter===t?'#fff':C.muted,transition:'all 0.15s'}}>{t==='ALL'?'All Zones':`${TM[t]?.icon||''} ${t.charAt(0)+t.slice(1).toLowerCase()}`}</div>)}
    </div>}
    {loading&&<div style={{color:C.muted,fontSize:13}}>Loading...</div>}
    {!loading&&zones.length===0&&!showForm&&<Card style={{textAlign:'center',padding:48}}>
      <div style={{fontSize:48,marginBottom:12}}>🏫</div>
      <div style={{fontSize:15,color:'#E4E4E7',fontWeight:700,marginBottom:8}}>No zones yet</div>
      <div style={{fontSize:12,color:C.muted,marginBottom:20}}>Create zones to organise gateways by physical location.</div>
      <Btn onClick={()=>setShowForm(true)} color={C.blue}>+ Create First Zone</Btn>
    </Card>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))',gap:16}}>
      {filtered.map(z=>{const meta=TM[z.zone_type]||{icon:'📍',color:C.blue};const gc=gwCount(z.id);const isDel=deleting===z.id;return(
        <Card key={z.id} style={{borderColor:editing?.id===z.id?C.blue:C.border}}>
          <div style={{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:14}}>
            <div style={{display:'flex',alignItems:'center',gap:12}}>
              <div style={{width:48,height:48,borderRadius:12,background:`${meta.color}22`,border:`2px solid ${meta.color}44`,display:'flex',alignItems:'center',justifyContent:'center',fontSize:24}}>{meta.icon}</div>
              <div>
                <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{z.name}</div>
                <div style={{fontSize:11,color:meta.color,fontWeight:700,marginTop:2}}>{z.zone_type} · Floor {z.floor||'1'}</div>
              </div>
            </div>
            <div style={{fontSize:11,fontWeight:700,padding:'3px 10px',borderRadius:20,background:`${gc>0?C.green:C.yellow}22`,color:gc>0?C.green:C.yellow,border:`1px solid ${gc>0?C.green:C.yellow}44`}}>{gc} gw</div>
          </div>
          {z.description&&<div style={{fontSize:12,color:C.muted,marginBottom:12,lineHeight:1.5}}>{z.description}</div>}
          <div style={{background:C.dark,borderRadius:8,padding:'8px 12px',marginBottom:14}}>
            <div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:4}}>Gateways</div>
            {gc>0?<div style={{fontSize:12,color:C.green}}>{gwList(z.id)}</div>:<div style={{fontSize:12,color:'#4A5568'}}>None assigned — assign from Gateways tab</div>}
          </div>
          {isDel?<div style={{background:'#2B0D0D',border:`1px solid ${C.red}44`,borderRadius:8,padding:12,marginBottom:10}}>
            <div style={{fontSize:12,color:C.red,fontWeight:700,marginBottom:8}}>Delete this zone?</div>
            <div style={{display:'flex',gap:8}}><Btn small color={C.red} onClick={()=>del(z.id)}>Yes, Delete</Btn><Btn small outline color='#4A5568' onClick={()=>setDeleting(null)}>Cancel</Btn></div>
          </div>:<div style={{display:'flex',gap:8,alignItems:'center'}}>
            <Btn small outline color={C.blue} onClick={()=>{setEditing(z);setShowForm(false);}}>✏️ Edit</Btn>
            <Btn small outline color={C.red} disabled={gc>0} onClick={()=>setDeleting(z.id)}>🗑️ Delete</Btn>
            {gc>0&&<span style={{fontSize:11,color:'#4A5568'}}>Unassign gateways first</span>}
          </div>}
        </Card>
      );})}
    </div>
  </div>;
}
