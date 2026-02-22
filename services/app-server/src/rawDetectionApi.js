
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
