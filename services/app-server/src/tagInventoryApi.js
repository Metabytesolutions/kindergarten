
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
      LEFT JOIN users u ON (bt.assigned_to='TEACHER' AND u.full_name=bt.label)
      LEFT JOIN (
        SELECT DISTINCT ON (tag_mac) tag_mac, gateway_id
        FROM detections ORDER BY tag_mac, detected_at DESC
      ) ld ON ld.tag_mac=bt.mac_address
      LEFT JOIN ble_gateways bg ON bg.id=ld.gateway_id
      LEFT JOIN zones z ON z.id=bg.zone_id
      WHERE bt.mac_address LIKE 'BC5729%'
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
