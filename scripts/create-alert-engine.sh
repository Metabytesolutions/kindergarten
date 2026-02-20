#!/bin/bash
set -e
echo "🔧 Creating alert engine files..."

cat > ~/prosper-platform/services/app-server/src/alertEngine.js << 'EOF'
const db = require('./db');
let ws;
const CRITICAL_THRESHOLD_SEC = 60;
const CHECK_INTERVAL_MS = 5000;
let activeAlerts = new Map();

function start(wsModule) {
  ws = wsModule;
  setInterval(checkPresence, CHECK_INTERVAL_MS);
  console.log('✅ Alert engine started');
}

async function checkPresence() {
  try {
    const result = await db.query(`
      SELECT t.id as tag_id, t.mac_address, t.last_seen_at, t.last_rssi,
             s.id as student_id, s.first_name, s.last_name,
             EXTRACT(EPOCH FROM (NOW() - t.last_seen_at))::int as seconds_ago
      FROM ble_tags t
      JOIN students s ON s.id = t.student_id
      WHERE t.is_active = true
    `);
    for (const tag of result.rows) {
      const key = `missing_${tag.student_id}`;
      if (tag.seconds_ago > CRITICAL_THRESHOLD_SEC) {
        if (!activeAlerts.has(key)) {
          await fireAlert({
            type: 'TAG_MISSING', severity: 'CRITICAL',
            title: `Student Missing: ${tag.first_name} ${tag.last_name}`,
            description: `No detection for ${tag.seconds_ago} seconds`,
            student_id: tag.student_id, tag_id: tag.tag_id,
            evidence: { seconds_ago: tag.seconds_ago, last_rssi: tag.last_rssi }
          });
          activeAlerts.set(key, true);
        }
      } else {
        if (activeAlerts.has(key)) {
          await resolveAlert('TAG_MISSING', tag.student_id, null);
          activeAlerts.delete(key);
          ws.broadcast('ALERT_RESOLVED', {
            type: 'TAG_MISSING',
            message: `${tag.first_name} ${tag.last_name} is back in range`
          });
          console.log(`✅ Resolved: ${tag.first_name} ${tag.last_name} back`);
        }
      }
    }
    await checkGatewayHealth();
  } catch (err) {
    console.error('Alert engine error:', err.message);
  }
}

async function checkGatewayHealth() {
  const result = await db.query(`
    SELECT id, label,
           EXTRACT(EPOCH FROM (NOW() - last_heartbeat_at))::int as seconds_since_heartbeat
    FROM ble_gateways WHERE is_active = true
  `);
  for (const gw of result.rows) {
    const key = `gateway_${gw.id}`;
    if (gw.seconds_since_heartbeat > 60) {
      await db.query(`UPDATE ble_gateways SET health_state='OFFLINE', updated_at=NOW() WHERE id=$1`, [gw.id]);
      if (!activeAlerts.has(key)) {
        await fireAlert({
          type: 'GATEWAY_OFFLINE', severity: 'CRITICAL',
          title: `Gateway Offline: ${gw.label}`,
          description: `No heartbeat for ${gw.seconds_since_heartbeat} seconds`,
          gateway_id: gw.id,
          evidence: { seconds: gw.seconds_since_heartbeat }
        });
        activeAlerts.set(key, true);
      }
    } else {
      if (activeAlerts.has(key)) {
        await resolveAlert('GATEWAY_OFFLINE', null, gw.id);
        activeAlerts.delete(key);
        ws.broadcast('ALERT_RESOLVED', { type: 'GATEWAY_OFFLINE', message: `Gateway ${gw.label} back online` });
      }
    }
  }
}

async function fireAlert({ type, severity, title, description, student_id, tag_id, gateway_id, evidence }) {
  const existing = await db.query(`
    SELECT id FROM alerts WHERE alert_type=$1 AND status='OPEN'
    AND (student_id=$2 OR $2 IS NULL) AND (gateway_id=$3 OR $3 IS NULL)
  `, [type, student_id||null, gateway_id||null]);
  if (existing.rows.length > 0) return;
  const result = await db.query(`
    INSERT INTO alerts (alert_type, severity, status, title, description, evidence, student_id, tag_id, gateway_id)
    VALUES ($1,$2,'OPEN',$3,$4,$5,$6,$7,$8) RETURNING id
