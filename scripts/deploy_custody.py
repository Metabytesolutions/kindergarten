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
print('  Prosper RFID — Chain of Custody Deploy')
print('='*55)

# STEP 1: DB schema
print('\n📦 Step 1: DB schema...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
-- School-wide settings (configurable timeout etc.)
CREATE TABLE IF NOT EXISTS school_settings (
  key   VARCHAR(64) PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO school_settings (key,value) VALUES
  ('custody_transfer_timeout_minutes','5'),
  ('missing_alert_grace_seconds','60'),
  ('school_name','Prosper School'),
  ('school_uuid','7777772E-6B6B-6D63-6E2E-636F6D000001')
ON CONFLICT (key) DO NOTHING;

-- Teacher zone assignments
CREATE TABLE IF NOT EXISTS teacher_zones (
  teacher_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  zone_id    UUID NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
  zone_role  VARCHAR(10) NOT NULL DEFAULT 'PRIMARY',
  PRIMARY KEY (teacher_id, zone_id)
);
CREATE INDEX IF NOT EXISTS idx_teacher_zones_teacher ON teacher_zones(teacher_id);
CREATE INDEX IF NOT EXISTS idx_teacher_zones_zone ON teacher_zones(zone_id);

-- Current custody (single source of truth)
CREATE TABLE IF NOT EXISTS student_custody (
  student_id          UUID PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
  current_teacher_id  UUID REFERENCES users(id),
  current_zone_id     UUID REFERENCES zones(id),
  custody_since       TIMESTAMPTZ DEFAULT NOW(),
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Custody transfer log
CREATE TABLE IF NOT EXISTS custody_transfers (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  transfer_group  UUID NOT NULL DEFAULT uuid_generate_v4(),
  student_id      UUID NOT NULL REFERENCES students(id),
  from_teacher_id UUID REFERENCES users(id),
  to_teacher_id   UUID NOT NULL REFERENCES users(id),
  to_zone_id      UUID NOT NULL REFERENCES zones(id),
  initiated_by    UUID NOT NULL REFERENCES users(id),
  status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
  initiated_at    TIMESTAMPTZ DEFAULT NOW(),
  responded_at    TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '5 minutes',
  notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_transfers_student  ON custody_transfers(student_id);
CREATE INDEX IF NOT EXISTS idx_transfers_to       ON custody_transfers(to_teacher_id);
CREATE INDEX IF NOT EXISTS idx_transfers_from     ON custody_transfers(from_teacher_id);
CREATE INDEX IF NOT EXISTS idx_transfers_pending  ON custody_transfers(status) WHERE status='PENDING';
CREATE INDEX IF NOT EXISTS idx_transfers_group    ON custody_transfers(transfer_group);

-- Add primary zone to users
ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_zone_id UUID REFERENCES zones(id);

-- Seed initial custody from existing student→teacher assignments
INSERT INTO student_custody (student_id, current_teacher_id, current_zone_id)
SELECT s.id, s.teacher_id, s.zone_id
FROM students s
WHERE s.teacher_id IS NOT NULL
ON CONFLICT (student_id) DO NOTHING;
" """)
print('  ✅ DB schema done')

# STEP 2: Seed teacher_zones from existing assignments
print('\n📦 Step 2: Seeding teacher zones from existing user zone_id...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
INSERT INTO teacher_zones (teacher_id, zone_id, zone_role)
SELECT u.id, u.zone_id, 'PRIMARY'
FROM users u
WHERE u.role='TEACHER' AND u.zone_id IS NOT NULL
ON CONFLICT DO NOTHING;
SELECT t.username, z.name, tz.zone_role
FROM teacher_zones tz
JOIN users t ON t.id=tz.teacher_id
JOIN zones z ON z.id=tz.zone_id;
" """)
print('  ✅ Teacher zones seeded')

# STEP 3: Custody API
print('\n📝 Step 3: Writing adminCustody.js...')
write(f'{API}/adminCustody.js', '''const express = require('express');
const db      = require('./db');
const router  = express.Router();

// ── Helper: get school setting ───────────────────────────────────────────────
async function getSetting(key, fallback) {
  try {
    const r = await db.query('SELECT value FROM school_settings WHERE key=$1', [key]);
    return r.rows[0] ? r.rows[0].value : fallback;
  } catch(e) { return fallback; }
}

// GET /api/custody/overview — all students with current custody
router.get('/overview', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.student_id as school_id,
        sc.current_teacher_id, sc.current_zone_id, sc.custody_since,
        u.username as teacher_username, u.full_name as teacher_name,
        z.name as zone_name, z.zone_type,
        t.mac_address as tag_mac, t.last_seen_at, t.battery_mv,
        ps.state as presence_state
      FROM students s
      LEFT JOIN student_custody sc ON sc.student_id = s.id
      LEFT JOIN users u ON u.id = sc.current_teacher_id
      LEFT JOIN zones z ON z.id = sc.current_zone_id
      LEFT JOIN ble_tags t ON t.student_id = s.id AND t.is_active = true
      LEFT JOIN presence_states ps ON ps.student_id = s.id
      WHERE s.is_active = true
      ORDER BY u.username NULLS LAST, s.last_name
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/my-students — students in current teacher custody
router.get('/my-students', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.student_id as school_id,
        sc.current_zone_id, sc.custody_since,
        z.name as zone_name, z.zone_type,
        t.mac_address as tag_mac, t.last_seen_at, t.battery_mv,
        t.last_rssi, ps.state as presence_state,
        -- pending outgoing transfers
        (SELECT COUNT(*) FROM custody_transfers ct
         WHERE ct.student_id=s.id AND ct.from_teacher_id=$1
         AND ct.status=''PENDING'') as pending_out
      FROM students s
      JOIN student_custody sc ON sc.student_id=s.id AND sc.current_teacher_id=$1
      LEFT JOIN zones z ON z.id=sc.current_zone_id
      LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=s.id
      WHERE s.is_active=true
      ORDER BY s.last_name
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/incoming — pending transfers to this teacher
router.get('/incoming', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT ct.*, ct.transfer_group,
        s.first_name, s.last_name,
        fu.full_name as from_teacher_name, fu.username as from_teacher_username,
        z.name as to_zone_name,
        EXTRACT(EPOCH FROM (ct.expires_at - NOW())) as seconds_remaining
      FROM custody_transfers ct
      JOIN students s ON s.id=ct.student_id
      JOIN users fu ON fu.id=ct.from_teacher_id
      JOIN zones z ON z.id=ct.to_zone_id
      WHERE ct.to_teacher_id=$1 AND ct.status=''PENDING''
        AND ct.expires_at > NOW()
      ORDER BY ct.transfer_group, ct.initiated_at
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/teachers-zones — all teachers with their zones (for transfer UI)
router.get('/teachers-zones', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT u.id, u.username, u.full_name,
        JSON_AGG(JSON_BUILD_OBJECT(
          ''zone_id'', z.id, ''zone_name'', z.name,
          ''zone_type'', z.zone_type, ''zone_role'', tz.zone_role
        )) as zones
      FROM users u
      JOIN teacher_zones tz ON tz.teacher_id=u.id
      JOIN zones z ON z.id=tz.zone_id
      WHERE u.role=''TEACHER'' AND u.is_active=true AND u.id != $1
      GROUP BY u.id ORDER BY u.full_name
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/transfer — initiate batch transfer
router.post('/transfer', async (req, res) => {
  try {
    const { student_ids, to_teacher_id, to_zone_id, notes } = req.body;
    if (!student_ids?.length || !to_teacher_id || !to_zone_id)
      return res.status(400).json({ error: 'student_ids, to_teacher_id, to_zone_id required' });

    const timeoutMins = await getSetting('custody_transfer_timeout_minutes', '5');
    const groupId = (await db.query('SELECT uuid_generate_v4() as id')).rows[0].id;

    // Verify all students are in caller custody (unless IT/DIRECTOR)
    if (req.user.role === 'TEACHER') {
      const check = await db.query(
        `SELECT COUNT(*) FROM student_custody
         WHERE student_id = ANY($1) AND current_teacher_id=$2`,
        [student_ids, req.user.id]);
      if (parseInt(check.rows[0].count) !== student_ids.length)
        return res.status(403).json({ error: 'Some students not in your custody' });
    }

    // Create transfer records
    const transfers = [];
    for (const sid of student_ids) {
      const custody = await db.query(
        'SELECT current_teacher_id FROM student_custody WHERE student_id=$1', [sid]);
      const fromTeacherId = custody.rows[0]?.current_teacher_id || req.user.id;
      const r = await db.query(`
        INSERT INTO custody_transfers
          (transfer_group,student_id,from_teacher_id,to_teacher_id,to_zone_id,initiated_by,notes,expires_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7, NOW() + ($8 || '' minutes'')::INTERVAL)
        RETURNING *`,
        [groupId, sid, fromTeacherId, to_teacher_id, to_zone_id, req.user.id, notes||null, timeoutMins]);
      transfers.push(r.rows[0]);
    }

    console.log(`📤 Transfer initiated: ${student_ids.length} students → teacher ${to_teacher_id}`);
    res.json({ success:true, transfer_group:groupId, count:transfers.length, transfers });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/transfer/:group/accept — accept all in group
router.post('/transfer/:group/accept', async (req, res) => {
  try {
    const pending = await db.query(`
      SELECT * FROM custody_transfers
      WHERE transfer_group=$1 AND to_teacher_id=$2
        AND status=''PENDING'' AND expires_at > NOW()
    `, [req.params.group, req.user.id]);

    if (!pending.rows.length)
      return res.status(404).json({ error: 'No pending transfers found or expired' });

    for (const t of pending.rows) {
      // Update custody
      await db.query(`
        INSERT INTO student_custody (student_id,current_teacher_id,current_zone_id,custody_since,updated_at)
        VALUES ($1,$2,$3,NOW(),NOW())
        ON CONFLICT (student_id) DO UPDATE
        SET current_teacher_id=$2, current_zone_id=$3, custody_since=NOW(), updated_at=NOW()
      `, [t.student_id, req.user.id, t.to_zone_id]);

      // Mark accepted
      await db.query(`UPDATE custody_transfers SET status=''ACCEPTED'',responded_at=NOW() WHERE id=$1`, [t.id]);

      // Log
      await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id)
        VALUES ($1,$2,''CUSTODY_ACCEPTED'',''student'',$3)`,
        [req.user.id, req.user.role, t.student_id]);
    }

    console.log(`✅ Custody accepted: group ${req.params.group} (${pending.rows.length} students)`);
    res.json({ success:true, accepted: pending.rows.length });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/transfer/:group/reject
router.post('/transfer/:group/reject', async (req, res) => {
  try {
    const r = await db.query(`
      UPDATE custody_transfers SET status=''REJECTED'', responded_at=NOW()
      WHERE transfer_group=$1 AND to_teacher_id=$2 AND status=''PENDING''
      RETURNING student_id, from_teacher_id
    `, [req.params.group, req.user.id]);

    console.log(`❌ Custody rejected: group ${req.params.group}`);
    res.json({ success:true, rejected: r.rows.length });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/settings
router.get('/settings', async (req, res) => {
  try {
    const r = await db.query('SELECT key, value FROM school_settings ORDER BY key');
    const settings = {};
    r.rows.forEach(row => settings[row.key] = row.value);
    res.json(settings);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/custody/settings  (IT only)
router.put('/settings', async (req, res) => {
  try {
    if (req.user.role !== ''IT'')
      return res.status(403).json({ error: 'IT Admin only' });
    for (const [key, value] of Object.entries(req.body)) {
      await db.query(`INSERT INTO school_settings (key,value,updated_at) VALUES ($1,$2,NOW())
        ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()`, [key, String(value)]);
    }
    res.json({ success:true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/teacher-zones/:teacherId
router.get('/teacher-zones/:teacherId', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT tz.zone_role, z.id, z.name, z.zone_type
      FROM teacher_zones tz JOIN zones z ON z.id=tz.zone_id
      WHERE tz.teacher_id=$1 ORDER BY tz.zone_role, z.name
    `, [req.params.teacherId]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/custody/teacher-zones/:teacherId  (full replace)
router.put('/teacher-zones/:teacherId', async (req, res) => {
  try {
    const { zones } = req.body; // [{zone_id, zone_role}]
    await db.query('DELETE FROM teacher_zones WHERE teacher_id=$1', [req.params.teacherId]);
    for (const z of (zones||[])) {
      await db.query(
        'INSERT INTO teacher_zones (teacher_id,zone_id,zone_role) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING',
        [req.params.teacherId, z.zone_id, z.zone_role||''PRIMARY'']);
    }
    res.json({ success:true, count: zones?.length||0 });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
''')

# STEP 4: Wire custody route
print('\n🔌 Step 4: Wiring custody route...')
idx = f'{API}/index.js'
src = open(idx).read()
if 'custodyRouter' not in src:
    open(idx,'a').write("\nconst custodyRouter = require('./adminCustody');\napp.use('/api/custody', requireAuth, custodyRouter);\n")
    print('  ✅ Custody route wired')
else:
    print('  ⏭  Already wired')

# STEP 5: Write CustodyManager.jsx (IT Admin — zone assignments + overview)
print('\n📝 Step 5: Writing CustodyManager.jsx...')
write(f'{UI}/CustodyManager.jsx', r"""import { useState, useEffect } from 'react';
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
""")

# STEP 6: Write TransferPanel.jsx (Teacher iPad UI)
print('\n📝 Step 6: Writing TransferPanel.jsx...')
write(f'{UI}/TransferPanel.jsx', r"""import { useState, useEffect, useCallback } from 'react';
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
""")

# STEP 7: Wire App.jsx — add Custody tab to IT Admin
print('\n🔌 Step 7: Wiring App.jsx...')
app_path = f'{UI}/App.jsx'
app_src  = open(app_path).read()
changed  = False

if 'CustodyManager' not in app_src:
    app_src = app_src.replace(
        "import StudentManager from './StudentManager'",
        "import StudentManager from './StudentManager'\nimport CustodyManager from './CustodyManager'"
    )
    changed = True

if "'custody'" not in app_src:
    app_src = app_src.replace(
        "{id:'students',label:'👶 Students'}",
        "{id:'students',label:'👶 Students'},{id:'custody',label:'🔗 Custody'}"
    )
    changed = True

if "itTab==='custody'" not in app_src:
    app_src = app_src.replace(
        "{itTab==='students' && <div style={{padding:24}}><StudentManager token={token}/></div>}",
        "{itTab==='students' && <div style={{padding:24}}><StudentManager token={token}/></div>}\n          {itTab==='custody' && <div style={{padding:24}}><CustodyManager token={token}/></div>}"
    )
    changed = True

if changed:
    open(app_path,'w').write(app_src)
    print('  ✅ Custody tab wired to IT Admin')
else:
    print('  ⏭  Already wired')

# STEP 8: Rebuild + smoke test
print('\n🐳 Step 8: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 35s...')
time.sleep(35)

print('\n🧪 Step 9: Smoke test...')
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print('  ✅ Login OK')

    for path,label in [
        ('/api/custody/overview',   'custody overview'),
        ('/api/custody/settings',   'school settings'),
        ('/api/custody/teachers-zones', 'teachers+zones'),
    ]:
        req2 = urllib.request.Request(f'http://localhost{path}',
            headers={'Authorization':f'Bearer {token}'})
        d = J.loads(urllib.request.urlopen(req2,timeout=10).read())
        count = len(d) if isinstance(d,list) else len(d.keys())
        print(f'  ✅ {path} → {count} record(s)')

    # Print settings
    req3 = urllib.request.Request('http://localhost/api/custody/settings',
        headers={'Authorization':f'Bearer {token}'})
    s = J.loads(urllib.request.urlopen(req3,timeout=10).read())
    print(f'  ⚙️  Transfer timeout: {s.get("custody_transfer_timeout_minutes")} min')
    print(f'  ⚙️  Missing grace: {s.get("missing_alert_grace_seconds")} sec')
    print(f'  ⚙️  School: {s.get("school_name")}')

except Exception as e:
    print(f'  ❌ {e}')

print('\n' + '='*55)
print('  ✅ CHAIN OF CUSTODY DEPLOYED')
print('='*55)
print('\n  IT Admin → 🔗 Custody tab')
print('  → Teacher Zones  — assign PRIMARY + SECONDARY zones')
print('  → Custody Overview — who owns which students')
print('  → Settings — configure transfer timeout\n')
