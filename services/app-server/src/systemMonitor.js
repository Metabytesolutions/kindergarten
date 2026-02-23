'use strict';
const db = require('./db');
const { logEvent } = require('./eventLogger');
let started = false;

async function runHealthCheck() {
  const checks = [];
  try {
    // DB check
    try {
      const t0=Date.now(); await db.query('SELECT 1'); const ms=Date.now()-t0;
      checks.push({service:'postgresql',status:ms<500?'OK':'WARN',detail:{response_ms:ms}});
    } catch(e) {
      checks.push({service:'postgresql',status:'CRITICAL',detail:{error:e.message}});
    }
    // MQTT/Gateway check
    try {
      const r=await db.query(`SELECT COUNT(*)::int as c, MAX(created_at) as last
        FROM ble_detections WHERE created_at>NOW()-INTERVAL '5 minutes'`);
      const mins=r.rows[0].last
        ?Math.floor((Date.now()-new Date(r.rows[0].last))/60000):999;
      checks.push({service:'mqtt_gateways',
        status:mins<2?'OK':mins<5?'WARN':'CRITICAL',
        detail:{detections_5min:r.rows[0].c,last_detection_mins_ago:mins}});
    } catch(e) {
      checks.push({service:'mqtt_gateways',status:'WARN',detail:{error:e.message}});
    }
    // Store
    for(const c of checks){
      await db.query(
        'INSERT INTO system_health_log(service,status,detail) VALUES($1,$2,$3)',
        [c.service,c.status,JSON.stringify(c.detail)]).catch(()=>{});
    }
    // Alert on critical
    const crits=checks.filter(c=>c.status==='CRITICAL');
    if(crits.length>0){
      await logEvent('SYSTEM_HEALTH_CRIT',{
        title:`🚨 System issue: ${crits.map(c=>c.service).join(', ')}`,
        detail:{checks:crits}}).catch(()=>{});
    }
    return checks;
  } catch(e) { console.error('[Monitor]',e.message); return []; }
}

async function getHealthSummary() {
  try {
    const r=await db.query(`SELECT DISTINCT ON(service) service,status,detail,check_time
      FROM system_health_log ORDER BY service,check_time DESC`);
    return r.rows;
  } catch(e) { return []; }
}

function startSystemMonitor() {
  if(started) return; started=true;
  setTimeout(runHealthCheck,30000);
  setInterval(runHealthCheck,5*60*1000);
  // Clean old logs daily
  setInterval(async()=>{
    await db.query("DELETE FROM system_health_log WHERE check_time<NOW()-INTERVAL '7 days'")
      .catch(()=>{});
  },24*60*60*1000);
  console.log('🔍 System monitor started');
}

module.exports={startSystemMonitor,runHealthCheck,getHealthSummary};
