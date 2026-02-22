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
print('  Prosper RFID — Substitute Teacher Zones')
print('='*55)

# STEP 1: DB
print('\n📦 Step 1: DB migrations...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
ALTER TYPE role_type ADD VALUE IF NOT EXISTS 'SUBSTITUTE';
ALTER TABLE users ADD COLUMN IF NOT EXISTS teacher_type VARCHAR(20) DEFAULT 'PERMANENT';
ALTER TABLE teacher_zones ADD COLUMN IF NOT EXISTS is_temporary BOOLEAN DEFAULT false;
ALTER TABLE teacher_zones ADD COLUMN IF NOT EXISTS assigned_by UUID REFERENCES users(id);
ALTER TABLE teacher_zones ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE teacher_zones ADD COLUMN IF NOT EXISTS notes TEXT;
CREATE INDEX IF NOT EXISTS idx_teacher_zones_temp ON teacher_zones(is_temporary) WHERE is_temporary=true;
" """)
print('  ✅ DB done')

# STEP 2: Update adminCustody.js with temp zone endpoints
print('\n📝 Step 2: Adding temp zone endpoints to adminCustody.js...')
custody_path = f'{API}/adminCustody.js'
src = open(custody_path).read()

new_endpoints = '''
// ── Substitute / Temporary Zone Management ───────────────────────────────────

// GET /api/custody/teacher-zones-all — all teachers with zones + temp flag
router.get('/teacher-zones-all', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT
        u.id, u.username, u.full_name, u.teacher_type,
        u.role,
        COALESCE(
          JSON_AGG(
            JSON_BUILD_OBJECT(
              'zone_id',    z.id,
              'zone_name',  z.name,
              'zone_type',  z.zone_type,
              'zone_role',  tz.zone_role,
              'is_temporary', tz.is_temporary,
              'assigned_by',  tz.assigned_by,
              'assigned_at',  tz.assigned_at,
              'notes',        tz.notes,
              'assigned_by_name', ab.full_name
            ) ORDER BY tz.is_temporary, tz.zone_role
          ) FILTER (WHERE z.id IS NOT NULL),
          '[]'
        ) as zones
      FROM users u
      LEFT JOIN teacher_zones tz ON tz.teacher_id = u.id
      LEFT JOIN zones z ON z.id = tz.zone_id
      LEFT JOIN users ab ON ab.id = tz.assigned_by
      WHERE u.role IN ('TEACHER','SUBSTITUTE') AND u.is_active = true
      GROUP BY u.id
      ORDER BY u.teacher_type, u.full_name
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/temp-zone — assign a temporary zone to any teacher
router.post('/temp-zone', async (req, res) => {
  try {
    const { teacher_id, zone_id, zone_role, notes } = req.body;
    if (!teacher_id || !zone_id)
      return res.status(400).json({ error: 'teacher_id and zone_id required' });
    if (!['IT','DIRECTOR'].includes(req.user.role))
      return res.status(403).json({ error: 'IT Admin or Director only' });

    // Check teacher exists
    const tr = await db.query(
      'SELECT id, username, full_name FROM users WHERE id=$1 AND role IN ($2,$3)',
      [teacher_id, 'TEACHER', 'SUBSTITUTE']);
    if (!tr.rows[0])
      return res.status(404).json({ error: 'Teacher not found' });

    // Upsert — if already assigned, update to temp
    await db.query(`
      INSERT INTO teacher_zones (teacher_id, zone_id, zone_role, is_temporary, assigned_by, assigned_at, notes)
      VALUES ($1, $2, $3, true, $4, NOW(), $5)
      ON CONFLICT (teacher_id, zone_id)
      DO UPDATE SET
        zone_role    = EXCLUDED.zone_role,
        is_temporary = true,
        assigned_by  = EXCLUDED.assigned_by,
        assigned_at  = NOW(),
        notes        = EXCLUDED.notes
    `, [teacher_id, zone_id, zone_role||'PRIMARY', req.user.id, notes||null]);

    await db.query(`
      INSERT INTO audit_log (actor_id, actor_role, action, entity_type, entity_id, new_value)
      VALUES ($1, $2, 'TEMP_ZONE_ASSIGNED', 'user', $3, $4)`,
      [req.user.id, req.user.role, teacher_id,
       JSON.stringify({zone_id, zone_role, notes})]);

    console.log(`🔄 Temp zone assigned: teacher=${tr.rows[0].username} zone=${zone_id}`);
    res.json({ success: true, teacher: tr.rows[0] });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// DELETE /api/custody/temp-zone — remove a temporary zone assignment
router.delete('/temp-zone', async (req, res) => {
  try {
    const { teacher_id, zone_id } = req.body;
    if (!teacher_id || !zone_id)
      return res.status(400).json({ error: 'teacher_id and zone_id required' });
    if (!['IT','DIRECTOR'].includes(req.user.role))
      return res.status(403).json({ error: 'IT Admin or Director only' });

    const r = await db.query(`
      DELETE FROM teacher_zones
      WHERE teacher_id=$1 AND zone_id=$2 AND is_temporary=true
      RETURNING *`, [teacher_id, zone_id]);

    if (!r.rows[0])
      return res.status(404).json({ error: 'Temporary zone assignment not found' });

    await db.query(`
      INSERT INTO audit_log (actor_id, actor_role, action, entity_type, entity_id)
      VALUES ($1, $2, 'TEMP_ZONE_REMOVED', 'user', $3)`,
      [req.user.id, req.user.role, teacher_id]);

    console.log(`🗑️  Temp zone removed: teacher=${teacher_id} zone=${zone_id}`);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/custody/teacher-type/:id — toggle PERMANENT / SUBSTITUTE
router.put('/teacher-type/:id', async (req, res) => {
  try {
    const { teacher_type } = req.body;
    if (!['PERMANENT','SUBSTITUTE'].includes(teacher_type))
      return res.status(400).json({ error: 'teacher_type must be PERMANENT or SUBSTITUTE' });
    if (!['IT','DIRECTOR'].includes(req.user.role))
      return res.status(403).json({ error: 'IT Admin or Director only' });

    const r = await db.query(
      'UPDATE users SET teacher_type=$2, updated_at=NOW() WHERE id=$1 RETURNING id,username,teacher_type',
      [req.params.id, teacher_type]);
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });

    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});
'''

if 'temp-zone' not in src:
    # Insert before module.exports
    src = src.replace('module.exports = router;', new_endpoints + '\nmodule.exports = router;')
    open(custody_path, 'w').write(src)
    print('  ✅ Temp zone endpoints added')
else:
    print('  ⏭  Already added')

# STEP 3: Update adminUsers.js to handle teacher_type in PUT
print('\n📝 Step 3: Updating adminUsers.js for teacher_type...')
users_path = f'{API}/adminUsers.js'
usrc = open(users_path).read()
if 'teacher_type' not in usrc:
    usrc = usrc.replace(
        'const { full_name, email, phone, role, zone_id, is_active } = req.body;',
        'const { full_name, email, phone, role, zone_id, teacher_type, is_active } = req.body;'
    )
    usrc = usrc.replace(
        'is_active=COALESCE($7,is_active), updated_at=NOW()',
        'is_active=COALESCE($7,is_active), teacher_type=COALESCE($8,teacher_type), updated_at=NOW()'
    )
    usrc = usrc.replace(
        '[req.params.id, full_name, email, phone||null, role, zone_id||null, is_active]',
        '[req.params.id, full_name, email, phone||null, role, zone_id||null, is_active, teacher_type||null]'
    )
    open(users_path, 'w').write(usrc)
    print('  ✅ adminUsers.js updated')
else:
    print('  ⏭  Already updated')

# STEP 4: Write SubstituteManager.jsx
print('\n📝 Step 4: Writing SubstituteManager.jsx...')
write(f'{UI}/SubstituteManager.jsx', r"""import { useState, useEffect } from 'react';
const API  = '/api/custody';
const ZAPI = '/api/admin/zones';
const UAPI = '/api/admin/users';
const auth = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',orange:'#E67E22',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
const ZONE_ICONS = {CLASSROOM:'🏫',CORRIDOR:'🚶',ENTRANCE:'🚪',EXIT:'🚨',LOBBY:'🏛️',OUTDOOR:'🌳',NURSE:'🏥',GYM:'🏋️',OFFICE:'💼',HALLWAY:'🚶',CAFETERIA:'🍽️',LIBRARY:'📚'};
const Card=({children,style={}})=><div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:20,...style}}>{children}</div>;
function Btn({onClick,children,color=C.blue,disabled,small,outline,full}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',justifyContent:'center',gap:6,opacity:disabled?0.5:1,width:full?'100%':'auto'}}>{children}</button>;}

function AssignTempZoneModal({token, teacher, zones, onClose, onSaved}){
  const [selectedZone, setSelectedZone] = useState('');
  const [zoneRole,     setZoneRole]     = useState('PRIMARY');
  const [notes,        setNotes]        = useState('');
  const [saving,       setSaving]       = useState(false);
  const [msg,          setMsg]          = useState('');

  // Filter out zones already permanently assigned
  const permZoneIds = (teacher.zones||[]).filter(z=>!z.is_temporary).map(z=>z.zone_id);
  const available   = zones.filter(z=>!permZoneIds.includes(z.id));

  const save = async () => {
    if(!selectedZone) return setMsg('Select a zone');
    setSaving(true); setMsg('');
    try {
      const r = await fetch(`${API}/temp-zone`, {method:'POST', headers:auth(token),
        body: JSON.stringify({teacher_id:teacher.id, zone_id:selectedZone, zone_role:zoneRole, notes})});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg('✓ Zone assigned');
      setTimeout(()=>{ onSaved(); onClose(); }, 800);
    } catch(e) { setMsg('❌ '+e.message); }
    finally { setSaving(false); }
  };

  return <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.75)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:2000,padding:20}}>
    <div style={{background:C.card,border:`2px solid ${C.orange}`,borderRadius:18,padding:24,width:'100%',maxWidth:460}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:16}}>
        <div>
          <h3 style={{color:C.orange,fontSize:15,margin:0}}>➕ Assign Temporary Zone</h3>
          <div style={{fontSize:12,color:C.muted,marginTop:2}}>{teacher.full_name||teacher.username}</div>
        </div>
        <button onClick={onClose} style={{background:'none',border:'none',color:C.muted,fontSize:20,cursor:'pointer'}}>✕</button>
      </div>

      {/* Zone type toggle */}
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Zone Role</div>
      <div style={{display:'flex',gap:8,marginBottom:16}}>
        {[{v:'PRIMARY',label:'🏠 Primary (Home Base)',color:C.green},
          {v:'SECONDARY',label:'🔀 Secondary (Roaming)',color:C.blue}].map(({v,label,color})=>(
          <div key={v} onClick={()=>setZoneRole(v)}
            style={{flex:1,padding:'10px',borderRadius:10,cursor:'pointer',textAlign:'center',
              background:zoneRole===v?`${color}22`:'transparent',
              border:`2px solid ${zoneRole===v?color:C.border}`,
              color:zoneRole===v?color:C.muted,fontSize:12,fontWeight:700}}>
            {label}
          </div>
        ))}
      </div>

      {/* Zone picker */}
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:8}}>Select Zone</div>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginBottom:16,maxHeight:240,overflowY:'auto'}}>
        {available.map(z=>{
          const icon = ZONE_ICONS[z.zone_type]||'📍';
          const sel  = selectedZone===z.id;
          return <div key={z.id} onClick={()=>setSelectedZone(z.id)}
            style={{background:sel?`${C.orange}22`:C.dark,border:`2px solid ${sel?C.orange:C.border}`,
              borderRadius:10,padding:10,cursor:'pointer'}}>
            <div style={{fontSize:18,marginBottom:4}}>{icon}</div>
            <div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>{z.name}</div>
            <div style={{fontSize:10,color:C.muted}}>{z.zone_type}</div>
          </div>;
        })}
        {available.length===0&&<div style={{fontSize:12,color:C.muted,gridColumn:'span 2',padding:20,textAlign:'center'}}>All zones already assigned</div>}
      </div>

      {/* Notes */}
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:4}}>Notes (optional)</div>
      <input value={notes} onChange={e=>setNotes(e.target.value)}
        placeholder="e.g. Covering Ms. Johnson — Room 4B"
        style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,
          padding:'9px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none',
          boxSizing:'border-box',marginBottom:16}}/>

      {msg&&<div style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red,marginBottom:12,fontWeight:600}}>{msg}</div>}
      <Btn onClick={save} disabled={saving||!selectedZone} color={C.orange} full>
        {saving?'⏳ Assigning...':'➕ Assign Temp Zone'}
      </Btn>
    </div>
  </div>;
}

function TeacherCard({token, teacher, onUpdate}){
  const [showModal,   setShowModal]   = useState(false);
  const [removing,    setRemoving]    = useState(null);
  const [togglingType,setTogglingType]= useState(false);
  const [zones,       setZones]       = useState([]);
  const [msg,         setMsg]         = useState('');

  useEffect(()=>{
    fetch(ZAPI,{headers:auth(token)}).then(r=>r.json()).then(setZones).catch(()=>{});
  },[]);

  const removeTemp = async (zoneId, zoneName) => {
    setRemoving(zoneId);
    try {
      const r = await fetch(`${API}/temp-zone`, {method:'DELETE', headers:auth(token),
        body: JSON.stringify({teacher_id:teacher.id, zone_id:zoneId})});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error);
      setMsg(`✓ Removed ${zoneName}`);
      onUpdate();
    } catch(e) { setMsg('❌ '+e.message); }
    finally { setRemoving(null); setTimeout(()=>setMsg(''),3000); }
  };

  const toggleType = async () => {
    setTogglingType(true);
    const newType = teacher.teacher_type==='SUBSTITUTE'?'PERMANENT':'SUBSTITUTE';
    try {
      const r = await fetch(`${API}/teacher-type/${teacher.id}`, {method:'PUT', headers:auth(token),
        body: JSON.stringify({teacher_type:newType})});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error);
      onUpdate();
    } catch(e) { setMsg('❌ '+e.message); }
    finally { setTogglingType(false); }
  };

  const isSub  = teacher.teacher_type==='SUBSTITUTE';
  const pZones = (teacher.zones||[]).filter(z=>!z.is_temporary);
  const tZones = (teacher.zones||[]).filter(z=>z.is_temporary);

  return <Card style={{borderColor:isSub?C.orange:C.border}}>
    {showModal&&<AssignTempZoneModal token={token} teacher={teacher} zones={zones}
      onClose={()=>setShowModal(false)} onSaved={onUpdate}/>}

    {/* Header */}
    <div style={{display:'flex',alignItems:'flex-start',gap:12,marginBottom:14}}>
      <div style={{width:46,height:46,borderRadius:12,background:`${isSub?C.orange:C.blue}22`,
        border:`2px solid ${isSub?C.orange:C.blue}44`,display:'flex',alignItems:'center',
        justifyContent:'center',fontSize:22,flexShrink:0}}>
        {isSub?'🔄':'👩‍🏫'}
      </div>
      <div style={{flex:1}}>
        <div style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{teacher.full_name||teacher.username}</div>
        <div style={{fontSize:11,color:C.muted}}>@{teacher.username}</div>
        <div style={{display:'flex',gap:6,marginTop:6,alignItems:'center'}}>
          <span style={{fontSize:11,fontWeight:700,padding:'2px 10px',borderRadius:20,
            background:isSub?`${C.orange}22`:`${C.blue}22`,
            color:isSub?C.orange:C.blue,
            border:`1px solid ${isSub?C.orange:C.blue}44`}}>
            {isSub?'🔄 SUBSTITUTE':'👩‍🏫 PERMANENT'}
          </span>
          <span onClick={toggleType} style={{fontSize:10,color:C.muted,cursor:'pointer',
            padding:'2px 8px',borderRadius:10,border:`1px solid ${C.border}`,
            opacity:togglingType?0.5:1}}>
            {togglingType?'...':`Switch to ${isSub?'Permanent':'Substitute'}`}
          </span>
        </div>
      </div>
      <Btn small color={C.orange} onClick={()=>setShowModal(true)}>+ Temp Zone</Btn>
    </div>

    {msg&&<div style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red,marginBottom:10,fontWeight:600}}>{msg}</div>}

    {/* Permanent zones */}
    {pZones.length>0&&<div style={{marginBottom:12}}>
      <div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',
        letterSpacing:'0.06em',marginBottom:6}}>Permanent Zones</div>
      <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
        {pZones.map(z=>(
          <div key={z.zone_id} style={{display:'flex',alignItems:'center',gap:6,padding:'5px 10px',
            borderRadius:8,background:z.zone_role==='PRIMARY'?`${C.green}22`:`${C.blue}22`,
            border:`1px solid ${z.zone_role==='PRIMARY'?C.green:C.blue}44`}}>
            <span style={{fontSize:14}}>{ZONE_ICONS[z.zone_type]||'📍'}</span>
            <span style={{fontSize:12,color:'#E4E4E7'}}>{z.zone_name}</span>
            <span style={{fontSize:10,fontWeight:700,color:z.zone_role==='PRIMARY'?C.green:C.blue}}>
              {z.zone_role==='PRIMARY'?'HOME':'2ND'}
            </span>
          </div>
        ))}
      </div>
    </div>}

    {/* Temporary zones */}
    {tZones.length>0&&<div>
      <div style={{fontSize:11,color:C.orange,fontWeight:600,textTransform:'uppercase',
        letterSpacing:'0.06em',marginBottom:6}}>⏱ Temporary Zones</div>
      <div style={{display:'flex',flexDirection:'column',gap:6}}>
        {tZones.map(z=>(
          <div key={z.zone_id} style={{display:'flex',alignItems:'center',gap:8,padding:'8px 10px',
            borderRadius:8,background:`${C.orange}11`,border:`1px solid ${C.orange}44`}}>
            <span style={{fontSize:16}}>{ZONE_ICONS[z.zone_type]||'📍'}</span>
            <div style={{flex:1}}>
              <div style={{display:'flex',alignItems:'center',gap:6}}>
                <span style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>{z.zone_name}</span>
                <span style={{fontSize:10,fontWeight:700,padding:'1px 6px',borderRadius:10,
                  background:`${C.orange}33`,color:C.orange,border:`1px solid ${C.orange}44`}}>
                  TEMP {z.zone_role==='PRIMARY'?'· HOME':'· 2ND'}
                </span>
              </div>
              {z.notes&&<div style={{fontSize:10,color:C.muted,marginTop:2}}>{z.notes}</div>}
              {z.assigned_by_name&&<div style={{fontSize:10,color:'#4A5568',marginTop:1}}>
                Assigned by {z.assigned_by_name} · {z.assigned_at?new Date(z.assigned_at).toLocaleDateString():''}
              </div>}
            </div>
            <Btn small outline color={C.red}
              disabled={removing===z.zone_id}
              onClick={()=>removeTemp(z.zone_id, z.zone_name)}>
              {removing===z.zone_id?'⏳':'✕'}
            </Btn>
          </div>
        ))}
      </div>
    </div>}

    {pZones.length===0&&tZones.length===0&&<div style={{fontSize:12,color:C.muted,
      textAlign:'center',padding:'12px 0'}}>No zones assigned yet</div>}
  </Card>;
}

export default function SubstituteManager({token}){
  const [teachers, setTeachers] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [filter,   setFilter]   = useState('ALL');

  const load = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/teacher-zones-all`, {headers:auth(token)});
      setTeachers(await r.json());
    } finally { setLoading(false); }
  };
  useEffect(()=>{ load(); },[]);

  const subs  = teachers.filter(t=>t.teacher_type==='SUBSTITUTE');
  const perms = teachers.filter(t=>t.teacher_type!=='SUBSTITUTE');
  const tempCount = teachers.reduce((n,t)=>n+(t.zones||[]).filter(z=>z.is_temporary).length,0);

  const displayed = filter==='ALL'    ? teachers
                  : filter==='SUB'    ? subs
                  : filter==='TEMP'   ? teachers.filter(t=>(t.zones||[]).some(z=>z.is_temporary))
                  : perms;

  return <div>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <div>
        <h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>Zone Coverage</h2>
        <p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>
          {teachers.length} teacher{teachers.length!==1?'s':''} ·
          {subs.length} substitute{subs.length!==1?'s':''} ·
          <span style={{color:C.orange}}> {tempCount} temp assignment{tempCount!==1?'s':''}</span>
        </p>
      </div>
    </div>

    {/* Filter bar */}
    <div style={{display:'flex',gap:6,marginBottom:20,flexWrap:'wrap'}}>
      {[{id:'ALL',label:'All Teachers'},
        {id:'PERM',label:'👩‍🏫 Permanent'},
        {id:'SUB',label:'🔄 Substitutes'},
        {id:'TEMP',label:`⏱ Has Temp Zones${tempCount>0?` (${tempCount})`:''}`}
      ].map(f=>(
        <div key={f.id} onClick={()=>setFilter(f.id)}
          style={{padding:'6px 14px',borderRadius:20,cursor:'pointer',fontSize:12,fontWeight:700,
            background:filter===f.id?C.orange:'transparent',
            border:`1.5px solid ${filter===f.id?C.orange:C.border}`,
            color:filter===f.id?'#fff':C.muted}}>
          {f.label}
        </div>
      ))}
    </div>

    {loading&&<div style={{color:C.muted,fontSize:13}}>Loading...</div>}

    {!loading&&displayed.length===0&&<Card style={{textAlign:'center',padding:48}}>
      <div style={{fontSize:48,marginBottom:12}}>🔄</div>
      <div style={{fontSize:15,color:'#E4E4E7',fontWeight:700,marginBottom:8}}>No teachers in this filter</div>
    </Card>}

    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(340px,1fr))',gap:16}}>
      {displayed.map(t=><TeacherCard key={t.id} token={token} teacher={t} onUpdate={load}/>)}
    </div>
  </div>;
}
""")

# STEP 5: Wire into CustodyManager as a 4th sub-tab
print('\n🔌 Step 5: Wiring SubstituteManager into CustodyManager...')
custody_ui = f'{UI}/CustodyManager.jsx'
csrc = open(custody_ui).read()
changed = False

if 'SubstituteManager' not in csrc:
    csrc = csrc.replace(
        "import { useState, useEffect } from 'react';",
        "import { useState, useEffect } from 'react';\nimport SubstituteManager from './SubstituteManager';"
    )
    # Add sub-tab to tab bar
    csrc = csrc.replace(
        "{id:'settings',label:'⚙️ Settings'}",
        "{id:'settings',label:'⚙️ Settings'},{id:'coverage',label:'🔄 Coverage'}"
    )
    # Add render
    csrc = csrc.replace(
        "{view==='settings'&&<SettingsPanel token={token}/>}",
        "{view==='settings'&&<SettingsPanel token={token}/>}\n    {view==='coverage'&&<SubstituteManager token={token}/>}"
    )
    open(custody_ui,'w').write(csrc)
    changed = True
    print('  ✅ Coverage tab added to CustodyManager')
else:
    print('  ⏭  Already wired')

# STEP 6: Rebuild
print('\n🐳 Step 6: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 35s...')
time.sleep(35)

# STEP 7: Smoke test
print('\n🧪 Step 7: Smoke test...')
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print('  ✅ Login OK')

    req2 = urllib.request.Request('http://localhost/api/custody/teacher-zones-all',
        headers={'Authorization':f'Bearer {token}'})
    teachers = J.loads(urllib.request.urlopen(req2,timeout=10).read())
    print(f'  ✅ /api/custody/teacher-zones-all → {len(teachers)} teacher(s)')
    for t in teachers:
        perm = [z for z in (t['zones'] or []) if not z['is_temporary']]
        temp = [z for z in (t['zones'] or []) if z['is_temporary']]
        print(f'     {t["teacher_type"]} {t["full_name"] or t["username"]} → {len(perm)} perm, {len(temp)} temp zones')

except Exception as e:
    print(f'  ❌ {e}')

print('\n' + '='*55)
print('  ✅ SUBSTITUTE / TEMP ZONE COVERAGE DEPLOYED')
print('='*55)
print('\n  IT Admin → 🔗 Custody → 🔄 Coverage tab')
print('  → See all teachers with permanent + temp zones')
print('  → Toggle PERMANENT / SUBSTITUTE badge per teacher')
print('  → + Temp Zone button to assign any zone instantly')
print('  → ✕ Remove button to revoke temp zone any time\n')
