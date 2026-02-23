'use strict';
const db = require('./db');
const { logEvent } = require('./eventLogger');

let monitorStarted = false;

async function runHealthCheck() {
  const checks = [];
  const now    = new Date();

  try {
    // Check 1: PostgreSQL responsive
    try {
      const t0 = Date.now();
      await db.query('SELECT 1');
      const ms = Date.now() - t0;
      checks.push({ service: 'postgresql', status: ms < 500 ? 'OK' : 'WARN',
                    detail: { response_ms: ms } });
    } catch(e) {
      checks.push({ service: 'postgresql', status: 'CRITICAL',
                    detail: { error: e.message } });
    }

    // Check 2: MQTT worker active (gateways sending data)
    try {
      const r = await db.query(`
        SELECT COUNT(*)::int as c,
               MAX(created_at) as last_detection
        FROM ble_detections
        WHERE created_at > NOW() - INTERVAL '5 minutes'
      `);
      const lastSeen = r.rows[0]?.last_detection;
      const minutesAgo = lastSeen
        ? Math.floor((now - new Date(lastSeen)) / 60000) : 999;
      checks.push({
        service: 'mqtt_worker',
        status:  minutesAgo < 2 ? 'OK' : minutesAgo < 5 ? 'WARN' : 'CRITICAL',
        detail:  { detections_5min: r.rows[0].c, last_detection_mins_ago: minutesAgo }
      });
    } catch(e) {
      checks.push({ service: 'mqtt_worker', status: 'WARN',
                    detail: { error: e.message } });
    }

    // Check 3: Gateway health
    try {
      const gateways = await db.query(`
        SELECT short_id, last_seen_at, setup_status,
          EXTRACT(EPOCH FROM (NOW()-last_seen_at))/60 as mins_ago
        FROM ble_gateways WHERE is_active=true
      `);
      for (const gw of gateways.rows) {
        const mins = Math.floor(gw.mins_ago || 999);
        checks.push({
          service: `gateway_${gw.short_id}`,
          status:  mins < 2 ? 'OK' : mins < 5 ? 'WARN' : 'CRITICAL',
          detail:  { last_seen_mins_ago: mins, setup_status: gw.setup_status }
        });
      }
    } catch(e) {
      checks.push({ service: 'gateways', status: 'WARN',
                    detail: { error: e.message } });
    }

    // Check 4: DB table sizes (data health)
    try {
      const r = await db.query(`
        SELECT
          (SELECT COUNT(*) FROM ble_detections
           WHERE created_at > NOW()-INTERVAL '1 hour')::int as detections_1h,
          (SELECT COUNT(*) FROM director_events
           WHERE created_at::date=CURRENT_DATE)::int as events_today,
          (SELECT COUNT(*) FROM student_sessions
           WHERE batch_date=CURRENT_DATE)::int as sessions_today
      `);
      checks.push({ service: 'data_pipeline', status: 'OK',
                    detail: r.rows[0] });
    } catch(e) {
      checks.push({ service: 'data_pipeline', status: 'WARN',
                    detail: { error: e.message } });
    }

    // Store results
    let hasWarn = false, hasCrit = false;
    for (const c of checks) {
      await db.query(`
        INSERT INTO system_health_log (service, status, detail)
        VALUES ($1,$2,$3)
      `, [c.service, c.status, JSON.stringify(c.detail)]);

      if (c.status === 'WARN')     hasWarn = true;
      if (c.status === 'CRITICAL') hasCrit = true;
    }

    // Alert if issues found (throttle — once per 15min per service)
    const critChecks = checks.filter(c => c.status === 'CRITICAL');
    const warnChecks = checks.filter(c => c.status === 'WARN');

    if (critChecks.length > 0) {
      const alreadyAlerted = await db.query(`
        SELECT 1 FROM system_health_log
        WHERE status='CRITICAL' AND alerted=true
          AND check_time > NOW() - INTERVAL '15 minutes'
        LIMIT 1
      `);
      if (!alreadyAlerted.rows[0]) {
        await logEvent('SYSTEM_HEALTH_CRIT', {
          title: `🚨 System issue: ${critChecks.map(c=>c.service).join(', ')}`,
          detail: { checks: critChecks },
        }).catch(() => {});
        await db.query(
          "UPDATE system_health_log SET alerted=true WHERE status='CRITICAL' AND alerted=false AND check_time > NOW()-INTERVAL '5 minutes'");
        console.error('🚨 CRITICAL health check:', critChecks.map(c=>c.service).join(', '));
      }
    } else if (warnChecks.length > 0) {
      const alreadyAlerted = await db.query(`
        SELECT 1 FROM system_health_log
        WHERE status='WARN' AND alerted=true
          AND check_time > NOW()-INTERVAL '30 minutes'
        LIMIT 1
      `);
      if (!alreadyAlerted.rows[0]) {
        await logEvent('SYSTEM_HEALTH_WARN', {
          title: `⚠️ System warning: ${warnChecks.map(c=>c.service).join(', ')}`,
          detail: { checks: warnChecks },
        }).catch(() => {});
        await db.query(
          "UPDATE system_health_log SET alerted=true WHERE status='WARN' AND alerted=false AND check_time > NOW()-INTERVAL '5 minutes'");
      }
    }

    return checks;
  } catch(e) {
    console.error('[SystemMonitor] Error:', e.message);
    return [];
  }
}

async function getHealthSummary() {
  try {
    const r = await db.query(`
      SELECT DISTINCT ON (service)
        service, status, detail, check_time
      FROM system_health_log
      ORDER BY service, check_time DESC
    `);
    return r.rows;
  } catch(e) { return []; }
}

function startSystemMonitor() {
  if (monitorStarted) return;
  monitorStarted = true;

  // Initial check after 30s startup
  setTimeout(runHealthCheck, 30000);

  // Then every 5 minutes
  setInterval(runHealthCheck, 5 * 60 * 1000);

  // Clean old logs weekly (keep 7 days)
  setInterval(async () => {
    try {
      await db.query(
        "DELETE FROM system_health_log WHERE check_time < NOW()-INTERVAL '7 days'");
    } catch(e) {}
  }, 24 * 60 * 60 * 1000);

  console.log('🔍 System monitor started (5min interval)');
}

module.exports = { startSystemMonitor, runHealthCheck, getHealthSummary };
