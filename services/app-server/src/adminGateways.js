const express = require('express');
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
