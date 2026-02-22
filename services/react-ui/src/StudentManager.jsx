import { useState, useEffect } from 'react';
const API  = '/api/admin/students';
const ZAPI = '/api/admin/zones';
const auth = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
const GRADES = ['Pre-K','K','1st','2nd','3rd','4th','5th','6th'];
const Card=({children,style={}})=><div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:20,...style}}>{children}</div>;
function Btn({onClick,children,color=C.blue,disabled,small,outline}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',gap:6,opacity:disabled?0.5:1}}>{children}</button>;}
function Fld({label,value,onChange,placeholder,type='text',half}){return <div style={{marginBottom:12,gridColumn:half?'span 1':'span 1'}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>{label}</div><input type={type} value={value||''} onChange={e=>onChange(e.target.value)} placeholder={placeholder} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/></div>;}
function Sel({label,value,onChange,options,placeholder='— Select —'}){return <div style={{marginBottom:12}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>{label}</div><select value={value||''} onChange={e=>onChange(e.target.value)} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 12px',color:value?'#E4E4E7':'#4A5568',fontFamily:'inherit',fontSize:13,outline:'none'}}><option value="">{placeholder}</option>{options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></div>;}

function StudentForm({token,student,onSaved,onCancel}){
  const [firstName,setFirstName]=useState(student?.first_name||'');
  const [lastName,setLastName]=useState(student?.last_name||'');
  const [studentId,setStudentId]=useState(student?.student_id||'');
  const [grade,setGrade]=useState(student?.grade||'');
  const [className,setClassName]=useState(student?.class_name||'');
  const [zoneId,setZoneId]=useState(student?.zone_id||'');
  const [teacherId,setTeacherId]=useState(student?.teacher_id||'');
  const [dob,setDob]=useState(student?.dob?student.dob.split('T')[0]:'');
  const [guardianName,setGuardianName]=useState(student?.guardian_name||'');
  const [guardianPhone,setGuardianPhone]=useState(student?.guardian_phone||'');
  const [zones,setZones]=useState([]);
  const [teachers,setTeachers]=useState([]);
  const [saving,setSaving]=useState(false);
  const [err,setErr]=useState('');
  const isEdit=!!student;

  useEffect(()=>{
    fetch(ZAPI,{headers:auth(token)}).then(r=>r.json()).then(setZones).catch(()=>{});
    fetch(`${API}/teachers`,{headers:auth(token)}).then(r=>r.json()).then(setTeachers).catch(()=>{});
  },[]);

  // Auto-fill zone when teacher selected
  const onTeacherChange = tid => {
    setTeacherId(tid);
    const t = teachers.find(t=>t.id===tid);
    if(t?.zone_id && !zoneId) setZoneId(t.zone_id);
  };

  const save=async()=>{
    if(!firstName.trim()||!lastName.trim())return setErr('First and last name required');
    setSaving(true);setErr('');
    try{
      const body={first_name:firstName.trim(),last_name:lastName.trim(),
        student_id:studentId||null,grade:grade||null,class_name:className||null,
        zone_id:zoneId||null,teacher_id:teacherId||null,
        dob:dob||null,guardian_name:guardianName||null,guardian_phone:guardianPhone||null};
      const r=await fetch(isEdit?`${API}/${student.id}`:API,{method:isEdit?'PUT':'POST',headers:auth(token),body:JSON.stringify(body)});
      const d=await r.json();
      if(!r.ok)throw new Error(d.error);
      onSaved(d);
    }catch(e){setErr(e.message);}
    finally{setSaving(false);}
  };

  return <Card style={{marginBottom:20,borderColor:C.blue}}>
    <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:4}}>
      {isEdit?`✏️ Edit — ${student.first_name} ${student.last_name}`:'➕ Add New Student'}
    </h3>
    <p style={{fontSize:12,color:C.muted,marginTop:0,marginBottom:16}}>Fields marked * are required</p>

    {/* Section: Identity */}
    <div style={{fontSize:11,color:C.blue,fontWeight:700,textTransform:'uppercase',letterSpacing:'0.08em',marginBottom:8}}>📋 Student Identity</div>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:4}}>
      <Fld label="First Name *" value={firstName} onChange={setFirstName} placeholder="Emma"/>
      <Fld label="Last Name *" value={lastName} onChange={setLastName} placeholder="Johnson"/>
      <Fld label="Student ID" value={studentId} onChange={setStudentId} placeholder="STU-001"/>
      <Fld label="Date of Birth" value={dob} onChange={setDob} type="date"/>
    </div>

    {/* Section: Class Assignment */}
    <div style={{fontSize:11,color:C.green,fontWeight:700,textTransform:'uppercase',letterSpacing:'0.08em',marginBottom:8,marginTop:8}}>🏫 Class Assignment</div>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:4}}>
      <Sel label="Grade" value={grade} onChange={setGrade} options={GRADES.map(g=>({value:g,label:g}))}/>
      <Fld label="Class Name" value={className} onChange={setClassName} placeholder="Sunflowers"/>
      <Sel label="Assigned Teacher" value={teacherId} onChange={onTeacherChange}
        options={teachers.map(t=>({value:t.id,label:t.full_name?`${t.full_name} (@${t.username})`:t.username}))}
        placeholder="— Select Teacher —"/>
      <Sel label="Classroom / Zone" value={zoneId} onChange={setZoneId}
        options={zones.map(z=>({value:z.id,label:`${z.name} (${z.zone_type})`}))}/>
    </div>

    {/* Section: Guardian */}
    <div style={{fontSize:11,color:C.yellow,fontWeight:700,textTransform:'uppercase',letterSpacing:'0.08em',marginBottom:8,marginTop:8}}>👨‍👩‍👧 Guardian Info</div>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:4}}>
      <Fld label="Guardian Name" value={guardianName} onChange={setGuardianName} placeholder="John Johnson"/>
      <Fld label="Guardian Phone" value={guardianPhone} onChange={setGuardianPhone} placeholder="+1 555 0100"/>
    </div>

    {/* Preview */}
    {(firstName||lastName)&&<div style={{background:C.dark,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14}}>
      <div style={{fontSize:11,color:C.muted,marginBottom:6}}>PREVIEW</div>
      <div style={{display:'flex',alignItems:'center',gap:12}}>
        <div style={{width:44,height:44,borderRadius:10,background:`${C.blue}22`,border:`2px solid ${C.blue}44`,display:'flex',alignItems:'center',justifyContent:'center',fontSize:20}}>👤</div>
        <div>
          <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{firstName} {lastName}</div>
          <div style={{fontSize:11,color:C.muted}}>
            {grade&&<span style={{color:C.green,marginRight:8}}>{grade}</span>}
            {className&&<span style={{marginRight:8}}>{className}</span>}
            {teacherId&&<span style={{color:C.blue}}>👩‍🏫 {teachers.find(t=>t.id===teacherId)?.full_name||teachers.find(t=>t.id===teacherId)?.username}</span>}
          </div>
        </div>
      </div>
    </div>}

    {err&&<div style={{color:C.red,fontSize:12,marginBottom:10}}>❌ {err}</div>}
    <div style={{display:'flex',gap:10}}>
      <Btn onClick={save} disabled={saving} color={C.green}>{saving?'⏳ Saving...':isEdit?'✓ Save Changes':'✓ Add Student'}</Btn>
      <Btn onClick={onCancel} outline color='#4A5568'>Cancel</Btn>
    </div>
  </Card>;
}

export default function StudentManager({token}){
  const [students,setStudents]=useState([]);const [loading,setLoading]=useState(true);const [showForm,setShowForm]=useState(false);const [editing,setEditing]=useState(null);const [deleting,setDeleting]=useState(null);const [msg,setMsg]=useState('');const [search,setSearch]=useState('');const [filterTeacher,setFilterTeacher]=useState('');const [teachers,setTeachers]=useState([]);
  const load=async()=>{setLoading(true);try{const[sr,tr]=await Promise.all([fetch(API,{headers:auth(token)}).then(r=>r.json()),fetch(`${API}/teachers`,{headers:auth(token)}).then(r=>r.json())]);setStudents(sr);setTeachers(tr);}finally{setLoading(false);}};
  useEffect(()=>{load();},[]);
  const del=async id=>{try{const r=await fetch(`${API}/${id}`,{method:'DELETE',headers:auth(token)});if(!r.ok){const d=await r.json();throw new Error(d.error);}setMsg('✓ Student removed');load();}catch(e){setMsg('❌ '+e.message);}setDeleting(null);setTimeout(()=>setMsg(''),4000);};
  const onSaved=()=>{setShowForm(false);setEditing(null);load();};
  const filtered=students.filter(s=>{
    const q=search.toLowerCase();
    const matchSearch=!q||`${s.first_name} ${s.last_name} ${s.student_id||''} ${s.class_name||''}`.toLowerCase().includes(q);
    const matchTeacher=!filterTeacher||s.teacher_id===filterTeacher;
    return matchSearch&&matchTeacher;
  });
  const battPct=mv=>mv?Math.min(100,Math.max(0,Math.round((mv-2800)/(3300-2800)*100))):null;

  return <div>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <div><h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>Student Management</h2>
        <p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>{students.length} student{students.length!==1?'s':''} · {students.filter(s=>s.tag_mac).length} with tags</p></div>
      {!showForm&&!editing&&<Btn onClick={()=>setShowForm(true)} color={C.blue}>+ Add Student</Btn>}
    </div>

    {msg&&<div style={{marginBottom:14,padding:'10px 14px',borderRadius:8,fontSize:13,fontWeight:600,background:msg.startsWith('✓')?'#0D2B1A':'#2B0D0D',border:`1px solid ${msg.startsWith('✓')?C.green:C.red}`,color:msg.startsWith('✓')?C.green:C.red}}>{msg}</div>}
    {showForm&&<StudentForm token={token} onSaved={onSaved} onCancel={()=>setShowForm(false)}/>}
    {editing&&<StudentForm token={token} student={editing} onSaved={onSaved} onCancel={()=>setEditing(null)}/>}

    {/* Search + Filter */}
    {!showForm&&!editing&&<div style={{display:'flex',gap:12,marginBottom:20}}>
      <div style={{flex:1,position:'relative'}}>
        <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="🔍 Search by name, ID, class..."
          style={{width:'100%',background:C.card,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 14px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/>
      </div>
      <select value={filterTeacher} onChange={e=>setFilterTeacher(e.target.value)}
        style={{background:C.card,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'9px 14px',color:filterTeacher?'#E4E4E7':'#4A5568',fontFamily:'inherit',fontSize:13,outline:'none',minWidth:180}}>
        <option value="">All Teachers</option>
        {teachers.map(t=><option key={t.id} value={t.id}>{t.full_name||t.username}</option>)}
      </select>
    </div>}

    {loading&&<div style={{color:C.muted,fontSize:13}}>Loading...</div>}
    {!loading&&students.length===0&&!showForm&&<Card style={{textAlign:'center',padding:48}}>
      <div style={{fontSize:48,marginBottom:12}}>👶</div>
      <div style={{fontSize:15,color:'#E4E4E7',fontWeight:700,marginBottom:8}}>No students yet</div>
      <div style={{fontSize:12,color:C.muted,marginBottom:20}}>Add students and assign them to teachers and classrooms.</div>
      <Btn onClick={()=>setShowForm(true)} color={C.blue}>+ Add First Student</Btn>
    </Card>}

    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))',gap:16}}>
      {filtered.map(s=>{
        const batt=battPct(s.battery_mv);
        const battColor=batt===null?'#4A5568':batt>50?C.green:batt>20?C.yellow:C.red;
        const isDel=deleting===s.id;
        return <Card key={s.id}>
          <div style={{display:'flex',alignItems:'flex-start',gap:12,marginBottom:14}}>
            <div style={{width:44,height:44,borderRadius:10,background:`${C.blue}22`,border:`2px solid ${C.blue}44`,display:'flex',alignItems:'center',justifyContent:'center',fontSize:20,flexShrink:0}}>👤</div>
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{s.first_name} {s.last_name}</div>
              <div style={{display:'flex',gap:6,flexWrap:'wrap',marginTop:4}}>
                {s.grade&&<span style={{fontSize:11,fontWeight:700,padding:'2px 8px',borderRadius:20,background:`${C.green}22`,color:C.green,border:`1px solid ${C.green}44`}}>{s.grade}</span>}
                {s.class_name&&<span style={{fontSize:11,color:C.muted}}>{s.class_name}</span>}
              </div>
            </div>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:14}}>
            {[
              ['Student ID',s.student_id||'—'],
              ['Teacher',s.teacher_full_name||s.teacher_username||'Unassigned'],
              ['Zone',s.zone_name||'Unassigned'],
              ['Guardian',s.guardian_name||'—'],
            ].map(([k,v])=><div key={k} style={{background:C.dark,borderRadius:8,padding:'7px 10px'}}><div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>{k}</div><div style={{fontSize:12,color:'#E4E4E7',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{v}</div></div>)}
          </div>
          {/* Tag status */}
          <div style={{background:C.dark,borderRadius:8,padding:'8px 12px',marginBottom:14,display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            <div>
              <div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>BLE Tag</div>
              {s.tag_mac
                ?<div style={{fontSize:12,color:C.green,fontFamily:'monospace'}}>{s.tag_label||s.tag_mac}</div>
                :<div style={{fontSize:12,color:'#4A5568'}}>No tag assigned</div>}
            </div>
            {batt!==null&&<div style={{textAlign:'right'}}>
              <div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>Battery</div>
              <div style={{fontSize:12,color:battColor,fontWeight:700}}>{batt}%</div>
            </div>}
          </div>
          {isDel?<div style={{background:'#2B0D0D',border:`1px solid ${C.red}44`,borderRadius:8,padding:12}}>
            <div style={{fontSize:12,color:C.red,fontWeight:700,marginBottom:8}}>Remove this student?</div>
            <div style={{display:'flex',gap:8}}><Btn small color={C.red} onClick={()=>del(s.id)}>Yes, Remove</Btn><Btn small outline color='#4A5568' onClick={()=>setDeleting(null)}>Cancel</Btn></div>
          </div>:<div style={{display:'flex',gap:8}}>
            <Btn small outline color={C.blue} onClick={()=>{setEditing(s);setShowForm(false);}}>✏️ Edit</Btn>
            <Btn small outline color={C.red} onClick={()=>setDeleting(s.id)}>🗑️ Remove</Btn>
          </div>}
        </Card>;
      })}
    </div>
  </div>;
}
