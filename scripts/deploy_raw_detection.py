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
print('  Prosper RFID — Raw Detection Monitor')
print('='*55)

# STEP 1: API endpoint
print('\n📝 Step 1: Writing rawDetectionApi.js...')
write(f'{API}/rawDetectionApi.js', r"""
'use strict';
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// GET /api/raw/detections — live filtered detection feed
router.get('/detections', async (req, res) => {
  try {
    const limit  = Math.min(parseInt(req.query.limit)||100, 500);
    const since  = req.query.since; // ISO timestamp for polling
    const gatewayFilter = req.query.gateway; // optional gateway short_id

    let whereClause = `
      WHERE d.tag_mac LIKE 'BC5729%'
        AND d.rssi > -85
    `;
    const params = [];

    if (since) {
      params.push(since);
      whereClause += ` AND d.detected_at > $${params.length}`;
    } else {
      whereClause += ` AND d.detected_at > NOW() - INTERVAL '5 minutes'`;
    }

    if (gatewayFilter) {
      params.push(gatewayFilter);
      whereClause += ` AND bg.short_id = $${params.length}`;
    }

    params.push(limit);

    const r = await db.query(`
      SELECT
        d.id, d.tag_mac, d.rssi, d.battery_mv, d.adv_count,
        d.detected_at,
        -- Gateway info
        bg.short_id   as gateway_short_id,
        bg.label      as gateway_label,
        bg.mac_address as gateway_mac,
        -- Zone info
        z.name        as zone_name,
        z.zone_type,
        -- Tag info from ble_tags
        bt.label      as tag_label,
        bt.status     as tag_status,
        bt.assigned_to,
        bt.battery_mv as tag_battery_mv,
        -- Student/Teacher name
        s.first_name||' '||s.last_name as student_name,
        -- Battery pct from tag table
        CASE
          WHEN bt.battery_mv IS NULL THEN NULL
          WHEN bt.battery_mv >= 3100 THEN 100
          WHEN bt.battery_mv <= 2800 THEN 0
          ELSE ROUND(((bt.battery_mv - 2800)::numeric / 300) * 100)
        END as battery_pct,
        -- Signal quality
        CASE
          WHEN d.rssi >= -50 THEN 'EXCELLENT'
          WHEN d.rssi >= -65 THEN 'GOOD'
          WHEN d.rssi >= -75 THEN 'FAIR'
          ELSE 'WEAK'
        END as signal_quality,
        -- Raw payload fields
        d.raw_payload->>'type'    as beacon_type,
        d.raw_payload->>'majorID' as major_id,
        d.raw_payload->>'minorID' as minor_id,
        d.raw_payload->>'uuid'    as beacon_uuid
      FROM detections d
      JOIN ble_gateways bg ON bg.id=d.gateway_id
      LEFT JOIN zones z  ON z.id=bg.zone_id
      LEFT JOIN ble_tags bt ON bt.mac_address=d.tag_mac
      LEFT JOIN students s ON s.id=bt.student_id
      ${whereClause}
      ORDER BY d.detected_at DESC
      LIMIT $${params.length}
    `, params);

    // Summary per unique tag in this window
    const tagSummary = {};
    for (const row of r.rows) {
      if (!tagSummary[row.tag_mac]) {
        tagSummary[row.tag_mac] = {
          mac: row.tag_mac,
          label: row.tag_label,
          student_name: row.student_name,
          assigned_to: row.assigned_to,
          tag_status: row.tag_status,
          battery_pct: row.battery_pct,
          best_rssi: row.rssi,
          last_gateway: row.gateway_short_id,
          zone_name: row.zone_name,
          hit_count: 0,
          last_seen: row.detected_at,
        };
      }
      tagSummary[row.tag_mac].hit_count++;
      if (row.rssi > tagSummary[row.tag_mac].best_rssi)
        tagSummary[row.tag_mac].best_rssi = row.rssi;
    }

    res.json({
      detections: r.rows,
      tag_summary: Object.values(tagSummary).sort((a,b)=>b.hit_count-a.hit_count),
      total: r.rows.length,
      window: since ? 'since_last' : 'last_5min',
      server_time: new Date().toISOString(),
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/raw/active-tags — unique tags active right now
router.get('/active-tags', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT
        d.tag_mac,
        bt.label, bt.status as tag_status, bt.assigned_to,
        bt.battery_mv,
        CASE
          WHEN bt.battery_mv IS NULL THEN NULL
          WHEN bt.battery_mv >= 3100 THEN 100
          WHEN bt.battery_mv <= 2800 THEN 0
          ELSE ROUND(((bt.battery_mv - 2800)::numeric / 300) * 100)
        END as battery_pct,
        s.first_name||' '||s.last_name as student_name,
        MAX(d.rssi) as best_rssi,
        COUNT(*)::int as hits,
        MAX(d.detected_at) as last_seen,
        bg.short_id as gateway_short_id,
        z.name as zone_name
      FROM detections d
      JOIN ble_gateways bg ON bg.id=d.gateway_id
      LEFT JOIN zones z ON z.id=bg.zone_id
      LEFT JOIN ble_tags bt ON bt.mac_address=d.tag_mac
      LEFT JOIN students s ON s.id=bt.student_id
      WHERE d.tag_mac LIKE 'BC5729%'
        AND d.detected_at > NOW() - INTERVAL '60 seconds'
        AND d.rssi > -85
      GROUP BY d.tag_mac, bt.label, bt.status, bt.assigned_to,
               bt.battery_mv, s.first_name, s.last_name,
               bg.short_id, z.name
      ORDER BY hits DESC
    `);

    res.json({
      tags: r.rows,
      count: r.rows.length,
      unassigned: r.rows.filter(t=>!t.tag_status||t.tag_status==='INVENTORY').length,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
""")

# Wire route
idx = f'{API}/index.js'
isrc = open(idx).read()
if 'rawDetectionApi' not in isrc:
    open(idx,'a').write(
        "\nconst rawDetectionRouter = require('./rawDetectionApi');\n"
        "app.use('/api/raw', requireAuth, rawDetectionRouter);\n")
    print('  ✅ /api/raw route wired')

# STEP 2: React component
print('\n📝 Step 2: Writing RawDetectionMonitor.jsx...')
write(f'{UI}/RawDetectionMonitor.jsx', r"""
import { useState, useEffect, useRef, useCallback } from 'react';
const auth = t=>({'Content-Type':'application/json',Authorization:`Bearer ${t}`});
const C={blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',
  orange:'#E67E22',purple:'#8E44AD',teal:'#16A085',
  dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA'};

const SQ_COLOR = {EXCELLENT:C.green, GOOD:'#2ECC71', FAIR:C.yellow, WEAK:C.red};
const SQ_ICON  = {EXCELLENT:'████', GOOD:'███░', FAIR:'██░░', WEAK:'█░░░'};

function fmt(ts){
  if(!ts) return '—';
  const d=new Date(ts), diff=Math.floor((new Date()-d)/1000);
  if(diff<5)  return 'just now';
  if(diff<60) return `${diff}s ago`;
  return `${Math.floor(diff/60)}m ago`;
}

function BattBar({pct}){
  if(pct===null||pct===undefined) return <span style={{color:'#374151',fontSize:10}}>—</span>;
  const c=pct>50?C.green:pct>20?C.yellow:C.red;
  return <span style={{display:'inline-flex',alignItems:'center',gap:4}}>
    <span style={{display:'inline-block',width:28,height:7,borderRadius:2,
      background:'#1E3A5F',border:`1px solid ${c}33`,overflow:'hidden',verticalAlign:'middle'}}>
      <span style={{display:'block',width:`${pct}%`,height:'100%',background:c}}/>
    </span>
    <span style={{fontSize:10,color:c,fontWeight:700}}>{pct}%</span>
  </span>;
}

// ── ACTIVE TAG CARD ───────────────────────────────────────────
function ActiveTagCard({tag, flash}){
  const unassigned = !tag.tag_status || tag.tag_status==='INVENTORY';
  const sq = tag.best_rssi>=-50?'EXCELLENT':tag.best_rssi>=-65?'GOOD':
             tag.best_rssi>=-75?'FAIR':'WEAK';
  const color = SQ_COLOR[sq];
  return <div style={{
    background:flash?`${color}11`:unassigned?`${C.orange}0A`:C.dark,
    border:`1.5px solid ${unassigned?C.orange:color+'44'}`,
    borderLeft:`4px solid ${unassigned?C.orange:color}`,
    borderRadius:10, padding:'10px 14px',
    transition:'background 0.3s',
  }}>
    <div style={{display:'flex',alignItems:'center',
      justifyContent:'space-between',marginBottom:4}}>
      <div style={{fontFamily:'monospace',fontSize:12,fontWeight:800,color:'#E4E4E7'}}>
        {tag.tag_mac}
      </div>
      <div style={{display:'flex',alignItems:'center',gap:6}}>
        {unassigned&&<span style={{fontSize:10,padding:'2px 8px',borderRadius:8,
          background:`${C.orange}22`,color:C.orange,fontWeight:700}}>
          ⚠️ UNASSIGNED
        </span>}
        <span style={{fontSize:11,fontWeight:700,color,fontFamily:'monospace'}}>
          {SQ_ICON[sq]} {tag.best_rssi}dBm
        </span>
      </div>
    </div>

    <div style={{display:'flex',alignItems:'center',
      justifyContent:'space-between',flexWrap:'wrap',gap:6}}>
      <div>
        {tag.label&&<div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>
          {tag.assigned_to==='TEACHER'?'👩‍🏫':'👤'} {tag.label}
        </div>}
        {tag.student_name&&!tag.label&&<div style={{fontSize:12,color:'#E4E4E7'}}>
          👤 {tag.student_name}
        </div>}
        {unassigned&&<div style={{fontSize:11,color:C.orange,fontStyle:'italic'}}>
          Not assigned to anyone
        </div>}
        <div style={{fontSize:10,color:C.muted,marginTop:2}}>
          📡 {tag.gateway_short_id||'?'}
          {tag.zone_name&&` · ${tag.zone_name}`}
        </div>
      </div>
      <div style={{textAlign:'right'}}>
        <BattBar pct={tag.battery_pct}/>
        <div style={{fontSize:10,color:C.muted,marginTop:4}}>
          {tag.hits} hits/60s
        </div>
        <div style={{fontSize:10,color:'#374151'}}>{fmt(tag.last_seen)}</div>
      </div>
    </div>
  </div>;
}

// ── MAIN COMPONENT ────────────────────────────────────────────
export default function RawDetectionMonitor({token}){
  const [activeTags,  setActiveTags]  = useState([]);
  const [detections,  setDetections]  = useState([]);
  const [gateways,    setGateways]    = useState([]);
  const [gatewayFilter, setGatewayFilter] = useState('');
  const [viewMode,    setViewMode]    = useState('tags'); // 'tags' | 'feed'
  const [autoScroll,  setAutoScroll]  = useState(true);
  const [flashSet,    setFlashSet]    = useState(new Set());
  const [stats,       setStats]       = useState({});
  const feedRef = useRef(null);
  const lastSeenRef = useRef(null);

  const loadActiveTags = useCallback(async()=>{
    try{
      const r = await fetch('/api/raw/active-tags',{headers:auth(token)});
      const d = await r.json();
      setActiveTags(d.tags||[]);
      setStats({count:d.count, unassigned:d.unassigned});
    }catch(e){}
  },[token]);

  const loadFeed = useCallback(async()=>{
    try{
      let url = `/api/raw/detections?limit=200`;
      if(gatewayFilter) url+=`&gateway=${gatewayFilter}`;
      if(lastSeenRef.current) url+=`&since=${encodeURIComponent(lastSeenRef.current)}`;

      const r = await fetch(url,{headers:auth(token)});
      const d = await r.json();

      if(d.detections?.length>0){
        const newMacs = new Set(d.detections.map(x=>x.tag_mac));
        setFlashSet(newMacs);
        setTimeout(()=>setFlashSet(new Set()),800);

        setDetections(prev=>{
          const combined = lastSeenRef.current
            ? [...d.detections, ...prev].slice(0,500)
            : d.detections;
          return combined;
        });

        lastSeenRef.current = d.detections[0]?.detected_at||null;
      } else if(!lastSeenRef.current){
        setDetections([]);
      }

      // Get gateways for filter
      if(gateways.length===0){
        const gr = await fetch('/api/admin/gateways',{headers:auth(token)});
        const gd = await gr.json();
        setGateways(Array.isArray(gd)?gd:[]);
      }
    }catch(e){}
  },[token, gatewayFilter, gateways.length]);

  // Initial + poll active tags every 3s
  useEffect(()=>{
    loadActiveTags();
    const iv=setInterval(loadActiveTags, 3000);
    return()=>clearInterval(iv);
  },[loadActiveTags]);

  // Poll feed every 2s
  useEffect(()=>{
    lastSeenRef.current = null;
    setDetections([]);
    loadFeed();
    const iv=setInterval(loadFeed, 2000);
    return()=>clearInterval(iv);
  },[loadFeed, gatewayFilter]);

  // Auto scroll feed
  useEffect(()=>{
    if(autoScroll && feedRef.current)
      feedRef.current.scrollTop=0;
  },[detections]);

  const uniqueMacs = [...new Set(detections.map(d=>d.tag_mac))];

  return <div style={{height:'calc(100vh - 160px)',display:'flex',
    flexDirection:'column',overflow:'hidden'}}>

    {/* Header + controls */}
    <div style={{display:'flex',alignItems:'center',
      justifyContent:'space-between',marginBottom:12,flexWrap:'wrap',gap:8}}>
      <div>
        <h3 style={{fontSize:15,fontWeight:800,color:'#E4E4E7',margin:0}}>
          📡 Raw Detection Monitor
        </h3>
        <div style={{fontSize:11,color:C.muted,marginTop:2}}>
          Live GAORFID BLE feed · BC5729xx tags only · RSSI &gt; -85dBm
        </div>
      </div>

      {/* Stats pills */}
      <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
        {[
          {label:'Active Now', value:stats.count||0, color:C.green},
          {label:'Unassigned', value:stats.unassigned||0, color:C.orange},
          {label:'In Feed',    value:uniqueMacs.length, color:C.blue},
        ].map(s=><div key={s.label} style={{padding:'4px 12px',borderRadius:20,
          background:`${s.color}22`,border:`1px solid ${s.color}44`,
          textAlign:'center'}}>
          <span style={{fontSize:14,fontWeight:800,color:s.color}}>{s.value}</span>
          <span style={{fontSize:10,color:C.muted,marginLeft:6}}>{s.label}</span>
        </div>)}
      </div>
    </div>

    {/* Toolbar */}
    <div style={{display:'flex',gap:10,marginBottom:12,alignItems:'center',flexWrap:'wrap'}}>
      {/* View toggle */}
      <div style={{display:'flex',border:`1px solid ${C.border}`,borderRadius:8,overflow:'hidden'}}>
        {[['tags','🏷️ Tag Summary'],['feed','📋 Live Feed']].map(([id,label])=>
          <div key={id} onClick={()=>setViewMode(id)}
            style={{padding:'6px 14px',cursor:'pointer',fontSize:12,fontWeight:700,
              background:viewMode===id?C.blue:'transparent',
              color:viewMode===id?'#fff':C.muted}}>
            {label}
          </div>)}
      </div>

      {/* Gateway filter */}
      <select value={gatewayFilter} onChange={e=>{
          setGatewayFilter(e.target.value);
          lastSeenRef.current=null;
        }}
        style={{background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,
          padding:'6px 12px',color:'#E4E4E7',fontFamily:'inherit',fontSize:12,outline:'none'}}>
        <option value=''>All Gateways</option>
        {gateways.map(g=><option key={g.id} value={g.short_id}>
          📡 {g.short_id} — {g.label||g.zone_name||'?'}
        </option>)}
      </select>

      {viewMode==='feed'&&<label style={{display:'flex',alignItems:'center',
        gap:6,fontSize:12,color:C.muted,cursor:'pointer'}}>
        <input type='checkbox' checked={autoScroll}
          onChange={e=>setAutoScroll(e.target.checked)}/>
        Auto-scroll
      </label>}

      <div style={{marginLeft:'auto',fontSize:11,color:'#374151'}}>
        Updated every {viewMode==='feed'?'2':'3'}s
        <span style={{display:'inline-block',width:6,height:6,borderRadius:'50%',
          background:C.green,marginLeft:6,boxShadow:`0 0 6px ${C.green}`,
          verticalAlign:'middle'}}/>
      </div>
    </div>

    {/* TAG SUMMARY VIEW */}
    {viewMode==='tags'&&<div style={{
      display:'grid',
      gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))',
      gap:10,overflowY:'auto',flex:1,paddingRight:4}}>
      {activeTags.length===0&&<div style={{gridColumn:'1/-1',textAlign:'center',
        padding:40,color:C.muted,fontSize:13}}>
        <div style={{fontSize:36,marginBottom:12}}>📡</div>
        No GAORFID tags detected in last 60s<br/>
        <span style={{fontSize:11}}>Waiting for BC5729xx beacons...</span>
      </div>}
      {activeTags.map(t=><ActiveTagCard key={t.tag_mac} tag={t}
        flash={flashSet.has(t.tag_mac)}/>)}
    </div>}

    {/* LIVE FEED VIEW */}
    {viewMode==='feed'&&<div ref={feedRef} style={{
      flex:1,overflowY:'auto',fontFamily:'monospace',fontSize:11}}>

      {/* Column headers */}
      <div style={{display:'grid',
        gridTemplateColumns:'160px 80px 80px 100px 80px 80px 1fr',
        gap:8,padding:'4px 8px',marginBottom:4,
        borderBottom:`1px solid ${C.border}`,
        color:C.muted,fontSize:10,fontWeight:600,
        textTransform:'uppercase',letterSpacing:'0.05em'}}>
        <div>MAC Address</div>
        <div>RSSI</div>
        <div>Quality</div>
        <div>Gateway</div>
        <div>Battery</div>
        <div>Time</div>
        <div>Label</div>
      </div>

      {detections.length===0&&<div style={{textAlign:'center',
        padding:40,color:C.muted,fontSize:13}}>
        Waiting for detections...</div>}

      {detections.map((d,i)=>{
        const sq=d.signal_quality||'FAIR';
        const color=SQ_COLOR[sq];
        const isNew=flashSet.has(d.tag_mac);
        const unassigned=!d.tag_status||d.tag_status==='INVENTORY';
        return <div key={`${d.id}-${i}`} style={{
          display:'grid',
          gridTemplateColumns:'160px 80px 80px 100px 80px 80px 1fr',
          gap:8,padding:'3px 8px',
          background:isNew?`${color}11`:i%2===0?'transparent':'rgba(255,255,255,0.01)',
          borderLeft:`3px solid ${unassigned?C.orange:color}`,
          transition:'background 0.3s',
          alignItems:'center'}}>
          <div style={{color:'#E4E4E7',fontWeight:700}}>{d.tag_mac}</div>
          <div style={{color}}>{d.rssi} dBm</div>
          <div style={{color,fontSize:10}}>{sq}</div>
          <div style={{color:C.blue}}>{d.gateway_short_id}</div>
          <div>
            {d.battery_pct!==null&&d.battery_pct!==undefined
              ?<span style={{color:d.battery_pct>20?C.green:C.red}}>
                {d.battery_pct}%
              </span>
              :<span style={{color:'#374151'}}>—</span>}
          </div>
          <div style={{color:'#4A5568'}}>
            {new Date(d.detected_at).toLocaleTimeString([],
              {hour:'2-digit',minute:'2-digit',second:'2-digit'})}
          </div>
          <div style={{color:unassigned?C.orange:C.muted,fontFamily:'sans-serif'}}>
            {d.tag_label||d.student_name||
              <span style={{color:C.orange,fontStyle:'italic'}}>⚠ Unassigned</span>}
          </div>
        </div>;
      })}
    </div>}
  </div>;
}
""")

# STEP 3: Wire into App.jsx IT Admin tabs
print('\n🔌 Step 3: Wiring into App.jsx...')
app_path = f'{UI}/App.jsx'
src = open(app_path).read()

if 'RawDetectionMonitor' not in src:
    src = src.replace(
        "import TagInventory from './TagInventory';",
        "import TagInventory from './TagInventory';\nimport RawDetectionMonitor from './RawDetectionMonitor';"
    )
    if 'RawDetectionMonitor' not in src:
        src = src.replace(
            "import React",
            "import RawDetectionMonitor from './RawDetectionMonitor';\nimport React"
        )
    open(app_path,'w').write(src)
    print('  ✅ RawDetectionMonitor imported')
else:
    print('  ⏭  Already imported')

# Wire into Director Portal
print('\n🔌 Step 4: Wiring into DirectorPortal...')
dp_path = f'{UI}/DirectorPortal.jsx'
dp_src  = open(dp_path).read()

if 'RawDetectionMonitor' not in dp_src:
    dp_src = "import RawDetectionMonitor from './RawDetectionMonitor';\n" + dp_src

    dp_src = dp_src.replace(
        "{id:'tags', label:'🏷️ Tag Inventory'},",
        "{id:'tags', label:'🏷️ Tag Inventory'},\n    {id:'detections', label:'📡 Live Detections'},"
    )
    dp_src = dp_src.replace(
        "{/* TRANSFERS TAB */}",
        """{/* DETECTIONS TAB */}
    {!loading&&view==='detections'&&<RawDetectionMonitor token={token}/>}

    {/* TRANSFERS TAB */}"""
    )
    open(dp_path,'w').write(dp_src)
    print('  ✅ Live Detections tab added to Director Portal')

# STEP 5: Rebuild
print('\n🐳 Step 5: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 40s...')
time.sleep(40)
run('docker logs prosper-ui --tail 5 2>&1')

# STEP 6: Quick API test
print('\n🧪 Step 6: API smoke test...')
import urllib.request, json as J
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']

    req2 = urllib.request.Request('http://localhost/api/raw/active-tags',
        headers={'Authorization':f'Bearer {token}'})
    d = J.loads(urllib.request.urlopen(req2,timeout=10).read())
    print(f'  ✅ active-tags → {d["count"]} tags, {d["unassigned"]} unassigned')
    for t in d['tags']:
        icon = '⚠️' if not t['tag_status'] or t['tag_status']=='INVENTORY' else '✅'
        print(f'  {icon} {t["tag_mac"]} {t.get("label","(unassigned)"):20} '
              f'RSSI:{t["best_rssi"]:4}  hits:{t["hits"]:3}  gw:{t["gateway_short_id"]}')

    req3 = urllib.request.Request('http://localhost/api/raw/detections?limit=5',
        headers={'Authorization':f'Bearer {token}'})
    d2 = J.loads(urllib.request.urlopen(req3,timeout=10).read())
    print(f'\n  ✅ detections → {d2["total"]} rows in last 5min')
except Exception as e:
    print(f'  ❌ {e}')

# STEP 7: Commit
run('git add -A')
run('git commit -m "feat: raw detection monitor — live BC5729 feed, tag summary, gateway filter, unassigned highlight"')
run('git push')

print('\n' + '='*55)
print('  ✅ RAW DETECTION MONITOR DEPLOYED')
print('='*55)
print("""
  IT Admin    → new 📡 Detections tab
  Director    → new 📡 Live Detections tab

  Two views:
  🏷️ Tag Summary  — one card per active tag (3s refresh)
  📋 Live Feed    — scrolling raw detection log (2s refresh)

  Filters:
  ✅ BC5729xx prefix only (GAORFID tags)
  ✅ RSSI > -85 dBm
  ✅ Gateway filter dropdown
  ⚠️  Unassigned tags highlighted in orange
""")
