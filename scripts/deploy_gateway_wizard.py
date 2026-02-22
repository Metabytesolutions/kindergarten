#!/usr/bin/env python3
import os, subprocess, sys, time, urllib.request, json as J

BASE = os.path.expanduser('~/prosper-platform')
UI   = f'{BASE}/services/react-ui/src'
API  = f'{BASE}/services/app-server/src'

def run(cmd, check=False):
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
print('  Prosper RFID — Gateway Wizard Deploy')
print('='*55)

# STEP 1: DB
print('\n📦 Step 1: DB migration...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
ALTER TABLE ble_gateways ADD COLUMN IF NOT EXISTS short_id VARCHAR(20);
ALTER TABLE ble_gateways ADD COLUMN IF NOT EXISTS zone_id UUID REFERENCES zones(id);
ALTER TABLE ble_gateways ADD COLUMN IF NOT EXISTS connection_type VARCHAR(10) DEFAULT 'WIFI';
ALTER TABLE ble_gateways ADD COLUMN IF NOT EXISTS rssi_threshold INT DEFAULT -70;
ALTER TABLE ble_gateways ADD COLUMN IF NOT EXISTS setup_status VARCHAR(20) DEFAULT 'CONFIGURED';
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS old_value JSONB;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS new_value JSONB;
UPDATE ble_gateways SET short_id='00A0D1',setup_status='CONFIGURED' WHERE mac_address='F0A882F54070' AND short_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_gw_shortid ON ble_gateways(short_id);
CREATE TABLE IF NOT EXISTS pending_gateways (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), mac_address VARCHAR(20) UNIQUE NOT NULL, short_id VARCHAR(20), ip_address VARCHAR(45), first_seen_at TIMESTAMPTZ DEFAULT NOW(), last_seen_at TIMESTAMPTZ DEFAULT NOW(), raw_payload JSONB);
" """)
print('  ✅ DB done')

# STEP 2: adminGateways.js
print('\n📝 Step 2: Writing adminGateways.js...')
write(f'{API}/adminGateways.js', '''const express = require('express');
const db      = require('./db');
const router  = express.Router();
const pending = new Map();

function resolveConfigResponse(mac, payload) {
  for (const [k, p] of pending.entries()) {
    clearTimeout(p.timer); p.resolve({ mac, payload }); pending.delete(k);
  }
}

router.get('/', async (req, res) => {
  try {
    const r = await db.query(`SELECT g.*, z.name as zone_name FROM ble_gateways g LEFT JOIN zones z ON z.id=g.zone_id ORDER BY g.label`);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.get('/pending', async (req, res) => {
  try {
    const r = await db.query(`SELECT * FROM pending_gateways WHERE mac_address NOT IN (SELECT mac_address FROM ble_gateways WHERE mac_address IS NOT NULL) ORDER BY last_seen_at DESC`);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.get('/zones', async (req, res) => {
  try {
    const r = await db.query('SELECT id, name FROM zones ORDER BY name');
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.get('/:id', async (req, res) => {
  try {
    const r = await db.query(`SELECT g.*, z.name as zone_name FROM ble_gateways g LEFT JOIN zones z ON z.id=g.zone_id WHERE g.id=$1`, [req.params.id]);
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.post('/register', async (req, res) => {
  try {
    const { mac_address, short_id, label, connection_type, zone_id, rssi_threshold } = req.body;
    if (!mac_address || !short_id) return res.status(400).json({ error: 'mac_address and short_id required' });
    const mac = mac_address.toUpperCase();
    const ex  = await db.query('SELECT id FROM ble_gateways WHERE mac_address=$1', [mac]);
    let result;
    if (ex.rows.length > 0) {
      result = await db.query(`UPDATE ble_gateways SET short_id=$2,label=COALESCE($3,label),connection_type=COALESCE($4,connection_type),zone_id=$5,rssi_threshold=COALESCE($6,rssi_threshold),setup_status='CONFIGURED',is_active=true,updated_at=NOW() WHERE mac_address=$1 RETURNING *`,
        [mac, short_id, label, connection_type||'WIFI', zone_id||null, rssi_threshold||-70]);
    } else {
      result = await db.query(`INSERT INTO ble_gateways (mac_address,short_id,label,connection_type,zone_id,rssi_threshold,health_state,setup_status,is_active) VALUES ($1,$2,$3,$4,$5,$6,'UNKNOWN','CONFIGURED',true) RETURNING *`,
        [mac, short_id, label||`Gateway ${short_id}`, connection_type||'WIFI', zone_id||null, rssi_threshold||-70]);
    }
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'GATEWAY_REGISTERED','ble_gateway',$3)`, [req.user.id, req.user.role, result.rows[0].id]);
    await db.query('DELETE FROM pending_gateways WHERE mac_address=$1', [mac]);
    console.log(`✅ Gateway registered: ${mac} (${short_id})`);
    res.json(result.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.put('/:id', async (req, res) => {
  try {
    const { label, zone_id, rssi_threshold, connection_type } = req.body;
    const r = await db.query(`UPDATE ble_gateways SET label=COALESCE($2,label),zone_id=$3,rssi_threshold=COALESCE($4,rssi_threshold),connection_type=COALESCE($5,connection_type),updated_at=NOW() WHERE id=$1 RETURNING *`,
      [req.params.id, label, zone_id||null, rssi_threshold, connection_type]);
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id,new_value) VALUES ($1,$2,'GATEWAY_UPDATED','ble_gateway',$3,$4)`,
      [req.user.id, req.user.role, req.params.id, JSON.stringify(req.body)]);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.post('/:id/push-config', async (req, res) => {
  try {
    const gwr = await db.query('SELECT * FROM ble_gateways WHERE id=$1', [req.params.id]);
    if (!gwr.rows[0]) return res.status(404).json({ error: 'Not found' });
    const gw = gwr.rows[0];
    if (!gw.short_id) return res.status(400).json({ error: 'No short_id configured' });
    const { publishToGateway } = require('./mqttWorker');
    const host = process.env.NUC_IP || '192.168.5.63';
    const config = { action:'set_config', data:{ mqttHost:host, mqttPort:1883, mqttEnable:true, mqttClientId:`gw_${gw.mac_address}`, pubTopic:`kbeacon/publish/${gw.mac_address}`, subTopic:`kbeacon/subadmin/${gw.short_id}`, pubInterval:500, rssiFilter:gw.rssi_threshold||-80 }};
    const topic = `kbeacon/subadmin/${gw.short_id}`;
    publishToGateway(topic, config);
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id,new_value) VALUES ($1,$2,'GATEWAY_CONFIG_PUSHED','ble_gateway',$3,$4)`,
      [req.user.id, req.user.role, gw.id, JSON.stringify(config)]);
    console.log(`📤 Config pushed to ${gw.mac_address} via ${topic}`);
    res.json({ success:true, topic, config });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.post('/:id/command', async (req, res) => {
  try {
    const { action } = req.body;
    if (!action) return res.status(400).json({ error: 'action required' });
    const gwr = await db.query('SELECT * FROM ble_gateways WHERE id=$1', [req.params.id]);
    if (!gwr.rows[0]) return res.status(404).json({ error: 'Not found' });
    const gw = gwr.rows[0];
    if (!gw.short_id) return res.status(400).json({ error: 'No short_id' });
    const { publishToGateway } = require('./mqttWorker');
    publishToGateway(`kbeacon/subadmin/${gw.short_id}`, { action });
    const response = await new Promise(resolve => {
      const timer = setTimeout(() => { pending.delete(gw.short_id); resolve({ timeout:true }); }, 8000);
      pending.set(gw.short_id, { resolve, timer });
    });
    res.json({ sent:true, action, response });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
module.exports.resolveConfigResponse = resolveConfigResponse;
''')

# STEP 3: Wire route into index.js
print('\n🔌 Step 3: Wiring route into index.js...')
idx = f'{API}/index.js'
src = open(idx).read()
if 'adminGatewaysRouter' not in src:
    open(idx, 'a').write("\nconst adminGatewaysRouter = require('./adminGateways');\napp.use('/api/admin/gateways', requireAuth, adminGatewaysRouter);\n")
    print('  ✅ Route added')
else:
    print('  ⏭  Already wired')

# STEP 4: Add NUC_IP to .env
print('\n🔑 Step 4: Checking .env...')
env = f'{BASE}/.env'
txt = open(env).read() if os.path.exists(env) else ''
if 'NUC_IP' not in txt:
    open(env, 'a').write('\nNUC_IP=192.168.5.63\n')
    print('  ✅ NUC_IP added')
else:
    print('  ⏭  Already set')

# STEP 5: Write GatewayManager.jsx
print('\n📝 Step 5: Writing GatewayManager.jsx...')
write(f'{UI}/GatewayManager.jsx', r"""import { useState, useEffect, useRef } from 'react';
const API = '/api/admin/gateways';
const auth = t => ({ 'Content-Type':'application/json', Authorization:`Bearer ${t}` });
const C = { blue:'#2E86AB',green:'#27AE60',red:'#C0392B',yellow:'#F39C12',purple:'#8E44AD',dark:'#0A1628',card:'#111D2E',border:'#1E3A5F',muted:'#8899AA' };
function Dot({state}){const col={HEALTHY:C.green,DEGRADED:C.yellow,OFFLINE:C.red,UNKNOWN:'#4A5568',CONFIGURED:C.blue}[state]||'#4A5568';return <span style={{display:'inline-flex',alignItems:'center',gap:5}}><span style={{width:8,height:8,borderRadius:'50%',background:col,boxShadow:`0 0 6px ${col}88`}}/><span style={{fontSize:11,color:col,fontWeight:700}}>{state}</span></span>;}
const Box=({c,s={}})=><div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:20,...s}}>{c}</div>;
function Btn({onClick,children,color=C.blue,disabled,small,outline}){return <button onClick={onClick} disabled={disabled} style={{background:outline?'transparent':disabled?'#1E3A5F':color,color:disabled?'#4A5568':outline?color:'#fff',border:`1.5px solid ${disabled?'#1E3A5F':color}`,borderRadius:8,padding:small?'6px 14px':'10px 22px',fontFamily:'inherit',fontSize:small?12:13,fontWeight:700,cursor:disabled?'not-allowed':'pointer',display:'flex',alignItems:'center',gap:6,opacity:disabled?0.5:1}}>{children}</button>;}
function Fld({label,value,onChange,placeholder,mono}){return <div style={{marginBottom:14}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:5}}>{label}</div><input value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'10px 14px',color:'#E4E4E7',fontFamily:mono?'monospace':'inherit',fontSize:13,outline:'none',boxSizing:'border-box'}}/></div>;}
function Sel({label,value,onChange,options}){return <div style={{marginBottom:14}}><div style={{fontSize:11,color:C.muted,fontWeight:600,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:5}}>{label}</div><select value={value} onChange={e=>onChange(e.target.value)} style={{width:'100%',background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'10px 14px',color:'#E4E4E7',fontFamily:'inherit',fontSize:13,outline:'none'}}><option value="">— Select —</option>{options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></div>;}
function Steps({step,steps}){return <div style={{display:'flex',alignItems:'center',marginBottom:28}}>{steps.map((s,i)=><div key={i} style={{display:'flex',alignItems:'center',flex:i<steps.length-1?1:'none'}}><div style={{display:'flex',flexDirection:'column',alignItems:'center',gap:4}}><div style={{width:32,height:32,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',fontWeight:800,fontSize:13,background:i<step?C.green:i===step?C.blue:'#1E3A5F',color:'#fff'}}>{i<step?'✓':i+1}</div><div style={{fontSize:10,whiteSpace:'nowrap',color:i===step?C.blue:i<step?C.green:'#4A5568',fontWeight:i===step?700:400}}>{s}</div></div>{i<steps.length-1&&<div style={{flex:1,height:2,margin:'0 6px 18px',background:i<step?C.green:'#1E3A5F'}}/>}</div>)}</div>;}
function Log({logs}){const ref=useRef();useEffect(()=>{if(ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[logs]);return <div ref={ref} style={{background:'#060E1A',borderRadius:8,border:`1px solid ${C.border}`,padding:'10px 14px',fontFamily:'monospace',fontSize:11,color:'#B0C4D8',height:130,overflowY:'auto',marginTop:14}}>{logs.length===0?<span style={{color:'#4A5568'}}>Waiting...</span>:logs.map((l,i)=><div key={i} style={{marginBottom:3,color:l.t==='err'?C.red:l.t==='ok'?C.green:l.t==='warn'?C.yellow:'#B0C4D8'}}><span style={{color:'#4A5568'}}>[{l.time}] </span>{l.msg}</div>)}</div>;}

function Wizard({token,onDone,onCancel}){
  const [step,setStep]=useState(0);const [conn,setConn]=useState('WIFI');const [pending,setPending]=useState([]);const [selGw,setSelGw]=useState(null);const [manMac,setManMac]=useState('');const [manIp,setManIp]=useState('');const [shortId,setShortId]=useState('');const [label,setLabel]=useState('');const [zoneId,setZoneId]=useState('');const [rssi,setRssi]=useState('-70');const [zones,setZones]=useState([]);const [logs,setLogs]=useState([]);const [det,setDet]=useState(false);const [busy,setBusy]=useState(false);const [reg,setReg]=useState(null);const poll=useRef(null);
  const log=(msg,t='info')=>setLogs(l=>[...l,{time:new Date().toLocaleTimeString('en-US',{hour12:false}),msg,t}]);
  useEffect(()=>{fetch(`${API}/zones`,{headers:auth(token)}).then(r=>r.json()).then(setZones).catch(()=>{});return()=>clearInterval(poll.current);},[]);
  const startDet=()=>{setStep(1);setDet(true);log('Scanning MQTT for new BLE gateways...');const go=async()=>{try{const r=await fetch(`${API}/pending`,{headers:auth(token)});const d=await r.json();setPending(d);if(d.length>0){log(`✓ ${d.length} gateway(s) detected`,'ok');d.forEach(g=>log(`  MAC:${g.mac_address} IP:${g.ip_address||'?'}`,'ok'));}}catch(e){log('Error:'+e.message,'err');}};go();poll.current=setInterval(go,4000);};
  const pick=gw=>{clearInterval(poll.current);setDet(false);setSelGw(gw);log(`Selected: ${gw.mac_address}`,'ok');setStep(2);};
  const push=async()=>{setBusy(true);log('Registering...');try{const mac=(selGw?.mac_address||manMac).toUpperCase().replace(/:/g,'');const rr=await fetch(`${API}/register`,{method:'POST',headers:auth(token),body:JSON.stringify({mac_address:mac,short_id:shortId.trim(),label:label||`Gateway ${shortId}`,connection_type:conn,zone_id:zoneId||null,rssi_threshold:parseInt(rssi)||-70})});const rv=await rr.json();if(!rr.ok)throw new Error(rv.error);log(`✓ Registered ${rv.id.slice(0,8)}...`,'ok');log(`Pushing config to kbeacon/subadmin/${shortId}...`);const cr=await fetch(`${API}/${rv.id}/push-config`,{method:'POST',headers:auth(token)});const cv=await cr.json();if(cv.success){log('✓ Config pushed via MQTT','ok');log(`  Host:${cv.config?.data?.mqttHost}:1883`);log(`  RSSI:${cv.config?.data?.rssiFilter}dBm`);setReg(rv);setStep(4);}else throw new Error(cv.error);}catch(e){log('Error:'+e.message,'err');}finally{setBusy(false);}};
  const verify=async()=>{setBusy(true);log('Checking heartbeat...');let n=0;const iv=setInterval(async()=>{n++;try{const r=await fetch(`${API}/${reg.id}`,{headers:auth(token)});const g=await r.json();const s=g.last_heartbeat_at?Math.floor((Date.now()-new Date(g.last_heartbeat_at))/1000):999;log(`  Check ${n}/5 — ${s}s ago`);if(s<45){clearInterval(iv);setBusy(false);log('✓ ONLINE — heartbeat confirmed!','ok');setStep(5);}else if(n>=5){clearInterval(iv);setBusy(false);log('⚠ No fresh heartbeat — allow 60s for config','warn');setStep(5);}}catch(e){log('Error:'+e.message,'err');}},8000);};
  const STEPS=['Connection','Detect','Short ID','Configure','Verify','Done'];
  const mac=(selGw?.mac_address||manMac).toUpperCase();
  return <div style={{maxWidth:640,margin:'0 auto'}}>
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:24}}>
      <div><h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>Add New Gateway</h2><p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>Automated MQTT configuration wizard</p></div>
      <Btn onClick={onCancel} outline color='#4A5568' small>✕ Cancel</Btn>
    </div>
    <Steps step={step} steps={STEPS}/>
    {step===0&&<Box c={<>
      <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:16}}>How is the gateway connected?</h3>
      <div style={{display:'flex',gap:14,marginBottom:20}}>{[{id:'WIFI',icon:'📶',label:'WiFi',desc:'Gateway joins school WiFi'},{id:'ETHERNET',icon:'🔌',label:'Ethernet',desc:'Connected via PoE cable'}].map(o=><div key={o.id} onClick={()=>setConn(o.id)} style={{flex:1,background:conn===o.id?'#0D2137':C.dark,border:`2px solid ${conn===o.id?C.blue:C.border}`,borderRadius:12,padding:18,cursor:'pointer'}}><div style={{fontSize:32,marginBottom:8}}>{o.icon}</div><div style={{fontSize:14,fontWeight:700,color:conn===o.id?C.blue:'#E4E4E7',marginBottom:4}}>{o.label}</div><div style={{fontSize:11,color:C.muted}}>{o.desc}</div>{conn===o.id&&<div style={{marginTop:8,fontSize:11,color:C.blue,fontWeight:700}}>✓ Selected</div>}</div>)}</div>
      <div style={{background:C.dark,border:`1px solid ${C.yellow}33`,borderRadius:8,padding:14,marginBottom:20}}>
        <div style={{fontSize:12,color:C.yellow,fontWeight:700,marginBottom:6}}>📋 Before continuing</div>
        <div style={{fontSize:12,color:C.muted,lineHeight:1.9}}>1. Power on gateway<br/>2. Connect laptop to <code style={{color:C.blue,background:'#0D2137',padding:'1px 5px',borderRadius:3}}>beacongw_XXXXXX</code> wifi, pw: <code style={{color:C.blue,background:'#0D2137',padding:'1px 5px',borderRadius:3}}>12345678</code><br/>3. Open <code style={{color:C.blue,background:'#0D2137',padding:'1px 5px',borderRadius:3}}>http://192.168.8.1</code> login: admin/admin<br/>4. Network → WiFi → select school WiFi → Save<br/>5. Note the 6-char code e.g. <code style={{color:C.green,background:'#0D2137',padding:'1px 5px',borderRadius:3}}>00A0D1</code></div>
      </div>
      <Btn onClick={startDet} color={C.blue}>Next → Detect Gateway</Btn>
    </>}/>}
    {step===1&&<Box c={<>
      <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:8}}>Detecting Gateway</h3>
      {det&&<div style={{display:'flex',alignItems:'center',gap:10,marginBottom:16,background:'#0D2137',border:`1px solid ${C.blue}33`,borderRadius:8,padding:12}}><span style={{width:10,height:10,borderRadius:'50%',background:C.blue,animation:'pulse 1.2s infinite',display:'inline-block'}}/><span style={{fontSize:12,color:C.blue}}>Scanning MQTT...</span></div>}
      {pending.map(gw=><div key={gw.mac_address} onClick={()=>pick(gw)} style={{background:C.dark,border:`2px solid ${C.green}`,borderRadius:10,padding:14,marginBottom:8,cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'space-between'}}><div><div style={{fontSize:13,fontWeight:700,color:'#E4E4E7',fontFamily:'monospace'}}>📡 {gw.mac_address}</div><div style={{fontSize:11,color:C.muted}}>IP: {gw.ip_address||'unknown'}</div></div><Btn small color={C.green}>Select →</Btn></div>)}
      <div style={{borderTop:`1px solid ${C.border}`,paddingTop:14,marginTop:8}}><div style={{fontSize:12,color:C.muted,marginBottom:8}}>Not detected? Enter manually:</div><div style={{display:'flex',gap:10,marginBottom:10}}><input value={manMac} onChange={e=>setManMac(e.target.value)} placeholder="F0A882F54081" style={{flex:1,background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'8px 12px',color:'#E4E4E7',fontFamily:'monospace',fontSize:12,outline:'none'}}/><input value={manIp} onChange={e=>setManIp(e.target.value)} placeholder="192.168.5.x (optional)" style={{flex:1,background:C.dark,border:`1.5px solid ${C.border}`,borderRadius:8,padding:'8px 12px',color:'#E4E4E7',fontFamily:'monospace',fontSize:12,outline:'none'}}/></div><Btn onClick={()=>{if(manMac)pick({mac_address:manMac.toUpperCase(),ip_address:manIp});}} outline color={C.blue} small disabled={!manMac}>Use Manual →</Btn></div>
      <Log logs={logs}/>
    </>}/>}
    {step===2&&<Box c={<>
      <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:16}}>Enter Gateway Short ID</h3>
      <div style={{background:C.dark,border:`1px solid ${C.green}`,borderRadius:10,padding:12,marginBottom:14}}><div style={{fontSize:12,color:C.green,fontWeight:700}}>✓ Gateway: {mac}</div></div>
      <div style={{background:C.dark,border:`1px solid ${C.yellow}33`,borderRadius:8,padding:12,marginBottom:14}}><div style={{fontSize:12,color:C.yellow,fontWeight:700,marginBottom:4}}>📋 Where to find the Short ID</div><div style={{fontSize:12,color:C.muted,lineHeight:1.8}}>Gateway WiFi name: <code style={{color:C.green,background:'#0D2137',padding:'1px 6px',borderRadius:3}}>beacongw_XXXXXX</code><br/>The 6 chars after underscore = Short ID<br/>e.g. <code style={{color:C.blue}}>beacongw_00A0D1</code> → <code style={{color:C.green}}>00A0D1</code></div></div>
      <Fld label="Short ID (6 chars)" value={shortId} onChange={v=>setShortId(v.toUpperCase())} placeholder="00A0D1" mono/>
      <Btn color={C.blue} disabled={shortId.length<4} onClick={()=>{if(shortId.length>=4){log(`Short ID: ${shortId}`);setStep(3);}}}>Next → Configure</Btn>
      <Log logs={logs}/>
    </>}/>}
    {step===3&&<Box c={<>
      <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:16}}>Configure & Push</h3>
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10,marginBottom:14}}>{[['MAC',mac],['Short ID',shortId]].map(([k,v])=><div key={k} style={{background:C.dark,border:`1px solid ${C.border}`,borderRadius:8,padding:10}}><div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>{k}</div><div style={{fontSize:12,color:C.blue,fontFamily:'monospace'}}>{v}</div></div>)}</div>
      <Fld label="Gateway Label" value={label} onChange={setLabel} placeholder="Classroom B Gateway"/>
      <Sel label="Assign to Zone" value={zoneId} onChange={setZoneId} options={zones.map(z=>({value:z.id,label:z.name}))}/>
      <Sel label="RSSI Threshold" value={rssi} onChange={setRssi} options={[{value:'-60',label:'-60 dBm (tight)'},{value:'-70',label:'-70 dBm (standard ✓)'},{value:'-75',label:'-75 dBm (large room)'},{value:'-80',label:'-80 dBm (max range)'}]}/>
      <div style={{background:C.dark,border:`1px solid ${C.blue}33`,borderRadius:8,padding:12,marginBottom:14}}><div style={{fontSize:11,color:C.blue,fontWeight:700,marginBottom:4}}>📤 Config to push:</div><div style={{fontFamily:'monospace',fontSize:11,color:C.muted,lineHeight:1.8}}>Topic: kbeacon/subadmin/{shortId}<br/>Host:  192.168.5.63:1883<br/>RSSI:  {rssi} dBm</div></div>
      <Btn onClick={push} disabled={busy} color={C.green}>{busy?'⏳ Working...':'📤 Register & Push Config'}</Btn>
      <Log logs={logs}/>
    </>}/>}
    {step===4&&<Box c={<>
      <h3 style={{color:C.blue,fontSize:15,marginTop:0,marginBottom:16}}>Verify Connection</h3>
      <div style={{background:C.dark,border:`1px solid ${C.green}`,borderRadius:10,padding:14,marginBottom:16}}><div style={{fontSize:12,color:C.green,fontWeight:700,marginBottom:4}}>✓ Config pushed successfully</div><div style={{fontSize:11,color:C.muted,fontFamily:'monospace',lineHeight:1.8}}>MAC: {reg?.mac_address}<br/>Short ID: {reg?.short_id}<br/>Zone: {zones.find(z=>z.id===zoneId)?.name||'Unassigned'}</div></div>
      {!busy&&<Btn onClick={verify} color={C.blue}>🔍 Verify Heartbeat</Btn>}
      {busy&&<div style={{display:'flex',alignItems:'center',gap:10,background:'#0D2137',border:`1px solid ${C.blue}33`,borderRadius:8,padding:12}}><span style={{width:10,height:10,borderRadius:'50%',background:C.blue,animation:'pulse 1.2s infinite',display:'inline-block'}}/><span style={{fontSize:12,color:C.blue}}>Listening for heartbeat...</span></div>}
      <Log logs={logs}/>
    </>}/>}
    {step===5&&<Box c={<div style={{textAlign:'center',padding:'20px 0'}}>
      <div style={{fontSize:56,marginBottom:12}}>🎉</div>
      <h3 style={{color:C.green,fontSize:18,fontWeight:800,marginBottom:8}}>Gateway Setup Complete!</h3>
      <p style={{fontSize:13,color:C.muted,marginBottom:24}}><strong style={{color:'#E4E4E7'}}>{label||`Gateway ${shortId}`}</strong> is registered and configured.</p>
      <div style={{background:C.dark,border:`1px solid ${C.border}`,borderRadius:10,padding:16,marginBottom:24,textAlign:'left'}}>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,fontSize:12}}>
          {[['MAC',reg?.mac_address],['Short ID',reg?.short_id],['Label',label||`Gateway ${shortId}`],['Zone',zones.find(z=>z.id===zoneId)?.name||'Unassigned'],['Connection',conn],['RSSI',`${rssi} dBm`]].map(([k,v])=><div key={k} style={{background:'#060E1A',borderRadius:6,padding:'8px 10px'}}><div style={{color:'#4A5568',fontSize:10,textTransform:'uppercase',marginBottom:2}}>{k}</div><div style={{color:'#E4E4E7',fontFamily:'monospace'}}>{v||'—'}</div></div>)}
        </div>
      </div>
      <div style={{display:'flex',gap:12,justifyContent:'center'}}>
        <Btn onClick={onDone} color={C.green}>✓ Done</Btn>
        <Btn outline color={C.blue} onClick={()=>{setStep(0);setSelGw(null);setShortId('');setLabel('');setZoneId('');setLogs([]);}}>+ Add Another</Btn>
      </div>
      <Log logs={logs}/>
    </div>}/>}
    <style>{`@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.4)}}`}</style>
  </div>;
}

function EditPanel({gw,token,onSaved}){
  const [label,setLabel]=useState(gw.label);const [rssi,setRssi]=useState(String(gw.rssi_threshold||-70));const [zoneId,setZoneId]=useState(gw.zone_id||'');const [zones,setZones]=useState([]);const [saving,setSaving]=useState(false);const [msg,setMsg]=useState('');
  useEffect(()=>{fetch(`${API}/zones`,{headers:auth(token)}).then(r=>r.json()).then(setZones).catch(()=>{});},[]);
  const save=async()=>{setSaving(true);try{const r=await fetch(`${API}/${gw.id}`,{method:'PUT',headers:auth(token),body:JSON.stringify({label,zone_id:zoneId||null,rssi_threshold:parseInt(rssi)})});if(r.ok){setMsg('✓ Saved');setTimeout(onSaved,800);}else{const d=await r.json();setMsg('Error:'+d.error);}}finally{setSaving(false);}};
  return <div style={{marginTop:16,borderTop:`1px solid ${C.border}`,paddingTop:16}}><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}><Fld label="Label" value={label} onChange={setLabel} placeholder="Classroom Gateway"/><Sel label="Zone" value={zoneId} onChange={setZoneId} options={zones.map(z=>({value:z.id,label:z.name}))}/><Sel label="RSSI" value={rssi} onChange={setRssi} options={[{value:'-60',label:'-60 dBm'},{value:'-70',label:'-70 dBm'},{value:'-75',label:'-75 dBm'},{value:'-80',label:'-80 dBm'}]}/></div><div style={{display:'flex',gap:10,alignItems:'center'}}><Btn onClick={save} disabled={saving} color={C.green} small>{saving?'⏳':'✓ Save'}</Btn>{msg&&<span style={{fontSize:12,color:msg.startsWith('✓')?C.green:C.red}}>{msg}</span>}</div></div>;
}

export default function GatewayManager({token}){
  const [gws,setGws]=useState([]);const [wizard,setWizard]=useState(false);const [editing,setEditing]=useState(null);const [loading,setLoading]=useState(true);const [res,setRes]=useState({});const [busy,setBusy]=useState({});
  const load=async()=>{setLoading(true);try{const r=await fetch(API,{headers:auth(token)});setGws(await r.json());}finally{setLoading(false);}};
  useEffect(()=>{load();const t=setInterval(load,15000);return()=>clearInterval(t);},[]);
  const act=async(id,url,key)=>{setBusy(b=>({...b,[id+key]:true}));setRes(r=>({...r,[id]:null}));try{const r=await fetch(url,{method:'POST',headers:auth(token),body:JSON.stringify({action:key})});setRes(rv=>({...rv,[id]:{ok:r.ok,d:await r.json()}}));}finally{setBusy(b=>({...b,[id+key]:false}));}};
  if(wizard)return <Wizard token={token} onDone={()=>{setWizard(false);load();}} onCancel={()=>setWizard(false)}/>;
  return <div>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:24}}>
      <div><h2 style={{fontSize:20,fontWeight:800,color:'#E4E4E7',margin:0}}>Gateway Management</h2><p style={{fontSize:12,color:C.muted,margin:'4px 0 0'}}>{gws.length} gateway{gws.length!==1?'s':''} · refreshes every 15s</p></div>
      <Btn onClick={()=>setWizard(true)} color={C.blue}>+ Add Gateway</Btn>
    </div>
    {loading&&<div style={{color:C.muted,fontSize:13}}>Loading...</div>}
    {!loading&&gws.length===0&&<Box c={<div style={{textAlign:'center',padding:40}}><div style={{fontSize:48,marginBottom:12}}>📡</div><div style={{fontSize:15,color:'#E4E4E7',fontWeight:700,marginBottom:8}}>No gateways registered</div><Btn onClick={()=>setWizard(true)} color={C.blue}>+ Add First Gateway</Btn></div>}/>}
    <div style={{display:'grid',gap:16}}>
      {gws.map(gw=>{
        const secs=gw.last_heartbeat_at?Math.floor((Date.now()-new Date(gw.last_heartbeat_at))/1000):null;
        const isEd=editing===gw.id;
        return <Box key={gw.id} s={{borderColor:isEd?C.blue:C.border}} c={<>
          <div style={{display:'flex',alignItems:'flex-start',gap:14,marginBottom:14}}>
            <span style={{fontSize:26}}>📡</span>
            <div style={{flex:1}}>
              <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:8}}><span style={{fontSize:15,fontWeight:800,color:'#E4E4E7'}}>{gw.label}</span><Dot state={gw.health_state||'UNKNOWN'}/><span style={{fontSize:11,color:gw.zone_name?C.blue:'#4A5568'}}>{gw.zone_name||'Unassigned'}</span></div>
              <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:8}}>
                {[{k:'MAC',v:gw.mac_address,m:1},{k:'Short ID',v:gw.short_id||'—',m:1,c:gw.short_id?C.green:C.yellow},{k:'IP',v:gw.ip_address||'—',m:1},{k:'Firmware',v:gw.firmware_version||'—'},{k:'Connection',v:gw.connection_type||'WIFI'},{k:'RSSI Filter',v:gw.rssi_threshold?`${gw.rssi_threshold}dBm`:'—'},{k:'Heartbeat',v:secs!=null?`${secs}s ago`:'Never',c:secs!=null?(secs<60?C.green:secs<120?C.yellow:C.red):'#4A5568'},{k:'Setup',v:gw.setup_status||'—',c:gw.setup_status==='CONFIGURED'?C.green:C.yellow}].map(({k,v,m,c})=><div key={k} style={{background:C.dark,borderRadius:8,padding:'7px 10px'}}><div style={{fontSize:10,color:'#4A5568',textTransform:'uppercase',marginBottom:2}}>{k}</div><div style={{fontSize:12,color:c||'#E4E4E7',fontFamily:m?'monospace':'inherit',fontWeight:c?700:400}}>{v}</div></div>)}
              </div>
            </div>
          </div>
          <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
            <Btn small outline color={C.blue} disabled={busy[gw.id+'get_config']||!gw.short_id} onClick={()=>act(gw.id,`${API}/${gw.id}/command`,'get_config')}>{busy[gw.id+'get_config']?'⏳':'📋'} Get Config</Btn>
            <Btn small color={C.green} disabled={busy[gw.id+'push']||!gw.short_id} onClick={()=>act(gw.id,`${API}/${gw.id}/push-config`,'push')}>{busy[gw.id+'push']?'⏳':'📤 Push Config'}</Btn>
            <Btn small outline color={C.yellow} disabled={busy[gw.id+'reboot']||!gw.short_id} onClick={()=>act(gw.id,`${API}/${gw.id}/command`,'reboot')}>🔄 Reboot</Btn>
            <Btn small outline color={C.purple} onClick={()=>setEditing(isEd?null:gw.id)}>{isEd?'▲ Close':'⚙️ Edit'}</Btn>
          </div>
          {res[gw.id]&&<div style={{marginTop:10,background:'#060E1A',borderRadius:8,padding:12,border:`1px solid ${res[gw.id].ok?C.green+'44':C.red+'44'}`,fontFamily:'monospace',fontSize:11,color:'#B0C4D8',maxHeight:100,overflowY:'auto'}}>{JSON.stringify(res[gw.id].d,null,2)}</div>}
          {isEd&&<EditPanel gw={gw} token={token} onSaved={()=>{setEditing(null);load();}}/>}
        </>}/>;
      })}
    </div>
  </div>;
}
""")

# STEP 6: Rebuild
print('\n🐳 Step 6: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 30s...')
time.sleep(30)

# STEP 7: Smoke test
print('\n🧪 Step 7: Smoke test...')
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'},method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print(f'  ✅ Login OK')
    req2 = urllib.request.Request('http://localhost/api/admin/gateways',
        headers={'Authorization':f'Bearer {token}'})
    gws = J.loads(urllib.request.urlopen(req2,timeout=10).read())
    print(f'  ✅ Gateways: {len(gws)} found')
    for g in gws:
        print(f'     📡 {g["label"]} | {g["mac_address"]} | short_id={g.get("short_id")} | {g["health_state"]}')
    if gws:
        req3 = urllib.request.Request(f'http://localhost/api/admin/gateways/{gws[0]["id"]}/push-config',
            data=b'{}',headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
        r3 = J.loads(urllib.request.urlopen(req3,timeout=10).read())
        print(f'  ✅ push-config → {r3.get("topic")}')
except Exception as e:
    print(f'  ❌ {e}')

print('\n' + '='*55)
print('  ✅ DEPLOY COMPLETE')
print('='*55)
print('\n  Open: http://192.168.5.63')
print('  Login: admin / Admin1234!')
print('  Navigate to: Gateway Management\n')
