
'use strict';
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// Restrict to DIRECTOR + IT
router.use((req,res,next)=>{
  if(!['DIRECTOR','IT'].includes(req.user.role))
    return res.status(403).json({error:'Director or IT Admin only'});
  next();
});

// GET /api/director/overview — school-wide snapshot
router.get('/overview', async (req,res)=>{
  try {
    // All students with custody + presence
    const students = await db.query(`
      SELECT s.id, s.first_name, s.last_name, s.grade, s.student_id as school_id,
        sc.current_teacher_id, sc.current_zone_id, sc.custody_since,
        u.full_name  as teacher_name, u.username as teacher_username,
        u.teacher_type,
        z.name       as zone_name, z.zone_type,
        t.mac_address as tag_mac, t.last_rssi, t.battery_mv, t.last_seen_at,
        ps.state     as presence_state
      FROM students s
      LEFT JOIN student_custody sc ON sc.student_id=s.id
      LEFT JOIN users u  ON u.id=sc.current_teacher_id
      LEFT JOIN zones z  ON z.id=sc.current_zone_id
      LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=s.id
        LEFT JOIN student_sessions ss ON ss.student_id=s.id AND ss.batch_date=CURRENT_DATE
      WHERE s.is_active=true
      ORDER BY u.full_name NULLS LAST, s.last_name
    `);

    // All teachers with student counts
    const teachers = await db.query(`
      SELECT u.id, u.username, u.full_name, u.teacher_type,
        z.name as zone_name, z.zone_type,
        COUNT(sc.student_id) as student_count,
        COUNT(sc.student_id) FILTER (
          WHERE ps.state IN ('CONFIRMED_PRESENT','PROBABLE_PRESENT')
        ) as present_count,
        COUNT(sc.student_id) FILTER (
          WHERE ps.state='MISSING' OR ps.state IS NULL
        ) as missing_count
      FROM users u
      LEFT JOIN zones z ON z.id=u.zone_id
      LEFT JOIN student_custody sc ON sc.current_teacher_id=u.id
      LEFT JOIN students s ON s.id=sc.student_id AND s.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=sc.student_id
      WHERE u.role IN ('TEACHER','SUBSTITUTE') AND u.is_active=true
      GROUP BY u.id, z.name, z.zone_type
      ORDER BY u.teacher_type, u.full_name
    `);

    // Pending transfers
    const transfers = await db.query(`
      SELECT ct.transfer_group, ct.status,
        fu.full_name as from_name, tu.full_name as to_name,
        z.name as zone_name, ct.initiated_at,
        COUNT(ct.student_id) as student_count,
        EXTRACT(EPOCH FROM (ct.expires_at-NOW()))::int as seconds_remaining
      FROM custody_transfers ct
      JOIN users fu ON fu.id=ct.from_teacher_id
      JOIN users tu ON tu.id=ct.to_teacher_id
      JOIN zones z  ON z.id=ct.to_zone_id
      WHERE ct.status='PENDING' AND ct.expires_at>NOW()
      GROUP BY ct.transfer_group,ct.status,fu.full_name,tu.full_name,z.name,
               ct.initiated_at,ct.expires_at
      ORDER BY ct.initiated_at
    `);

    // Summary counts
    const states = students.rows.map(s=>s.presence_state||'UNKNOWN');
    res.json({
      summary: {
        total:    students.rows.length,
        absent:   students.rows.filter(s=>s.session_status==='ABSENT').length,
        present:  states.filter(s=>['CONFIRMED_PRESENT','PROBABLE_PRESENT'].includes(s)).length,
        roaming:  states.filter(s=>s==='ROAMING').length,
        missing:  states.filter(s=>['MISSING','UNKNOWN'].includes(s)).length,
        teachers: teachers.rows.length,
        pending_transfers: transfers.rows.length,
      },
      students:  students.rows,
      teachers:  teachers.rows,
      transfers: transfers.rows,
    });
  } catch(e){ res.status(500).json({error:e.message}); }
});

// GET /api/director/student/:id — full student history
router.get('/student/:id', async(req,res)=>{
  try {
    const [student, custody, transfers, events] = await Promise.all([
      db.query(`
        SELECT s.*, u.full_name as teacher_name, z.name as zone_name,
          t.mac_address as tag_mac, t.battery_mv, t.last_seen_at, t.last_rssi,
          ps.state as presence_state,
          ss.status as session_status
        FROM students s
        LEFT JOIN users u ON u.id=s.teacher_id
        LEFT JOIN zones z ON z.id=s.zone_id
        LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
        LEFT JOIN presence_states ps ON ps.student_id=s.id
        LEFT JOIN student_sessions ss ON ss.student_id=s.id AND ss.batch_date=CURRENT_DATE
        WHERE s.id=$1`, [req.params.id]),
      db.query(`
        SELECT sc.*, u.full_name as teacher_name, z.name as zone_name
        FROM student_custody sc
        JOIN users u ON u.id=sc.current_teacher_id
        JOIN zones z ON z.id=sc.current_zone_id
        WHERE sc.student_id=$1`, [req.params.id]),
      db.query(`
        SELECT ct.*, fu.full_name as from_name, tu.full_name as to_name,
          z.name as zone_name
        FROM custody_transfers ct
        JOIN users fu ON fu.id=ct.from_teacher_id
        JOIN users tu ON tu.id=ct.to_teacher_id
        JOIN zones z  ON z.id=ct.to_zone_id
        WHERE ct.student_id=$1
        ORDER BY ct.initiated_at DESC LIMIT 20`, [req.params.id]),
      db.query(`
        SELECT * FROM director_events
        WHERE $1=ANY(student_ids)
        ORDER BY created_at DESC LIMIT 30`, [req.params.id]),
    ]);
    if(!student.rows[0]) return res.status(404).json({error:'Student not found'});
    res.json({
      student:   student.rows[0],
      custody:   custody.rows[0],
      transfers: transfers.rows,
      events:    events.rows,
    });
  } catch(e){ res.status(500).json({error:e.message}); }
});

module.exports = router;
