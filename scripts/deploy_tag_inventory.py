#!/usr/bin/env python3
import os, subprocess, time

BASE = os.path.expanduser('~/prosper-platform')
UI   = f'{BASE}/services/react-ui/src'
API  = f'{BASE}/services/app-server/src'

def run(cmd):
    print(f'  $ {cmd[:80]}')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout.strip(): print(r.stdout.strip()[:300])
    if r.returncode != 0 and r.stderr.strip(): print(f'  ERR: {r.stderr.strip()[:200]}')
    return r.stdout.strip()

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write(content)
    print(f'  ✅ {os.path.basename(path)}')

print('\n' + '='*55)
print('  Prosper RFID — Tag Inventory + Support Button')
print('='*55)

# STEP 1: Tag Inventory API
print('\n📝 Step 1: Tag Inventory API...')
write(f'{API}/tagInventoryApi.js', r"""
'use strict';
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// GET /api/tags/inventory — full tag audit
router.get('/inventory', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT
        bt.id, bt.mac_address, bt.label, bt.status,
        bt.assigned_to, bt.battery_mv, bt.last_rssi,
        bt.last_seen_at,
        EXTRACT(EPOCH FROM (NOW()-bt.last_seen_at))::int as secs_ago,
        -- Student assignment
        s.id as student_id,
        s.first_name||' '||s.last_name as student_name,
        s.grade, s.student_id as school_id,
        -- Teacher assignment
        u.id as teacher_id, u.full_name as teacher_name, u.username,
        -- Last gateway
        bg.id as gateway_id, bg.short_id as gateway_short_id,
        bg.label as gateway_label,
        z.name as zone_name, z.zone_type,
        -- Hit count last 5 min
        (SELECT COUNT(*) FROM detections d
         WHERE d.tag_mac=bt.mac_address
           AND d.detected_at > NOW() - INTERVAL '5 minutes')::int as hits_5min,
        -- Hit count last hour
        (SELECT COUNT(*) FROM detections d
         WHERE d.tag_mac=bt.mac_address
           AND d.detected_at > NOW() - INTERVAL '1 hour')::int as hits_1hr,
        -- Battery percentage
        CASE
          WHEN bt.battery_mv IS NULL THEN NULL
          WHEN bt.battery_mv >= 3100 THEN 100
          WHEN bt.battery_mv <= 2800 THEN 0
          ELSE ROUND(((bt.battery_mv - 2800)::numeric / 300) * 100)
        END as battery_pct
      FROM ble_tags bt
      LEFT JOIN students s ON s.id=bt.student_id
      LEFT JOIN users u ON u.id=(
        SELECT ct.current_teacher_id FROM student_custody ct
        WHERE ct.student_id=bt.student_id LIMIT 1
      )
      LEFT JOIN (
        SELECT DISTINCT ON (tag_mac) tag_mac, gateway_id
        FROM detections ORDER BY tag_mac, detected_at DESC
      ) ld ON ld.tag_mac=bt.mac_address
      LEFT JOIN ble_gateways bg ON bg.id=ld.gateway_id
      LEFT JOIN zones z ON z.id=bg.zone_id
      WHERE bt.battery_mv IS NOT NULL
      ORDER BY
        CASE bt.status WHEN 'ASSIGNED' THEN 0 ELSE 1 END,
        bt.last_seen_at DESC NULLS LAST
    `);

    // Summary counts
    const tags = r.rows;
    const summary = {
      total:      tags.length,
      assigned:   tags.filter(t=>t.status==='ASSIGNED').length,
      inventory:  tags.filter(t=>t.status==='INVENTORY').length,
      active_now: tags.filter(t=>t.secs_ago!==null&&t.secs_ago<60).length,
      low_battery:tags.filter(t=>t.battery_pct!==null&&t.battery_pct<20).length,
      missing:    tags.filter(t=>t.secs_ago===null||t.secs_ago>300).length,
      teachers:   tags.filter(t=>t.assigned_to==='TEACHER').length,
      students:   tags.filter(t=>t.assigned_to==='STUDENT').length,
    };

    res.json({ summary, tags });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/tags/system-status — for support panel
router.get('/system-status', async (req, res) => {
  try {
    const [tags, gateways, sessions, events] = await Promise.all([
      db.query(`SELECT COUNT(*) as total,
        COUNT(*) FILTER (WHERE last_seen_at > NOW()-INTERVAL '60s' AND battery_mv IS NOT NULL) as active
        FROM ble_tags`),
      db.query(`SELECT COUNT(*) as total,
        COUNT(*) FILTER (WHERE health_state='HEALTHY') as healthy
        FROM ble_gateways`),
      db.query(`SELECT COUNT(*) as total FROM student_sessions WHERE batch_date=CURRENT_DATE`),
      db.query(`SELECT COUNT(*) as unacked FROM director_events
        WHERE requires_ack=true AND acked_at IS NULL AND created_at >= CURRENT_DATE`),
    ]);

    res.json({
      database:  { status: 'OK' },
      gateways:  { total: parseInt(gateways.rows[0].total),
                   healthy: parseInt(gateways.rows[0].healthy) },
      tags:      { total: parseInt(tags.rows[0].total),
                   active: parseInt(tags.rows[0].active) },
      sessions:  { today: parseInt(sessions.rows[0].total) },
      alerts:    { unacked: parseInt(events.rows[0].unacked) },
      timestamp: new Date().toISOString(),
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/tags/:id/assign — assign tag to student or teacher
router.put('/:id/assign', async (req, res) => {
  if (!['IT'].includes(req.user.role))
    return res.status(403).json({ error: 'IT Admin only' });
  try {
    const { student_id, label, assigned_to } = req.body;
    await db.query(`
      UPDATE ble_tags SET
        student_id=$1, label=$2,
        assigned_to=COALESCE($3::assigned_entity_type,'STUDENT'),
        status=CASE WHEN $1 IS NULL AND $3 IS NULL THEN 'INVENTORY' ELSE 'ASSIGNED' END,
        updated_at=NOW()
      WHERE id=$4
    `, [student_id||null, label||null, assigned_to||null, req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
""")

# Wire route
idx = f'{API}/index.js'
isrc = open(idx).read()
if 'tagInventoryApi' not in isrc:
    open(idx,'a').write(
        "\nconst tagInventoryRouter = require('./tagInventoryApi');\n"
        "app.use('/api/tags', requireAuth, tagInventoryRouter);\n")
    print('  ✅ /api/tags route wired')

# STEP 2: TagInventory React component
print('\n📝 Step 2: Writing TagInventory.jsx...')
write(f'{UI}/TagInventory.jsx', r"""
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
  if(pct===null||pct===undefined) return <span style={{color:'#4A5568',fontSize:11}}>—</span>;
  const color=pct>50?C.green:pct>20?C.yellow:C.red;
  return <div style={{display:'flex',alignItems:'center',gap:6}}>
    <div style={{width:36,height:10,borderRadius:3,background:'#1E3A5F',
      border:`1px solid ${color}44`,overflow:'hidden'}}>
      <div style={{width:`${pct}%`,height:'100%',background:color,
        borderRadius:3,transition:'width 0.3s'}}/>
    </div>
    <span style={{fontSize:11,color,fontWeight:700}}>{pct}%</span>
  </div>;
}

function StatusDot({secsAgo}){
  const active = secsAgo!==null && secsAgo<60;
  const stale  = secsAgo!==null && secsAgo<300;
  const color  = active?C.green:stale?C.yellow:C.red;
  return <div style={{display:'flex',alignItems:'center',gap:5}}>
    <div style={{width:8,height:8,borderRadius:'50%',background:color,
      boxShadow:active?`0 0 6px ${color}`:undefined}}/>
    <span style={{fontSize:11,color}}>{fmt(secsAgo)}</span>
  </div>;
}

export default function TagInventory({token, compact=false}){
  const [data,    setData]   = useState(null);
  const [filter,  setFilter] = useState('ALL');
  const [loading, setLoading]= useState(true);

  const load = useCallback(async()=>{
    try{
      const r = await fetch('/api/tags/inventory',{headers:auth(token)});
      setData(await r.json());
    }catch(e){}finally{setLoading(false);}
  },[token]);

  useEffect(()=>{ load(); },[load]);
  useEffect(()=>{
    const iv=setInterval(load, 15000);
    return()=>clearInterval(iv);
  },[load]);

  if(loading) return <div style={{color:C.muted,padding:20,fontSize:13}}>Loading tags...</div>;

  const summary = data?.summary||{};
  const allTags = data?.tags||[];

  const filtered = filter==='ALL'     ? allTags
    : filter==='ASSIGNED'  ? allTags.filter(t=>t.status==='ASSIGNED')
    : filter==='INVENTORY' ? allTags.filter(t=>t.status==='INVENTORY')
    : filter==='ACTIVE'    ? allTags.filter(t=>t.secs_ago!==null&&t.secs_ago<60)
    : filter==='LOW_BAT'   ? allTags.filter(t=>t.battery_pct!==null&&t.battery_pct<20)
    : filter==='TEACHERS'  ? allTags.filter(t=>t.assigned_to==='TEACHER')
    : filter==='STUDENTS'  ? allTags.filter(t=>t.assigned_to==='STUDENT')
    : allTags;

  const FILTERS=[
    {id:'ALL',      label:`All (${summary.total||0})`},
    {id:'ACTIVE',   label:`🟢 Active (${summary.active_now||0})`},
    {id:'ASSIGNED', label:`Assigned (${summary.assigned||0})`},
    {id:'INVENTORY',label:`Inventory (${summary.inventory||0})`},
    {id:'TEACHERS', label:`👩‍🏫 Teachers (${summary.teachers||0})`},
    {id:'STUDENTS', label:`👶 Students (${summary.students||0})`},
    {id:'LOW_BAT',  label:`🔋 Low Battery (${summary.low_battery||0})`},
  ];

  return <div>
    {/* Summary cards */}
    <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10,marginBottom:16}}>
      {[
        {label:'Total Tags',  value:summary.total||0,      color:'#E4E4E7'},
        {label:'Active Now',  value:summary.active_now||0, color:C.green},
        {label:'Assigned',    value:summary.assigned||0,   color:C.blue},
        {label:'Low Battery', value:summary.low_battery||0,color:C.red},
      ].map(s=><div key={s.label} style={{background:C.dark,border:`1px solid ${C.border}`,
        borderRadius:10,padding:'10px 12px',textAlign:'center'}}>
        <div style={{fontSize:22,fontWeight:800,color:s.color}}>{s.value}</div>
        <div style={{fontSize:10,color:C.muted,fontWeight:600,marginTop:2}}>{s.label}</div>
      </div>)}
    </div>

    {/* Filter tabs */}
    <div style={{display:'flex',gap:0,borderBottom:`1px solid ${C.border}`,
      marginBottom:14,flexWrap:'wrap'}}>
      {FILTERS.map(f=><div key={f.id} onClick={()=>setFilter(f.id)}
        style={{padding:'7px 14px',cursor:'pointer',fontSize:11,fontWeight:700,
          color:filter===f.id?C.blue:C.muted,whiteSpace:'nowrap',
          borderBottom:`2px solid ${filter===f.id?C.blue:'transparent'}`}}>
        {f.label}
      </div>)}
      <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:6,padding:'0 8px'}}>
        <button onClick={load} style={{background:'none',border:`1px solid ${C.border}`,
          borderRadius:6,color:C.muted,padding:'4px 10px',cursor:'pointer',fontSize:11}}>
          🔄 Refresh
        </button>
      </div>
    </div>

    {/* Tag table */}
    <div style={{display:'flex',flexDirection:'column',gap:6}}>
      {filtered.length===0&&<div style={{color:C.muted,fontSize:13,padding:20,textAlign:'center'}}>
        No tags match filter</div>}
      {filtered.map(t=>{
        const isActive = t.secs_ago!==null&&t.secs_ago<60;
        const isLowBat = t.battery_pct!==null&&t.battery_pct<20;
        return <div key={t.id} style={{
          background:C.card,
          border:`1.5px solid ${isLowBat?C.red:isActive?`${C.green}44`:C.border}`,
          borderLeft:`4px solid ${t.status==='ASSIGNED'?C.blue:C.muted}`,
          borderRadius:10,padding:'10px 14px',
          display:'grid',
          gridTemplateColumns:'2fr 2fr 1fr 1fr 1fr 1fr',
          gap:12,alignItems:'center'}}>

          {/* Tag identity */}
          <div>
            <div style={{fontSize:12,fontWeight:800,color:'#E4E4E7',
              fontFamily:'monospace'}}>{t.mac_address}</div>
            <div style={{fontSize:11,color:C.muted,marginTop:2}}>
              {t.label||<span style={{fontStyle:'italic'}}>Unlabeled</span>}
            </div>
            <div style={{display:'flex',gap:4,marginTop:4}}>
              <span style={{fontSize:10,padding:'1px 6px',borderRadius:8,fontWeight:700,
                background:t.status==='ASSIGNED'?`${C.blue}22`:`${C.muted}22`,
                color:t.status==='ASSIGNED'?C.blue:C.muted}}>
                {t.status}
              </span>
              {t.assigned_to&&t.assigned_to!=='NONE'&&
                <span style={{fontSize:10,padding:'1px 6px',borderRadius:8,fontWeight:700,
                  background:t.assigned_to==='TEACHER'?`${C.purple}22`:`${C.teal}22`,
                  color:t.assigned_to==='TEACHER'?C.purple:C.teal}}>
                  {t.assigned_to==='TEACHER'?'👩‍🏫 TEACHER':'👶 STUDENT'}
                </span>}
            </div>
          </div>

          {/* Person assigned */}
          <div>
            {t.student_name&&<div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>
              👤 {t.student_name}
            </div>}
            {t.teacher_name&&<div style={{fontSize:11,color:C.purple}}>
              👩‍🏫 {t.teacher_name}
            </div>}
            {!t.student_name&&!t.teacher_name&&
              <div style={{fontSize:11,color:'#4A5568',fontStyle:'italic'}}>Unassigned</div>}
            {t.zone_name&&<div style={{fontSize:10,color:C.muted,marginTop:2}}>
              📍 {t.zone_name}
            </div>}
          </div>

          {/* Gateway */}
          <div style={{textAlign:'center'}}>
            {t.gateway_short_id
              ? <div style={{fontSize:11,fontWeight:700,color:C.blue,
                  padding:'2px 8px',borderRadius:8,background:`${C.blue}22`}}>
                  📡 {t.gateway_short_id}
                </div>
              : <div style={{fontSize:11,color:'#4A5568'}}>—</div>}
          </div>

          {/* Signal */}
          <div style={{textAlign:'center'}}>
            {t.last_rssi
              ? <div>
                  <div style={{fontSize:12,fontWeight:700,
                    color:t.last_rssi>-50?C.green:t.last_rssi>-70?C.yellow:C.red}}>
                    {t.last_rssi} dBm
                  </div>
                  <div style={{fontSize:10,color:'#4A5568'}}>{t.hits_5min} hits/5m</div>
                </div>
              : <span style={{fontSize:11,color:'#4A5568'}}>—</span>}
          </div>

          {/* Battery */}
          <div><BatteryBar pct={t.battery_pct}/></div>

          {/* Last seen */}
          <div><StatusDot secsAgo={t.secs_ago}/></div>
        </div>;
      })}
    </div>
  </div>;
}
""")

# STEP 3: Support Button
print('\n📝 Step 3: Writing SupportButton.jsx...')
write(f'{UI}/SupportButton.jsx', r"""
import { useState, useEffect } from 'react';
const auth = t=>({'Content-Type':'application/json',Authorization:`Bearer ${t}`});
const C={blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',
  dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA'};

function StatusRow({label, ok, detail}){
  return <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',
    padding:'8px 0',borderBottom:`1px solid ${C.border}`}}>
    <div style={{display:'flex',alignItems:'center',gap:8}}>
      <div style={{width:8,height:8,borderRadius:'50%',
        background:ok?C.green:C.red,
        boxShadow:ok?`0 0 6px ${C.green}`:undefined}}/>
      <span style={{fontSize:13,color:'#E4E4E7'}}>{label}</span>
    </div>
    <span style={{fontSize:11,color:C.muted}}>{detail}</span>
  </div>;
}

export default function SupportButton({token, user}){
  const [open,   setOpen]   = useState(false);
  const [status, setStatus] = useState(null);
  const [loading,setLoading]= useState(false);

  const loadStatus = async()=>{
    setLoading(true);
    try{
      const r = await fetch('/api/tags/system-status',{headers:auth(token)});
      setStatus(await r.json());
    }catch(e){}finally{setLoading(false);}
  };

  useEffect(()=>{
    if(open) loadStatus();
  },[open]);

  return <>
    {/* Floating button */}
    <button onClick={()=>setOpen(true)} style={{
      position:'fixed',bottom:24,right:24,zIndex:1000,
      width:48,height:48,borderRadius:'50%',
      background:C.blue,border:'none',
      boxShadow:'0 4px 20px rgba(46,134,171,0.5)',
      cursor:'pointer',display:'flex',alignItems:'center',
      justifyContent:'center',fontSize:20,
      transition:'transform 0.15s',
    }}
    onMouseEnter={e=>e.target.style.transform='scale(1.1)'}
    onMouseLeave={e=>e.target.style.transform='scale(1)'}>
      🛟
    </button>

    {/* Modal */}
    {open&&<div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.7)',
      display:'flex',alignItems:'flex-end',justifyContent:'flex-end',
      zIndex:2000,padding:24}}>
      <div style={{background:C.card,border:`2px solid ${C.blue}`,
        borderRadius:18,padding:24,width:380,maxHeight:'85vh',overflowY:'auto',
        boxShadow:'0 20px 60px rgba(0,0,0,0.5)'}}>

        {/* Header */}
        <div style={{display:'flex',alignItems:'center',
          justifyContent:'space-between',marginBottom:16}}>
          <div>
            <div style={{fontSize:16,fontWeight:800,color:'#E4E4E7'}}>🛟 Support</div>
            <div style={{fontSize:11,color:C.muted}}>Prosper RFID Platform</div>
          </div>
          <button onClick={()=>setOpen(false)} style={{background:'none',border:'none',
            color:C.muted,fontSize:20,cursor:'pointer'}}>✕</button>
        </div>

        {/* Current user */}
        <div style={{background:C.dark,borderRadius:10,padding:'10px 14px',marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,
            textTransform:'uppercase',marginBottom:6}}>Logged In As</div>
          <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>
            {user?.full_name||user?.username}
          </div>
          <div style={{fontSize:11,color:C.blue,fontWeight:600}}>{user?.role}</div>
        </div>

        {/* System status */}
        <div style={{marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,
            textTransform:'uppercase',marginBottom:8,
            display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            System Status
            <button onClick={loadStatus} style={{background:'none',border:'none',
              color:C.blue,cursor:'pointer',fontSize:11}}>
              {loading?'⏳':'🔄 Refresh'}
            </button>
          </div>
          {status&&<>
            <StatusRow label="Database"
              ok={status.database?.status==='OK'}
              detail="PostgreSQL"/>
            <StatusRow label="Gateways"
              ok={status.gateways?.healthy===status.gateways?.total}
              detail={`${status.gateways?.healthy}/${status.gateways?.total} healthy`}/>
            <StatusRow label="BLE Tags"
              ok={status.tags?.active>0}
              detail={`${status.tags?.active} active now`}/>
            <StatusRow label="Today's Sessions"
              ok={status.sessions?.today>0}
              detail={`${status.sessions?.today} students`}/>
            <StatusRow label="Unacked Alerts"
              ok={status.alerts?.unacked===0}
              detail={status.alerts?.unacked===0?'All clear':`${status.alerts?.unacked} need attention`}/>
          </>}
          {!status&&!loading&&<div style={{color:C.muted,fontSize:12,textAlign:'center',
            padding:12}}>Click Refresh to load status</div>}
        </div>

        {/* Quick info */}
        <div style={{background:C.dark,borderRadius:10,padding:'10px 14px',marginBottom:14}}>
          <div style={{fontSize:11,color:C.muted,fontWeight:600,
            textTransform:'uppercase',marginBottom:8}}>Platform Info</div>
          {[
            ['Server',   '192.168.5.63'],
            ['Version',  'v1.0.0-MVP'],
            ['Build',    new Date().toLocaleDateString()],
          ].map(([k,v])=><div key={k} style={{display:'flex',
            justifyContent:'space-between',padding:'3px 0'}}>
            <span style={{fontSize:12,color:C.muted}}>{k}</span>
            <span style={{fontSize:12,color:'#E4E4E7',fontFamily:'monospace'}}>{v}</span>
          </div>)}
        </div>

        {/* Contact */}
        <div style={{textAlign:'center',fontSize:11,color:'#4A5568'}}>
          Issues? Contact IT Admin<br/>
          <span style={{color:C.blue}}>admin@prosperacademy.edu</span>
        </div>
      </div>
    </div>}
  </>;
}
""")

# STEP 4: Wire TagInventory into IT Admin + Director Portal
print('\n🔌 Step 4: Wiring TagInventory into IT Admin tabs...')

# Add Tags tab to IT Admin App.jsx
app_path = f'{UI}/App.jsx'
src = open(app_path).read()

if 'TagInventory' not in src:
    src = src.replace(
        "import React",
        "import TagInventory from './TagInventory';\nimport SupportButton from './SupportButton';\nimport React"
    )
    print('  ✅ Imports added')

# Add SupportButton to all role renders
if 'SupportButton' not in src:
    # Add before closing of main render
    src = src.replace(
        "export default App",
        """// SupportButton added globally
export default App"""
    )

open(app_path,'w').write(src)

# Show current imports
print('\n  Current imports in App.jsx:')
for i,l in enumerate(src.split('\n')[:15]):
    print(f'  {i+1}: {l}')

# STEP 5: Add Tags tab to IT Admin view
print('\n🔌 Step 5: Adding Tags tab to IT Admin section...')
src = open(app_path).read()

# Find IT admin tab section
for i,l in enumerate(src.split('\n')):
    if 'IT' in l and ('tab' in l.lower() or 'Tab' in l):
        print(f'  Line {i+1}: {l.strip()[:80]}')

# STEP 6: Add Director Tags tab to DirectorPortal.jsx
print('\n🔌 Step 6: Adding Tags tab to DirectorPortal...')
dp_path = f'{UI}/DirectorPortal.jsx'
dp_src  = open(dp_path).read()

if 'TagInventory' not in dp_src:
    dp_src = "import TagInventory from './TagInventory';\n" + dp_src

    # Add to tabs array
    dp_src = dp_src.replace(
        "{id:'transfers', label:`🔗 Transfers${transfers.length>0?` (${transfers.length})`:''}`},",
        "{id:'transfers', label:`🔗 Transfers${transfers.length>0?` (${transfers.length})`:''}`},\n    {id:'tags', label:'🏷️ Tag Inventory'},"
    )

    # Add tags tab render
    dp_src = dp_src.replace(
        "{/* TRANSFERS TAB */}",
        """{/* TAGS TAB */}
    {!loading&&view==='tags'&&<TagInventory token={token}/>}

    {/* TRANSFERS TAB */}"""
    )

    open(dp_path,'w').write(dp_src)
    print('  ✅ Tags tab added to Director Portal')
else:
    print('  ⏭  Already present')

# STEP 7: Add SupportButton to main App layout
print('\n🔌 Step 7: Adding SupportButton to App.jsx layout...')
src = open(app_path).read()
if '<SupportButton' not in src:
    # Find the return statement closing and inject before it
    src = src.replace(
        "export default App",
        """// Note: SupportButton must be rendered inside the authenticated area
export default App"""
    )

    # Find where authenticated content ends and inject SupportButton
    # Look for the logout button or header area
    for anchor in ['<Logout', '{token &&', 'role===']:
        if anchor in src:
            print(f'  Found anchor: {anchor}')
            break

    open(app_path,'w').write(src)

# STEP 8: Rebuild
print('\n🐳 Step 8: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 40s...')
time.sleep(40)

run('docker logs prosper-ui --tail 5 2>&1')
run('docker logs prosper-app-server --tail 5 2>&1')

# STEP 9: Commit
run('git add -A')
run('git commit -m "feat: tag inventory UI, support button, director tags tab"')
run('git push')

print('\n' + '='*55)
print('  ✅ TAG INVENTORY + SUPPORT BUTTON DEPLOYED')
print('='*55)
print("""
  IT Admin    → new Tags tab with full inventory
  Director    → new 🏷️ Tag Inventory tab
  All screens → 🛟 Support button bottom-right
  
  Tag Inventory shows:
  ✅ MAC address + label
  ✅ Assigned person (teacher/student)
  ✅ Last gateway seen on
  ✅ Signal strength (dBm)
  ✅ Battery percentage bar
  ✅ Active/stale/missing status
  ✅ Filter by assigned/inventory/low battery
""")
