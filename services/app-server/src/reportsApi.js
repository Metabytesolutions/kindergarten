'use strict';
const express  = require('express');
const router   = express.Router();
const db       = require('./db');
const { requireAuth, requireRole } = require('./auth');
const { logEvent } = require('./eventLogger');
const { runHealthCheck, getHealthSummary } = require('./systemMonitor');
const { runEOD } = require('./eodService');

router.use(requireAuth);

// GET /api/reports/attendance?date=YYYY-MM-DD
router.get('/attendance', async (req, res) => {
  try {
    const date = req.query.date || new Date().toISOString().split('T')[0];

    const summary = await db.query(`
      SELECT
        COUNT(*) FILTER (WHERE status='PRESENT')::int     as present,
        COUNT(*) FILTER (WHERE status='ABSENT')::int      as absent,
        COUNT(*) FILTER (WHERE status='CHECKED_OUT')::int as checked_out,
        COUNT(*) FILTER (WHERE status='NO_SHOW')::int     as no_show,
        COUNT(*)::int                                       as total,
        ROUND(AVG(total_minutes))::int                      as avg_minutes
      FROM attendance_archive WHERE archive_date=$1
    `, [date]);

    const byTeacher = await db.query(`
      SELECT
        teacher_name,
        COUNT(*) FILTER (WHERE status='PRESENT')::int     as present,
        COUNT(*) FILTER (WHERE status='ABSENT')::int      as absent,
        COUNT(*) FILTER (WHERE status='CHECKED_OUT')::int as checked_out,
        COUNT(*) FILTER (WHERE status='NO_SHOW')::int     as no_show,
        COUNT(*)::int as total
      FROM attendance_archive
      WHERE archive_date=$1
      GROUP BY teacher_name ORDER BY teacher_name
    `, [date]);

    const students = await db.query(`
      SELECT
        student_name, student_grade, teacher_name, status,
        first_accepted_at, checked_out_at, total_minutes, tag_mac
      FROM attendance_archive
      WHERE archive_date=$1
      ORDER BY teacher_name, student_name
    `, [date]);

    res.json({
      date, summary: summary.rows[0],
      by_teacher: byTeacher.rows,
      students: students.rows,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/attendance-range?from=YYYY-MM-DD&to=YYYY-MM-DD
router.get('/attendance-range', async (req, res) => {
  try {
    const from = req.query.from;
    const to   = req.query.to || new Date().toISOString().split('T')[0];

    const r = await db.query(`
      SELECT
        archive_date,
        COUNT(*) FILTER (WHERE status='PRESENT')::int     as present,
        COUNT(*) FILTER (WHERE status='ABSENT')::int      as absent,
        COUNT(*) FILTER (WHERE status='CHECKED_OUT')::int as checked_out,
        COUNT(*) FILTER (WHERE status='NO_SHOW')::int     as no_show,
        COUNT(*)::int as total
      FROM attendance_archive
      WHERE archive_date BETWEEN $1 AND $2
      GROUP BY archive_date ORDER BY archive_date DESC
    `, [from, to]);

    res.json({ from, to, days: r.rows });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/student-history/:studentId
router.get('/student-history/:studentId', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT archive_date, status, teacher_name,
             first_accepted_at, checked_out_at, total_minutes
      FROM attendance_archive
      WHERE student_id=$1
      ORDER BY archive_date DESC LIMIT 30
    `, [req.params.studentId]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/health — system health status
router.get('/health', async (req, res) => {
  try {
    const summary = await getHealthSummary();
    const recent  = await db.query(`
      SELECT service, status, detail, check_time
      FROM system_health_log
      WHERE check_time > NOW()-INTERVAL '1 hour'
        AND status != 'OK'
      ORDER BY check_time DESC LIMIT 20
    `);
    res.json({ current: summary, recent_issues: recent.rows });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/reports/health/check — manual health check
router.post('/health/check', requireRole(['IT']), async (req, res) => {
  try {
    const results = await runHealthCheck();
    res.json({ results });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/reports/eod/trigger — manual EOD (IT Admin)
router.post('/eod/trigger', requireRole(['IT']), async (req, res) => {
  try {
    const result = await runEOD('MANUAL_TRIGGER');
    res.json(result);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/schedules — list scheduled reports
router.get('/schedules', requireRole(['IT']), async (req, res) => {
  try {
    const r = await db.query(
      'SELECT * FROM report_schedules ORDER BY created_at DESC');
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/reports/schedules — create scheduled report
router.post('/schedules', requireRole(['IT']), async (req, res) => {
  try {
    const { name, report_type, schedule, recipients, params } = req.body;
    const r = await db.query(`
      INSERT INTO report_schedules
        (name, report_type, schedule, recipients, params, created_by)
      VALUES ($1,$2,$3,$4,$5,$6) RETURNING *
    `, [name, report_type, schedule,
        recipients || [], params || {}, req.user.id]);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/reports/schedules/:id — update schedule
router.put('/schedules/:id', requireRole(['IT']), async (req, res) => {
  try {
    const { is_active } = req.body;
    const r = await db.query(
      'UPDATE report_schedules SET is_active=$1 WHERE id=$2 RETURNING *',
      [is_active, req.params.id]);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/available-dates — dates with archived data
router.get('/available-dates', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT DISTINCT archive_date,
        COUNT(*)::int as total_students,
        COUNT(*) FILTER (WHERE status='PRESENT')::int as present,
        COUNT(*) FILTER (WHERE status='ABSENT')::int  as absent
      FROM attendance_archive
      GROUP BY archive_date ORDER BY archive_date DESC LIMIT 30
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/students/:id/remove — director removes student
router.post('/student-remove/:id', requireRole(['IT','DIRECTOR']), async (req, res) => {
  try {
    const sid    = req.params.id;
    const { reason } = req.body;

    const sv = await db.query('SELECT * FROM students WHERE id=$1', [sid]);
    if (!sv.rows[0]) return res.status(404).json({ error: 'Student not found' });
    const student = sv.rows[0];

    // Get all session history
    const sessions = await db.query(
      'SELECT * FROM student_sessions WHERE student_id=$1', [sid]);

    // Get tag
    const tag = await db.query(
      'SELECT * FROM ble_tags WHERE student_id=$1 AND is_active=true', [sid]);

    // Archive student record
    await db.query(`
      INSERT INTO student_archive
        (original_id, student_data, session_history, removed_by,
         removal_reason, tag_mac_released)
      VALUES ($1,$2,$3,$4,$5,$6)
    `, [sid, JSON.stringify(student),
        JSON.stringify(sessions.rows),
        req.user.id, reason || null,
        tag.rows[0]?.mac_address || null]);

    // Release tag → INVENTORY
    if (tag.rows[0]) {
      await db.query(`
        UPDATE ble_tags SET
          status='INVENTORY', student_id=NULL,
          assigned_to='NONE', label='Unassigned'
        WHERE id=$1
      `, [tag.rows[0].id]);
    }

    // Remove custody + sessions
    await db.query('DELETE FROM student_custody WHERE student_id=$1', [sid]);
    await db.query('DELETE FROM student_permitted_zones WHERE student_id=$1', [sid]);

    // Soft-delete student
    await db.query(`
      UPDATE students SET is_active=false, updated_at=NOW()
      WHERE id=$1
    `, [sid]);

    await logEvent('STUDENT_REMOVED', {
      title: `${student.first_name} ${student.last_name} removed from system`,
      detail: { student: `${student.first_name} ${student.last_name}`,
                removed_by: req.user.username, reason,
                tag_released: tag.rows[0]?.mac_address || 'none' },
      studentIds: [sid], actorId: req.user.id,
    }).catch(() => {});

    console.log(`🗑️  Student removed: ${student.first_name} ${student.last_name}`);
    res.json({ success: true,
               tag_released: tag.rows[0]?.mac_address || null });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
