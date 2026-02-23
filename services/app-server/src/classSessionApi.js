'use strict';
const express  = require('express');
const router   = express.Router();
const db       = require('./db');
const { authMiddleware, requireRole } = require('./auth');
const { logEvent } = require('./eventLogger');

router.use(authMiddleware);

// GET /api/class-session/status — my session for today
router.get('/status', async (req, res) => {
  try {
    const today     = new Date().toISOString().split('T')[0];
    const teacherId = req.user.id;

    const cs = await db.query(`
      SELECT cs.*,
        (SELECT COUNT(*) FROM students s
         WHERE s.teacher_id=$1 AND s.is_active=true)::int as total_students,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status='ACCEPTED')::int as accepted_count,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status='ABSENT')::int as absent_count,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status='CHECKED_OUT')::int as checked_out_count,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status IN ('EXPECTED'))::int as expected_count
      FROM class_sessions cs
      WHERE cs.teacher_id=$1 AND cs.session_date=$2
    `, [teacherId, today]);

    // Get session window settings
    const settings = await db.query(`
      SELECT key, value FROM school_settings
      WHERE key IN ('session_start_hour','session_end_hour',
                    'class_close_warning_minutes')
    `);
    const cfg = {};
    settings.rows.forEach(r => cfg[r.key] = r.value);

    const now  = new Date();
    const hour = now.getHours();
    const sessionStartHour = parseInt(cfg.session_start_hour || '7');
    const sessionEndHour   = parseInt(cfg.session_end_hour   || '19');
    const sessionActive    = hour >= sessionStartHour && hour < sessionEndHour;

    res.json({
      session: cs.rows[0] || null,
      session_active: sessionActive,
      session_start_hour: sessionStartHour,
      session_end_hour:   sessionEndHour,
      close_warning_minutes: parseInt(cfg.class_close_warning_minutes || '30'),
      current_hour: hour,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/class-session/open — teacher opens class
router.post('/open', async (req, res) => {
  try {
    const teacherId = req.user.id;
    const today     = new Date().toISOString().split('T')[0];
    const now       = new Date();
    const hour      = now.getHours();

    // Get session settings
    const cfg = await db.query(
      "SELECT key,value FROM school_settings WHERE key IN ('session_start_hour','class_close_warning_minutes')");
    const settings = {};
    cfg.rows.forEach(r => settings[r.key] = parseInt(r.value));
    const startHour = settings.session_start_hour || 7;

    // Check if already open
    const existing = await db.query(
      'SELECT * FROM class_sessions WHERE teacher_id=$1 AND session_date=$2',
      [teacherId, today]);
    if (existing.rows[0]?.status === 'OPEN')
      return res.status(400).json({ error: 'Class already open' });
    if (existing.rows[0]?.status === 'CLOSED')
      return res.status(400).json({ error: 'Class already closed for today' });

    // Count students
    const sc = await db.query(
      'SELECT COUNT(*)::int as c FROM students WHERE teacher_id=$1 AND is_active=true',
      [teacherId]);

    await db.query(`
      INSERT INTO class_sessions
        (teacher_id, session_date, status, opened_at, student_count)
      VALUES ($1,$2,'OPEN',NOW(),$3)
      ON CONFLICT (teacher_id, session_date)
      DO UPDATE SET status='OPEN', opened_at=NOW(), student_count=$3
    `, [teacherId, today, sc.rows[0].c]);

    await logEvent('CLASS_OPENED', {
      title: `Class opened by ${req.user.username}`,
      detail: { teacher: req.user.username, opened_at: now.toISOString(),
                student_count: sc.rows[0].c },
      actorId: teacherId,
    }).catch(() => {});

    // Broadcast to director
    if (global.broadcastDirectorEvent) {
      global.broadcastDirectorEvent(JSON.stringify({
        type: 'CLASS_OPENED', teacherId,
        teacherName: req.user.full_name || req.user.username,
      }));
    }

    console.log(`📖 Class opened by ${req.user.username}`);
    res.json({ success: true, status: 'OPEN' });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/class-session/close — teacher closes class
router.post('/close', async (req, res) => {
  try {
    const teacherId = req.user.id;
    const today     = new Date().toISOString().split('T')[0];
    const now       = new Date();
    const { force } = req.body;

    // Check session exists and is open
    const cs = await db.query(
      'SELECT * FROM class_sessions WHERE teacher_id=$1 AND session_date=$2',
      [teacherId, today]);
    if (!cs.rows[0] || cs.rows[0].status !== 'OPEN')
      return res.status(400).json({ error: 'No open class session found' });

    // Check all students checked out or absent
    const blocking = await db.query(`
      SELECT s.first_name, s.last_name, ss.status
      FROM student_sessions ss
      JOIN students s ON s.id=ss.student_id
      WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
        AND ss.status IN ('ACCEPTED','EXPECTED')
    `, [teacherId, today]);

    if (blocking.rows.length > 0 && !force) {
      return res.json({
        blocked: true,
        reason: 'Students still checked in',
        students: blocking.rows.map(s =>
          ({ name: `${s.first_name} ${s.last_name}`, status: s.status })),
      });
    }

    // Get counts for summary
    const counts = await db.query(`
      SELECT
        COUNT(*) FILTER (WHERE status='ACCEPTED')::int    as present,
        COUNT(*) FILTER (WHERE status='ABSENT')::int      as absent,
        COUNT(*) FILTER (WHERE status='CHECKED_OUT')::int as checked_out,
        COUNT(*) FILTER (WHERE status='EXPECTED')::int    as no_show
      FROM student_sessions
      WHERE home_teacher_id=$1 AND batch_date=$2
    `, [teacherId, today]);
    const c = counts.rows[0];

    // Get session end hour
    const endHourR = await db.query(
      "SELECT value FROM school_settings WHERE key='session_end_hour'");
    const endHour  = parseInt(endHourR.rows[0]?.value || '19');
    const closedEarly = now.getHours() < endHour;

    // Close the session
    await db.query(`
      UPDATE class_sessions SET
        status='CLOSED', closed_at=NOW(),
        closed_early=$3,
        present_count=$4, absent_count=$5,
        checked_out_count=$6, no_show_count=$7
      WHERE teacher_id=$1 AND session_date=$2
    `, [teacherId, today, closedEarly,
        c.present, c.absent, c.checked_out, c.no_show]);

    // Force-checkout any remaining expected students
    if (force && blocking.rows.length > 0) {
      await db.query(`
        UPDATE student_sessions SET status='CHECKED_OUT', checkout_confirmed_at=NOW()
        WHERE home_teacher_id=$1 AND batch_date=$2
          AND status IN ('ACCEPTED','EXPECTED')
      `, [teacherId, today]);
      // Remove their custody
      for (const s of blocking.rows) {
        await db.query(
          'DELETE FROM student_custody WHERE current_teacher_id=$1',
          [teacherId]);
      }
    }

    await logEvent(closedEarly ? 'CLASS_CLOSED_EARLY' : 'CLASS_CLOSED', {
      title: `Class closed by ${req.user.username}${closedEarly?' (early)':''} `,
      detail: { teacher: req.user.username, closed_at: now.toISOString(),
                present: c.present, absent: c.absent,
                checked_out: c.checked_out, closed_early: closedEarly },
      actorId: teacherId,
    }).catch(() => {});

    if (global.broadcastDirectorEvent) {
      global.broadcastDirectorEvent(JSON.stringify({
        type: 'CLASS_CLOSED', teacherId,
        teacherName: req.user.full_name || req.user.username,
        summary: c, closedEarly,
      }));
    }

    // Check if ALL classes are closed — trigger EOD if so
    const openClasses = await db.query(`
      SELECT COUNT(*)::int as c FROM class_sessions
      WHERE session_date=$1 AND status='OPEN'
    `, [today]);
    if (openClasses.rows[0].c === 0) {
      console.log('🌙 All classes closed — triggering EOD');
      const { runEOD } = require('./eodService');
      setTimeout(() => runEOD('ALL_CLASSES_CLOSED'), 5000);
    }

    console.log(`🔒 Class closed by ${req.user.username}`);
    res.json({ success: true, status: 'CLOSED', summary: c, closedEarly });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/class-session/all-status — director view of all classrooms
router.get('/all-status', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
    const r = await db.query(`
      SELECT
        u.id as teacher_id, u.full_name as teacher_name, u.username,
        cs.status as class_status, cs.opened_at, cs.closed_at,
        cs.present_count, cs.absent_count, cs.checked_out_count,
        z.name as zone_name,
        (SELECT COUNT(*)::int FROM students s
         WHERE s.teacher_id=u.id AND s.is_active=true) as total_students,
        (SELECT COUNT(*)::int FROM student_sessions ss
         WHERE ss.home_teacher_id=u.id AND ss.batch_date=$1
           AND ss.status='ACCEPTED') as currently_in
      FROM users u
      LEFT JOIN class_sessions cs ON cs.teacher_id=u.id AND cs.session_date=$1
      LEFT JOIN zones z ON z.id=(
        SELECT tz.zone_id FROM teacher_zones tz
        WHERE tz.teacher_id=u.id AND tz.zone_role='PRIMARY' LIMIT 1)
      WHERE u.role='TEACHER' AND u.is_active=true
      ORDER BY u.full_name
    `, [today]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
