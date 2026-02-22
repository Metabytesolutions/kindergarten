const { logEvent } = require('./eventLogger');
const mqtt = require('mqtt');

let mqttClient = null;
const db   = require('./db');
let ws;

const BROKER_URL      = process.env.MQTT_BROKER_URL || 'mqtt://emqx:1883';
const TOPIC_PUBLISH   = 'kbeacon/publish/#';
const TOPIC_PUBACTION = 'kbeacon/pubaction/#';

function start(wsModule) {
  ws = wsModule;

  mqttClient = mqtt.connect(BROKER_URL, {
    clientId: `prosper_ingestion_${Date.now()}`,
    clean: true,
    reconnectPeriod: 5000,
  });

  mqttClient.on('connect', () => {
    console.log('✅ MQTT ingestion worker connected');
    mqttClient.subscribe([TOPIC_PUBLISH, TOPIC_PUBACTION], { qos: 0 });
  });

  mqttClient.on('message', async (topic, message) => {
    try {
      const payload = JSON.parse(message.toString());
      if (topic.startsWith('kbeacon/publish/'))   await handleDetection(topic, payload);
      if (topic.startsWith('kbeacon/pubaction/')) await handleHeartbeat(topic, payload);
    } catch (err) {
      console.error('MQTT message error:', err.message);
    }
  });

  mqttClient.on('error',     (err) => console.error('MQTT error:', err.message));
  mqttClient.on('reconnect', ()    => console.log('🔄 MQTT reconnecting...'));
}

async function handleDetection(topic, payload) {
  if (payload.msg !== 'advData' || !payload.obj?.length) return;

  const gatewayMac = payload.gmac || topic.split('/').pop();
  const gateway    = await ensureGateway(gatewayMac);
  if (!gateway) return;

  for (const det of payload.obj) {
    if (!det.dmac) continue;
    await ensureTag(det.dmac);

    await db.query(`
      INSERT INTO detections
        (gateway_id, tag_mac, rssi, battery_mv, adv_count, raw_payload, detected_at)
      VALUES ($1,$2,$3,$4,$5,$6, to_timestamp($7/1000.0))
    `, [
      gateway.id,
      det.dmac.toUpperCase(),
      det.rssi   || null,
      det.vbatt  || null,
      det.advCnt || null,
      JSON.stringify(det),
      det.time ? new Date(det.time).getTime() : Date.now(),
    ]);

    await db.query(`
      UPDATE ble_tags
      SET last_seen_at = NOW(),
          last_rssi    = $2,
          battery_mv   = COALESCE($3, battery_mv),
          updated_at   = NOW()
      WHERE mac_address = $1
    `, [det.dmac.toUpperCase(), det.rssi || null, det.vbatt || null]);
  }

  // Broadcast live detections to all WebSocket clients
  const presence = await db.query(`
    SELECT
      s.first_name, s.last_name,
      t.mac_address, t.last_rssi, t.battery_mv, t.last_seen_at,
      CASE
        WHEN t.last_seen_at > NOW() - INTERVAL '10 seconds' THEN 'PRESENT'
        WHEN t.last_seen_at > NOW() - INTERVAL '30 seconds' THEN 'PROBABLE'
        ELSE 'MISSING'
      END as presence_state,
      EXTRACT(EPOCH FROM (NOW() - t.last_seen_at))::int as seconds_ago
    FROM ble_tags t
    JOIN students s ON s.id = t.student_id
    WHERE t.is_active = true
    ORDER BY s.first_name
  `);

  ws.broadcast('PRESENCE_UPDATE', presence.rows);
  console.log(`📥 ${payload.obj.length} detections from ${gatewayMac}`);
}

async function handleHeartbeat(topic, payload) {
  if (payload.msg !== 'alive') return;
  const gatewayMac = payload.gmac || topic.split('/').pop();

  await db.query(`
    UPDATE ble_gateways
    SET health_state      = 'HEALTHY',
        last_heartbeat_at = NOW(),
        firmware_version  = $2,
        ip_address        = $3,
        updated_at        = NOW()
    WHERE mac_address = $1
  `, [gatewayMac.toUpperCase(), payload.ver || null, payload.wanIP || null]);

  ws.broadcast('GATEWAY_HEARTBEAT', {
    mac: gatewayMac, firmware: payload.ver,
    ip: payload.wanIP, state: 'HEALTHY'
  });

  console.log(`💓 Heartbeat: ${gatewayMac}`);
}

async function ensureGateway(mac) {
  const macUpper = mac.toUpperCase();
  const existing = await db.query('SELECT id FROM ble_gateways WHERE mac_address = $1', [macUpper]);
  if (existing.rows.length > 0) return existing.rows[0];

  const inserted = await db.query(`
    INSERT INTO ble_gateways
      (mac_address, label, health_state, discovered_at, mqtt_topic, is_active)
    VALUES ($1,$2,'HEALTHY',NOW(),$3,true)
    ON CONFLICT (mac_address) DO UPDATE SET health_state='HEALTHY', updated_at=NOW()
    RETURNING id
  `, [macUpper, `Gateway_${mac.slice(-6)}`, `kbeacon/publish/${mac}`]);

  return inserted.rows[0];
}

async function ensureTag(mac) {
  await db.query(`
    INSERT INTO ble_tags (mac_address, tag_type, assigned_to, is_active, registered_at)
    VALUES ($1,'STUDENT','NONE',false,NOW())
    ON CONFLICT (mac_address) DO NOTHING
  `, [mac.toUpperCase()]);
}



// ── PUBLISH TO GATEWAY ────────────────────────────────────────────────────────
function publishToGateway(topic, payload) {
  if (!mqttClient || !mqttClient.connected) {
    console.error('❌ MQTT client not connected — cannot publish');
    return false;
  }
  const msg = typeof payload === 'string' ? payload : JSON.stringify(payload);
  mqttClient.publish(topic, msg, { qos: 1 }, (err) => {
    if (err) console.error(`❌ Publish error to ${topic}:`, err.message);
    else     console.log(`📤 Published to ${topic}:`, msg.slice(0, 120));
  });
  return true;
}

module.exports = { start, publishToGateway };
