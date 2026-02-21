const express = require('express');
const db      = require('./db');
const router  = express.Router();

router.post('/start', async (req, res) => {
  try {
    const zone = await db.query('SELECT id FROM zones LIMIT 1');
    const classroom_id = req.body.classroom_id || zone.rows[0]?.id;
    if (!classroom_id) return res.status(400).json({ error: 'No classroom found' });
    const result = await db.query(`
      INSERT INTO batches (classroom_id, teacher_id, state, started_at)
      VALUES ($1, $2, 'ACTIVE', NOW()) RETURNING *
    `, [classroom_id, req.user.id]);
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/active', async (req, res) => {
  try {
    const result = await db.query(`
      SELECT b.*, u.username as teacher_name, z.name as zone_name
      FROM batches b
      LEFT JOIN users u ON u.id = b.teacher_id
      LEFT JOIN zones z ON z.id = b.classroom_id
      WHERE b.state = 'ACTIVE'
      ORDER BY b.started_at DESC LIMIT 1
    `);
    res.json(result.rows[0] || null);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/', async (req, res) => {
  try {
    const result = await db.query(`
      SELECT b.*, u.username as teacher_name, z.name as zone_name
      FROM batches b
      LEFT JOIN users u ON u.id = b.teacher_id
      LEFT JOIN zones z ON z.id = b.classroom_id
      ORDER BY b.created_at DESC LIMIT 20
    `);
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/:id/close', async (req, res) => {
  try {
    const result = await db.query(`
      UPDATE batches SET state='CLOSED', closed_at=NOW(), updated_at=NOW()
      WHERE id=$1 AND state='ACTIVE' RETURNING *
    `, [req.params.id]);
    if (result.rows.length === 0) return res.status(404).json({ error: 'Not found' });
    res.json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/activate', async (req, res) => {
  try {
    const { mac_address, student_id, batch_id } = req.body;
    if (!mac_address || !student_id) return res.status(400).json({ error: 'mac_address and student_id required' });
    const tagResult = await db.query('SELECT id FROM ble_tags WHERE mac_address=$1', [mac_address.toUpperCase()]);
    if (tagResult.rows.length === 0) return res.status(404).json({ error: 'Tag not found' });
    const studentResult = await db.query('SELECT id, first_name, last_name FROM students WHERE id=$1', [student_id]);
    if (studentResult.rows.length === 0) return res.status(404).json({ error: 'Student not found' });
    await db.query(`
      UPDATE ble_tags SET student_id=$2, assigned_to='STUDENT', is_active=true,
      activated_at=NOW(), updated_at=NOW()
      WHERE mac_address=$1
    `, [mac_address.toUpperCase(), student_id]);
    await db.query(`
      INSERT INTO audit_log (actor_id, actor_role, action, entity_type, entity_id)
      VALUES ($1,$2,'TAG_ACTIVATED','ble_tag',$3)
    `, [req.user.id, req.user.role, tagResult.rows[0].id]);
    console.log(`✅ Tag ${mac_address} activated for ${studentResult.rows[0].first_name}`);
    res.json({ success: true, mac_address: mac_address.toUpperCase(), student: studentResult.rows[0] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/deactivate', async (req, res) => {
  try {
    const { mac_address } = req.body;
    await db.query(`
      UPDATE ble_tags SET is_active=false, student_id=NULL,
      assigned_to='NONE', activated_at=NULL, updated_at=NOW()
      WHERE mac_address=$1
    `, [mac_address.toUpperCase()]);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/unactivated/tags', async (req, res) => {
  try {
    const result = await db.query(`
      SELECT t.mac_address, t.last_seen_at, t.last_rssi,
             EXTRACT(EPOCH FROM (NOW() - t.last_seen_at))::int as seconds_ago
      FROM ble_tags t
      WHERE t.is_active = false
        AND t.last_seen_at > NOW() - INTERVAL '30 seconds'
      ORDER BY t.last_rssi DESC
    `);
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/unassigned/students', async (req, res) => {
  try {
    const result = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.dob
      FROM students s
      LEFT JOIN ble_tags t ON t.student_id = s.id AND t.is_active = true
      WHERE t.id IS NULL AND s.is_active = true
      ORDER BY s.first_name
    `);
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/:id/report', async (req, res) => {
  try {
    const result = await db.query(`
      SELECT s.first_name, s.last_name, t.mac_address, t.activated_at,
             t.last_seen_at, t.last_rssi,
             CASE
               WHEN t.last_seen_at > NOW() - INTERVAL '10 seconds' THEN 'PRESENT'
               WHEN t.last_seen_at > NOW() - INTERVAL '5 minutes'  THEN 'PROBABLE'
               ELSE 'ABSENT'
             END as status
      FROM ble_tags t
      JOIN students s ON s.id = t.student_id
      WHERE t.batch_id = $1
      ORDER BY s.first_name
    `, [req.params.id]);
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
