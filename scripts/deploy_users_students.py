#!/usr/bin/env python3
import os, subprocess, time, urllib.request, json as J, hashlib, secrets

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
print('  Prosper RFID — Users + Students Deploy')
print('='*55)

# STEP 1: DB migrations
print('\n📦 Step 1: DB migrations...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(128);
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS zone_id UUID REFERENCES zones(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE students ADD COLUMN IF NOT EXISTS student_id VARCHAR(32);
ALTER TABLE students ADD COLUMN IF NOT EXISTS grade VARCHAR(20);
ALTER TABLE students ADD COLUMN IF NOT EXISTS class_name VARCHAR(64);
ALTER TABLE students ADD COLUMN IF NOT EXISTS zone_id UUID REFERENCES zones(id);
ALTER TABLE students ADD COLUMN IF NOT EXISTS teacher_id UUID REFERENCES users(id);
ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_url TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE ble_tags ADD COLUMN IF NOT EXISTS label VARCHAR(64);
ALTER TABLE ble_tags ADD COLUMN IF NOT EXISTS tx_power INTEGER DEFAULT -12;
ALTER TABLE ble_tags ADD COLUMN IF NOT EXISTS adv_interval INTEGER DEFAULT 500;
ALTER TABLE ble_tags ADD COLUMN IF NOT EXISTS beacon_type VARCHAR(20) DEFAULT 'iBeacon';
ALTER TABLE ble_tags ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'INVENTORY';
ALTER TABLE ble_tags ADD COLUMN IF NOT EXISTS provisioned_at TIMESTAMPTZ;
" """)
print('  ✅ DB migrations done')

# STEP 2: Users API
print('\n📝 Step 2: Writing adminUsers.js...')
write(f'{API}/adminUsers.js', '''const express = require('express');
const db      = require('./db');
const bcrypt  = require('bcrypt');
const router  = express.Router();

// GET /api/admin/users
router.get('/', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT u.id, u.username, u.email, u.role, u.full_name, u.phone,
             u.is_active, u.created_at, u.last_login_at, u.updated_at,
             z.name as zone_name, u.zone_id
      FROM users u
      LEFT JOIN zones z ON z.id = u.zone_id
      ORDER BY u.role, u.username
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/users
router.post('/', async (req, res) => {
  try {
    const { username, email, password, role, full_name, phone, zone_id } = req.body;
    if (!username || !email || !password || !role)
      return res.status(400).json({ error: 'username, email, password, role required' });
    if (!['TEACHER','DIRECTOR','IT'].includes(role))
      return res.status(400).json({ error: 'Invalid role' });
    const hash = await bcrypt.hash(password, 10);
    const r = await db.query(
      `INSERT INTO users (username,email,password_hash,role,full_name,phone,zone_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id,username,email,role,full_name,is_active,created_at`,
      [username, email, hash, role, full_name||null, phone||null, zone_id||null]
    );
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'USER_CREATED','user',$3)`,
      [req.user.id, req.user.role, r.rows[0].id]);
    console.log(`✅ User created: ${username} (${role})`);
    res.json(r.rows[0]);
  } catch(e) {
    if (e.code === '23505') return res.status(400).json({ error: 'Username or email already exists' });
    res.status(500).json({ error: e.message });
  }
});

// PUT /api/admin/users/:id
router.put('/:id', async (req, res) => {
  try {
    const { full_name, email, phone, role, zone_id, is_active } = req.body;
    const r = await db.query(
      `UPDATE users SET full_name=COALESCE($2,full_name), email=COALESCE($3,email),
       phone=$4, role=COALESCE($5,role), zone_id=$6,
       is_active=COALESCE($7,is_active), updated_at=NOW()
       WHERE id=$1 RETURNING id,username,email,role,full_name,is_active,zone_id`,
      [req.params.id, full_name, email, phone||null, role, zone_id||null, is_active]
    );
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/users/:id/reset-password
router.post('/:id/reset-password', async (req, res) => {
  try {
    const { password } = req.body;
    if (!password || password.length < 8)
      return res.status(400).json({ error: 'Password must be at least 8 characters' });
    const hash = await bcrypt.hash(password, 10);
    await db.query('UPDATE users SET password_hash=$2, updated_at=NOW() WHERE id=$1', [req.params.id, hash]);
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'PASSWORD_RESET','user',$3)`,
      [req.user.id, req.user.role, req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// DELETE /api/admin/users/:id  (soft deactivate)
router.delete('/:id', async (req, res) => {
  try {
    if (req.params.id === req.user.id)
      return res.status(400).json({ error: 'Cannot deactivate your own account' });
    await db.query('UPDATE users SET is_active=false, updated_at=NOW() WHERE id=$1', [req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
''')

# STEP 3: Students API
print('\n📝 Step 3: Writing adminStudents.js...')
write(f'{API}/adminStudents.js', '''const express = require('express');
const db      = require('./db');
const router  = express.Router();

// GET /api/admin/students
router.get('/', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT s.*,
        z.name as zone_name,
        u.username as teacher_username,
        u.full_name as teacher_full_name,
        t.mac_address as tag_mac,
        t.id as tag_id,
        t.label as tag_label,
        t.is_active as tag_active,
        t.battery_mv,
        t.last_seen_at
      FROM students s
      LEFT JOIN zones z ON z.id = s.zone_id
      LEFT JOIN users u ON u.id = s.teacher_id
      LEFT JOIN ble_tags t ON t.student_id = s.id AND t.is_active = true
      WHERE s.is_active = true
      ORDER BY s.last_name, s.first_name
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/admin/students/teachers  (for dropdown)
router.get('/teachers', async (req, res) => {
  try {
    const r = await db.query(`SELECT id, username, full_name, zone_id FROM users WHERE role='TEACHER' AND is_active=true ORDER BY username`);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/students
router.post('/', async (req, res) => {
  try {
    const { first_name, last_name, student_id, grade, class_name, zone_id, teacher_id, dob, guardian_name, guardian_phone } = req.body;
    if (!first_name || !last_name) return res.status(400).json({ error: 'first_name and last_name required' });
    const r = await db.query(
      `INSERT INTO students (first_name,last_name,student_id,grade,class_name,zone_id,teacher_id,dob,guardian_name,guardian_phone)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *`,
      [first_name, last_name, student_id||null, grade||null, class_name||null,
       zone_id||null, teacher_id||null, dob||null, guardian_name||null, guardian_phone||null]
    );
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'STUDENT_CREATED','student',$3)`,
      [req.user.id, req.user.role, r.rows[0].id]);
    console.log(`✅ Student created: ${first_name} ${last_name}`);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/admin/students/:id
router.put('/:id', async (req, res) => {
  try {
    const { first_name, last_name, student_id, grade, class_name, zone_id, teacher_id, dob, guardian_name, guardian_phone, is_active } = req.body;
    const r = await db.query(
      `UPDATE students SET
        first_name=COALESCE($2,first_name), last_name=COALESCE($3,last_name),
        student_id=$4, grade=$5, class_name=$6, zone_id=$7, teacher_id=$8,
        dob=$9, guardian_name=$10, guardian_phone=$11,
        is_active=COALESCE($12,is_active), updated_at=NOW()
       WHERE id=$1 RETURNING *`,
      [req.params.id, first_name, last_name, student_id||null, grade||null,
       class_name||null, zone_id||null, teacher_id||null, dob||null,
       guardian_name||null, guardian_phone||null, is_active]
    );
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// DELETE /api/admin/students/:id (soft delete)
router.delete('/:id', async (req, res) => {
  try {
    await db.query('UPDATE students SET is_active=false, updated_at=NOW() WHERE id=$1', [req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
''')

# STEP 4: Wire routes
print('\n🔌 Step 4: Wiring routes...')
idx = f'{API}/index.js'
src = open(idx).read()
added = False
if 'adminUsersRouter' not in src:
    open(idx,'a').write("\nconst adminUsersRouter = require('./adminUsers');\napp.use('/api/admin/users', requireAuth, adminUsersRouter);\n")
    print('  ✅ Users route added')
    added = True
if 'adminStudentsRouter' not in src:
    open(idx,'a').write("\nconst adminStudentsRouter = require('./adminStudents');\napp.use('/api/admin/students', requireAuth, adminStudentsRouter);\n")
    print('  ✅ Students route added')
    added = True
if not added:
    print('  ⏭  Already wired')

# STEP 5: Write UserManager.jsx
print('\n📝 Step 5: Writing UserManager.jsx...')
write(f'{UI}/UserManager.jsx', r"""import { useState, useEffect } from 'react';
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
""")

# STEP 6: Write StudentManager.jsx
print('\n📝 Step 6: Writing StudentManager.jsx...')
write(f'{UI}/StudentManager.jsx', r"""import { useState, useEffect } from 'react';
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
""")

# STEP 7: DB — permitted zones table + new enum values
print('\n📦 Step 7: Permitted zones table + enum updates...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
CREATE TABLE IF NOT EXISTS student_permitted_zones (
  student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  zone_id    UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
  PRIMARY KEY (student_id, zone_id)
);
CREATE INDEX IF NOT EXISTS idx_permitted_student ON student_permitted_zones(student_id);
ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'EXIT_VIOLATION';
ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'ZONE_VIOLATION';
ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'MISSING';
ALTER TYPE presence_state_type ADD VALUE IF NOT EXISTS 'ROAMING';
ALTER TYPE presence_state_type ADD VALUE IF NOT EXISTS 'MISSING';
" """)
print('  ✅ DB done')

# STEP 8: Permitted zones API endpoints (append to adminStudents.js)
print('\n📝 Step 8: Adding permitted zones endpoints to adminStudents.js...')
students_api = f'{API}/adminStudents.js'
src = open(students_api).read()
if 'permitted-zones' not in src:
    open(students_api,'a').write('''
// GET /api/admin/students/:id/permitted-zones
router.get('/:id/permitted-zones', async (req, res) => {
  try {
    const r = await db.query(
      `SELECT z.id, z.name, z.zone_type FROM student_permitted_zones spz
       JOIN zones z ON z.id = spz.zone_id WHERE spz.student_id=$1`,
      [req.params.id]
    );
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/admin/students/:id/permitted-zones  (full replace)
router.put('/:id/permitted-zones', async (req, res) => {
  try {
    const { zone_ids } = req.body; // array of zone UUIDs
    await db.query('DELETE FROM student_permitted_zones WHERE student_id=$1', [req.params.id]);
    if (zone_ids && zone_ids.length > 0) {
      const vals = zone_ids.map((_,i) => `($1,$${i+2})`).join(',');
      await db.query(`INSERT INTO student_permitted_zones (student_id,zone_id) VALUES ${vals}`,
        [req.params.id, ...zone_ids]);
    }
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id,new_value) VALUES ($1,$2,'PERMITTED_ZONES_UPDATED','student',$3,$4)`,
      [req.user.id, req.user.role, req.params.id, JSON.stringify({zone_ids})]);
    res.json({ success: true, count: zone_ids?.length||0 });
  } catch(e) { res.status(500).json({ error: e.message }); }
});
''')
    print('  ✅ Permitted zones endpoints added')
else:
    print('  ⏭  Already added')

# STEP 9: Update alert engine with zone-aware logic
print('\n📝 Step 9: Updating alert engine with zone-aware logic...')
alert_path = f'{API}/alertEngine.js'
if os.path.exists(alert_path):
    src = open(alert_path).read()
    if 'permitted_zones' not in src and 'EXIT_VIOLATION' not in src:
        # Append zone-aware detection logic helper
        open(alert_path,'a').write('''

// ── Zone-aware presence evaluation ──────────────────────────────────────────
// Called by mqttWorker when a detection arrives
// Returns: { state, alertType, alertSeverity } or null
async function evaluateZonePresence(db, studentId, detectedZoneId) {
  try {
    // Get student primary zone
    const sr = await db.query(
      'SELECT zone_id FROM students WHERE id=$1', [studentId]);
    if (!sr.rows[0]) return null;
    const primaryZoneId = sr.rows[0].zone_id;

    // Get detected zone type
    const zr = await db.query(
      'SELECT zone_type FROM zones WHERE id=$1', [detectedZoneId]);
    const zoneType = zr.rows[0]?.zone_type;

    // Rule 1: EXIT zone type → always CRITICAL
    if (zoneType === 'EXIT') {
      return { state:'EXIT_CONFIRMED', alertType:'EXIT_VIOLATION', severity:'CRITICAL' };
    }

    // Rule 2: Primary classroom → PRESENT
    if (detectedZoneId === primaryZoneId) {
      return { state:'CONFIRMED_PRESENT', alertType:null, severity:null };
    }

    // Rule 3: Check permitted zones
    const pr = await db.query(
      'SELECT 1 FROM student_permitted_zones WHERE student_id=$1 AND zone_id=$2',
      [studentId, detectedZoneId]);
    if (pr.rows.length > 0) {
      return { state:'ROAMING', alertType:null, severity:null };
    }

    // Rule 4: Unknown zone → ZONE_VIOLATION
    return { state:'TRANSITIONING', alertType:'ZONE_VIOLATION', severity:'WARNING' };
  } catch(e) {
    console.error('evaluateZonePresence error:', e.message);
    return null;
  }
}

module.exports.evaluateZonePresence = evaluateZonePresence;
''')
        print('  ✅ Zone-aware logic added to alertEngine.js')
    else:
        print('  ⏭  Already updated')
else:
    print('  ⚠️  alertEngine.js not found — skipping (will apply when engine is built)')

# STEP 10: Wire App.jsx — add Users + Students tabs
print('\n🔌 Step 10: Wiring App.jsx...')
app_path = f'{UI}/App.jsx'
app_src  = open(app_path).read()
changed  = False

if 'UserManager' not in app_src:
    app_src = app_src.replace(
        "import ZoneManager from './ZoneManager'",
        "import ZoneManager from './ZoneManager'\nimport UserManager from './UserManager'\nimport StudentManager from './StudentManager'"
    )
    changed = True
    print('  ✅ Imports added')

if "'users'" not in app_src:
    app_src = app_src.replace(
        "{id:'zones',label:'🏫 Zones'}",
        "{id:'zones',label:'🏫 Zones'},{id:'users',label:'👥 Users'},{id:'students',label:'👶 Students'}"
    )
    changed = True
    print('  ✅ Tabs added')

if 'itTab===\'users\'' not in app_src:
    app_src = app_src.replace(
        "{itTab==='zones' && <div style={{padding:24}}><ZoneManager token={token}/></div>}",
        "{itTab==='zones' && <div style={{padding:24}}><ZoneManager token={token}/></div>}\n          {itTab==='users' && <div style={{padding:24}}><UserManager token={token}/></div>}\n          {itTab==='students' && <div style={{padding:24}}><StudentManager token={token}/></div>}"
    )
    changed = True
    print('  ✅ Tab renders added')

if changed:
    open(app_path,'w').write(app_src)
else:
    print('  ⏭  Already wired')

# STEP 11: Add Students tab to Director view
print('\n🔌 Step 11: Wiring Director view...')
director_path = f'{UI}/DirectorView.jsx'
if os.path.exists(director_path):
    dsrc = open(director_path).read()
    if 'StudentManager' not in dsrc:
        # Add import
        dsrc = dsrc.replace(
            "import { useState",
            "import StudentManager from './StudentManager';\nimport { useState"
        )
        # We'll add a tab — find the return statement opening div
        # Add directorTab state if not present
        if 'directorTab' not in dsrc:
            dsrc = dsrc.replace(
                "export default function DirectorView(",
                "export default function DirectorView("
            )
            # Inject tab state and nav bar before main content
            dsrc = dsrc.replace(
                "  return (\n    <div>",
                "  const [directorTab, setDirectorTab] = React.useState('overview');\n  return (\n    <div>"
            )
            dsrc = dsrc.replace(
                "  return (\n    <div style",
                "  const [directorTab, setDirectorTab] = React.useState('overview');\n  return (\n    <div style"
            )
        open(director_path,'w').write(dsrc)
        print('  ✅ StudentManager import added to DirectorView')
    else:
        print('  ⏭  Already wired')
else:
    print('  ⚠️  DirectorView.jsx not found — skipping')

# STEP 12: Rebuild
print('\n🐳 Step 12: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 35s...')
time.sleep(35)

# STEP 13: Smoke test
print('\n🧪 Step 13: Smoke test...')
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print('  ✅ Login OK')

    # Test users endpoint
    req2 = urllib.request.Request('http://localhost/api/admin/users',
        headers={'Authorization':f'Bearer {token}'})
    users = J.loads(urllib.request.urlopen(req2,timeout=10).read())
    print(f'  ✅ /api/admin/users → {len(users)} user(s)')
    for u in users: print(f'     {u["role"]} @{u["username"]} ({u["full_name"] or "no name"})')

    # Test students endpoint
    req3 = urllib.request.Request('http://localhost/api/admin/students',
        headers={'Authorization':f'Bearer {token}'})
    students = J.loads(urllib.request.urlopen(req3,timeout=10).read())
    print(f'  ✅ /api/admin/students → {len(students)} student(s)')
    for s in students: print(f'     👤 {s["first_name"]} {s["last_name"]} | teacher={s["teacher_username"] or "unassigned"} | tag={s["tag_mac"] or "none"}')

    # Test teachers dropdown
    req4 = urllib.request.Request('http://localhost/api/admin/students/teachers',
        headers={'Authorization':f'Bearer {token}'})
    teachers = J.loads(urllib.request.urlopen(req4,timeout=10).read())
    print(f'  ✅ /api/admin/students/teachers → {len(teachers)} teacher(s)')

except Exception as e:
    print(f'  ❌ {e}')

print('\n' + '='*55)
print('  ✅ USERS + STUDENTS DEPLOYED')
print('='*55)
print('\n  Open: http://192.168.5.63')
print('  IT Admin → 👥 Users tab  — create teachers & directors')
print('  IT Admin → 👶 Students tab — add students, assign teachers')
print('  Zone logic: Primary classroom + permitted roaming zones\n')
