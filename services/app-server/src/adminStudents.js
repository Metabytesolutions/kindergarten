const { logEvent } = require('./eventLogger');
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// GET /api/admin/students
router.get('/', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT s.*,
        z.name as zone_name,
        u.username as teacher_username,
        u.full_name as teacher_full_name,
        t.mac_address as tag_mac,
        t.id as tag_id,
        t.label as tag_label,
        t.is_active as tag_active,
        t.battery_mv,
        t.last_seen_at
      FROM students s
      LEFT JOIN zones z ON z.id = s.zone_id
      LEFT JOIN users u ON u.id = s.teacher_id
      LEFT JOIN ble_tags t ON t.student_id = s.id AND t.is_active = true
      WHERE s.is_active = true
      ORDER BY s.last_name, s.first_name
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/admin/students/teachers  (for dropdown)
router.get('/teachers', async (req, res) => {
  try {
    const r = await db.query(`SELECT id, username, full_name, zone_id FROM users WHERE role='TEACHER' AND is_active=true ORDER BY username`);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/students
router.post('/', async (req, res) => {
  try {
    const { first_name, last_name, student_id, grade, class_name, zone_id, teacher_id, dob, guardian_name, guardian_phone } = req.body;
    if (!first_name || !last_name) return res.status(400).json({ error: 'first_name and last_name required' });
    const r = await db.query(
      `INSERT INTO students (first_name,last_name,student_id,grade,class_name,zone_id,teacher_id,dob,guardian_name,guardian_phone)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *`,
      [first_name, last_name, student_id||null, grade||null, class_name||null,
       zone_id||null, teacher_id||null, dob||null, guardian_name||null, guardian_phone||null]
    );
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'STUDENT_CREATED','student',$3)`,
      [req.user.id, req.user.role, r.rows[0].id]);
    console.log(`✅ Student created: ${first_name} ${last_name}`);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/admin/students/:id
router.put('/:id', async (req, res) => {
  try {
    const { first_name, last_name, student_id, grade, class_name, zone_id, teacher_id, dob, guardian_name, guardian_phone, is_active } = req.body;
    const r = await db.query(
      `UPDATE students SET
        first_name=COALESCE($2,first_name), last_name=COALESCE($3,last_name),
        student_id=$4, grade=$5, class_name=$6, zone_id=$7, teacher_id=$8,
        dob=$9, guardian_name=$10, guardian_phone=$11,
        is_active=COALESCE($12,is_active), updated_at=NOW()
       WHERE id=$1 RETURNING *`,
      [req.params.id, first_name, last_name, student_id||null, grade||null,
       class_name||null, zone_id||null, teacher_id||null, dob||null,
       guardian_name||null, guardian_phone||null, is_active]
    );
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// DELETE /api/admin/students/:id (soft delete)
router.delete('/:id', async (req, res) => {
  try {
    await db.query('UPDATE students SET is_active=false, updated_at=NOW() WHERE id=$1', [req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;

// GET /api/admin/students/:id/permitted-zones
router.get('/:id/permitted-zones', async (req, res) => {
  try {
    const r = await db.query(
      `SELECT z.id, z.name, z.zone_type FROM student_permitted_zones spz
       JOIN zones z ON z.id = spz.zone_id WHERE spz.student_id=$1`,
      [req.params.id]
    );
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/admin/students/:id/permitted-zones  (full replace)
router.put('/:id/permitted-zones', async (req, res) => {
  try {
    const { zone_ids } = req.body; // array of zone UUIDs
    await db.query('DELETE FROM student_permitted_zones WHERE student_id=$1', [req.params.id]);
    if (zone_ids && zone_ids.length > 0) {
      const vals = zone_ids.map((_,i) => `($1,$${i+2})`).join(',');
      await db.query(`INSERT INTO student_permitted_zones (student_id,zone_id) VALUES ${vals}`,
        [req.params.id, ...zone_ids]);
    }
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id,new_value) VALUES ($1,$2,'PERMITTED_ZONES_UPDATED','student',$3,$4)`,
      [req.user.id, req.user.role, req.params.id, JSON.stringify({zone_ids})]);
    res.json({ success: true, count: zone_ids?.length||0 });
  } catch(e) { res.status(500).json({ error: e.message }); }
});
