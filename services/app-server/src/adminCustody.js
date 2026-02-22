const express = require('express');
const db      = require('./db');
const router  = express.Router();

// ── Helper: get school setting ───────────────────────────────────────────────
async function getSetting(key, fallback) {
  try {
    const r = await db.query('SELECT value FROM school_settings WHERE key=$1', [key]);
    return r.rows[0] ? r.rows[0].value : fallback;
  } catch(e) { return fallback; }
}

// GET /api/custody/overview — all students with current custody
router.get('/overview', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.student_id as school_id,
        sc.current_teacher_id, sc.current_zone_id, sc.custody_since,
        u.username as teacher_username, u.full_name as teacher_name,
        z.name as zone_name, z.zone_type,
        t.mac_address as tag_mac, t.last_seen_at, t.battery_mv,
        ps.state as presence_state
      FROM students s
      LEFT JOIN student_custody sc ON sc.student_id = s.id
      LEFT JOIN users u ON u.id = sc.current_teacher_id
      LEFT JOIN zones z ON z.id = sc.current_zone_id
      LEFT JOIN ble_tags t ON t.student_id = s.id AND t.is_active = true
      LEFT JOIN presence_states ps ON ps.student_id = s.id
      WHERE s.is_active = true
      ORDER BY u.username NULLS LAST, s.last_name
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/my-students — students in current teacher custody
router.get('/my-students', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.student_id as school_id,
        sc.current_zone_id, sc.custody_since,
        z.name as zone_name, z.zone_type,
        t.mac_address as tag_mac, t.last_seen_at, t.battery_mv,
        t.last_rssi, ps.state as presence_state,
        -- pending outgoing transfers
        (SELECT COUNT(*) FROM custody_transfers ct
         WHERE ct.student_id=s.id AND ct.from_teacher_id=$1
         AND ct.status='PENDING') as pending_out
      FROM students s
      JOIN student_custody sc ON sc.student_id=s.id AND sc.current_teacher_id=$1
      LEFT JOIN zones z ON z.id=sc.current_zone_id
      LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=s.id
      WHERE s.is_active=true
      ORDER BY s.last_name
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/incoming — pending transfers to this teacher
router.get('/incoming', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT ct.*, ct.transfer_group,
        s.first_name, s.last_name,
        fu.full_name as from_teacher_name, fu.username as from_teacher_username,
        z.name as to_zone_name,
        EXTRACT(EPOCH FROM (ct.expires_at - NOW())) as seconds_remaining
      FROM custody_transfers ct
      JOIN students s ON s.id=ct.student_id
      JOIN users fu ON fu.id=ct.from_teacher_id
      JOIN zones z ON z.id=ct.to_zone_id
      WHERE ct.to_teacher_id=$1 AND ct.status='PENDING'
        AND ct.expires_at > NOW()
      ORDER BY ct.transfer_group, ct.initiated_at
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/teachers-zones — all teachers with their zones (for transfer UI)
router.get('/teachers-zones', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT u.id, u.username, u.full_name,
        JSON_AGG(JSON_BUILD_OBJECT(
          'zone_id', z.id, 'zone_name', z.name,
          'zone_type', z.zone_type, 'zone_role', tz.zone_role
        )) as zones
      FROM users u
      JOIN teacher_zones tz ON tz.teacher_id=u.id
      JOIN zones z ON z.id=tz.zone_id
      WHERE u.role='TEACHER' AND u.is_active=true AND u.id != $1
      GROUP BY u.id ORDER BY u.full_name
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/transfer — initiate batch transfer
router.post('/transfer', async (req, res) => {
  try {
    const { student_ids, to_teacher_id, to_zone_id, notes } = req.body;
    if (!student_ids?.length || !to_teacher_id || !to_zone_id)
      return res.status(400).json({ error: 'student_ids, to_teacher_id, to_zone_id required' });

    const timeoutMins = await getSetting('custody_transfer_timeout_minutes', '5');
    const groupId = (await db.query('SELECT uuid_generate_v4() as id')).rows[0].id;

    // Verify all students are in caller custody (unless IT/DIRECTOR)
    if (req.user.role === 'TEACHER') {
      const check = await db.query(
        `SELECT COUNT(*) FROM student_custody
         WHERE student_id = ANY($1) AND current_teacher_id=$2`,
        [student_ids, req.user.id]);
      if (parseInt(check.rows[0].count) !== student_ids.length)
        return res.status(403).json({ error: 'Some students not in your custody' });
    }

    // Create transfer records
    const transfers = [];
    for (const sid of student_ids) {
      const custody = await db.query(
        'SELECT current_teacher_id FROM student_custody WHERE student_id=$1', [sid]);
      const fromTeacherId = custody.rows[0]?.current_teacher_id || req.user.id;
      const r = await db.query(`
        INSERT INTO custody_transfers
          (transfer_group,student_id,from_teacher_id,to_teacher_id,to_zone_id,initiated_by,notes,expires_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7, NOW() + ($8 || ' minutes')::INTERVAL)
        RETURNING *`,
        [groupId, sid, fromTeacherId, to_teacher_id, to_zone_id, req.user.id, notes||null, timeoutMins]);
      transfers.push(r.rows[0]);
    }

    console.log(`📤 Transfer initiated: ${student_ids.length} students → teacher ${to_teacher_id}`);
    res.json({ success:true, transfer_group:groupId, count:transfers.length, transfers });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/transfer/:group/accept — accept all in group
router.post('/transfer/:group/accept', async (req, res) => {
  try {
    const pending = await db.query(`
      SELECT * FROM custody_transfers
      WHERE transfer_group=$1 AND to_teacher_id=$2
        AND status='PENDING' AND expires_at > NOW()
    `, [req.params.group, req.user.id]);

    if (!pending.rows.length)
      return res.status(404).json({ error: 'No pending transfers found or expired' });

    for (const t of pending.rows) {
      // Update custody
      await db.query(`
        INSERT INTO student_custody (student_id,current_teacher_id,current_zone_id,custody_since,updated_at)
        VALUES ($1,$2,$3,NOW(),NOW())
        ON CONFLICT (student_id) DO UPDATE
        SET current_teacher_id=$2, current_zone_id=$3, custody_since=NOW(), updated_at=NOW()
      `, [t.student_id, req.user.id, t.to_zone_id]);

      // Mark accepted
      await db.query(`UPDATE custody_transfers SET status='ACCEPTED',responded_at=NOW() WHERE id=$1`, [t.id]);

      // Log
      await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id)
        VALUES ($1,$2,'CUSTODY_ACCEPTED','student',$3)`,
        [req.user.id, req.user.role, t.student_id]);
    }

    console.log(`✅ Custody accepted: group ${req.params.group} (${pending.rows.length} students)`);
    res.json({ success:true, accepted: pending.rows.length });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/custody/transfer/:group/reject
router.post('/transfer/:group/reject', async (req, res) => {
  try {
    const r = await db.query(`
      UPDATE custody_transfers SET status='REJECTED', responded_at=NOW()
      WHERE transfer_group=$1 AND to_teacher_id=$2 AND status='PENDING'
      RETURNING student_id, from_teacher_id
    `, [req.params.group, req.user.id]);

    console.log(`❌ Custody rejected: group ${req.params.group}`);
    res.json({ success:true, rejected: r.rows.length });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/settings
router.get('/settings', async (req, res) => {
  try {
    const r = await db.query('SELECT key, value FROM school_settings ORDER BY key');
    const settings = {};
    r.rows.forEach(row => settings[row.key] = row.value);
    res.json(settings);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/custody/settings  (IT only)
router.put('/settings', async (req, res) => {
  try {
    if (req.user.role !== 'IT')
      return res.status(403).json({ error: 'IT Admin only' });
    for (const [key, value] of Object.entries(req.body)) {
      await db.query(`INSERT INTO school_settings (key,value,updated_at) VALUES ($1,$2,NOW())
        ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()`, [key, String(value)]);
    }
    res.json({ success:true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/custody/teacher-zones/:teacherId
router.get('/teacher-zones/:teacherId', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT tz.zone_role, z.id, z.name, z.zone_type
      FROM teacher_zones tz JOIN zones z ON z.id=tz.zone_id
      WHERE tz.teacher_id=$1 ORDER BY tz.zone_role, z.name
    `, [req.params.teacherId]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/custody/teacher-zones/:teacherId  (full replace)
router.put('/teacher-zones/:teacherId', async (req, res) => {
  try {
    const { zones } = req.body; // [{zone_id, zone_role}]
    await db.query('DELETE FROM teacher_zones WHERE teacher_id=$1', [req.params.teacherId]);
    for (const z of (zones||[])) {
      await db.query(
        'INSERT INTO teacher_zones (teacher_id,zone_id,zone_role) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING',
        [req.params.teacherId, z.zone_id, z.zone_role||'PRIMARY']);
    }
    res.json({ success:true, count: zones?.length||0 });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
