
'use strict';
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// ── GET /api/events  — paginated event stream ────────────────────────────────
router.get('/', async (req, res) => {
  try {
    const {
      category, severity, event_type,
      from, to,
      student_id,
      unacked_only,
      limit = 50, offset = 0
    } = req.query;

    const role = req.user.role;
    const conditions = [`$1 = ANY(visible_to)`];
    const params = [role];
    let p = 2;

    if (category)    { conditions.push(`category=$${p++}`);    params.push(category); }
    if (severity)    { conditions.push(`severity=$${p++}`);    params.push(severity); }
    if (event_type)  { conditions.push(`event_type=$${p++}`);  params.push(event_type); }
    if (from)        { conditions.push(`created_at>=$${p++}`); params.push(from); }
    if (to)          { conditions.push(`created_at<=$${p++}`); params.push(to); }
    if (student_id)  { conditions.push(`$${p++}=ANY(student_ids)`); params.push(student_id); }
    if (unacked_only==='true') { conditions.push(`requires_ack=true AND acked_at IS NULL`); }

    // Auto-clear expired INFO events
    await db.query(`
      UPDATE director_events
      SET acked_at=NOW(), acked_by=NULL
      WHERE requires_ack=false
        AND auto_clear_at IS NOT NULL
        AND auto_clear_at < NOW()
        AND acked_at IS NULL`);

    const where = conditions.join(' AND ');

    const [evts, cnt] = await Promise.all([
      db.query(`
        SELECT
          de.*,
          a.username  as actor_username,
          a.full_name as actor_name,
          a.role      as actor_role,
          ab.username as acked_by_username,
          ab.full_name as acked_by_name,
          z.name      as zone_name,
          -- hydrate student names
          COALESCE((
            SELECT JSON_AGG(JSON_BUILD_OBJECT(
              'id',s.id,'first_name',s.first_name,'last_name',s.last_name
            ))
            FROM students s WHERE s.id = ANY(de.student_ids)
          ), '[]') as students
        FROM director_events de
        LEFT JOIN users a  ON a.id  = de.actor_id
        LEFT JOIN users ab ON ab.id = de.acked_by
        LEFT JOIN zones z  ON z.id  = de.zone_id
        WHERE ${where}
        ORDER BY de.created_at DESC
        LIMIT $${p} OFFSET $${p+1}`,
        [...params, parseInt(limit), parseInt(offset)]),
      db.query(`SELECT COUNT(*) FROM director_events WHERE ${where}`, params),
    ]);

    res.json({
      total:  parseInt(cnt.rows[0].count),
      limit:  parseInt(limit),
      offset: parseInt(offset),
      events: evts.rows,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/events/summary — counts by category + unacked critical
router.get('/summary', async (req, res) => {
  try {
    const role = req.user.role;
    const [cats, unacked, recent] = await Promise.all([
      db.query(`
        SELECT category, severity, COUNT(*) as count
        FROM director_events
        WHERE $1=ANY(visible_to)
          AND created_at >= NOW() - INTERVAL '1 day'
          AND (acked_at IS NULL OR requires_ack=false)
        GROUP BY category, severity ORDER BY category, severity`,
        [role]),
      db.query(`
        SELECT COUNT(*) FROM director_events
        WHERE $1=ANY(visible_to)
          AND requires_ack=true AND acked_at IS NULL`, [role]),
      db.query(`
        SELECT COUNT(*) FROM director_events
        WHERE $1=ANY(visible_to)
          AND created_at >= NOW() - INTERVAL '1 hour'`, [role]),
    ]);
    res.json({
      unacked_critical: parseInt(unacked.rows[0].count),
      last_hour:        parseInt(recent.rows[0].count),
      by_category:      cats.rows,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/events/:id/acknowledge
router.post('/:id/acknowledge', async (req, res) => {
  try {
    const r = await db.query(`
      UPDATE director_events
      SET acked_by=$2, acked_at=NOW()
      WHERE id=$1 AND requires_ack=true AND acked_at IS NULL
      RETURNING *`,
      [req.params.id, req.user.id]);
    if (!r.rows[0])
      return res.status(404).json({ error: 'Event not found or already acknowledged' });
    res.json({ success: true, event: r.rows[0] });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/events/settings — update visibility per event type
router.put('/settings', async (req, res) => {
  try {
    if (req.user.role !== 'IT')
      return res.status(403).json({ error: 'IT Admin only' });
    for (const [eventType, roles] of Object.entries(req.body)) {
      const key = `event_visible_${eventType}`;
      const val = Array.isArray(roles) ? roles.join(',') : String(roles);
      await db.query(`
        INSERT INTO school_settings (key,value,updated_at) VALUES ($1,$2,NOW())
        ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()`,
        [key, val]);
    }
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/events/settings — get all visibility settings
router.get('/settings', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT key, value FROM school_settings
      WHERE key LIKE 'event_visible_%' ORDER BY key`);
    const settings = {};
    r.rows.forEach(row => {
      const type = row.key.replace('event_visible_','');
      settings[type] = row.value.split(',').map(s=>s.trim());
    });
    res.json(settings);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
