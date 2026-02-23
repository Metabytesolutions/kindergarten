
'use strict';
const express    = require('express');
const db         = require('./db');
const { logEvent } = require('./eventLogger');
const { scheduleMissingAlert } = require('./checkoutTracker');
const router     = express.Router();

// ── GET /api/session/roster — today's expected students ───────────────────────
router.get('/roster', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
    const teacherId = req.user.id;

    // All students assigned to this teacher (their home teacher)
    const r = await db.query(`
      SELECT
        s.id, s.first_name, s.last_name, s.grade, s.student_id as school_id,
        -- Today's session status
        COALESCE(ss.status, 'EXPECTED') as session_status,
        ss.accepted_at, ss.checkout_initiated_at, ss.checkout_confirmed_at,
        -- Current custody
        sc.current_teacher_id, sc.current_zone_id,
        cu.full_name  as custody_teacher_name,
        cu.username   as custody_teacher_username,
        cz.name       as custody_zone_name,
        -- Home zone
        z.name        as home_zone_name,
        -- BLE
        t.mac_address as tag_mac, t.last_rssi, t.battery_mv, t.last_seen_at,
        -- Presence
        ps.state      as presence_state,
        -- Pending transfers OUT
        (SELECT COUNT(*)::int FROM custody_transfers ct
         WHERE ct.student_id=s.id AND ct.from_teacher_id=$1
           AND ct.status='PENDING') as transfer_pending_out,
        -- Pending transfers IN
        (SELECT COUNT(*)::int FROM custody_transfers ct
         WHERE ct.student_id=s.id AND ct.to_teacher_id=$1
           AND ct.status='PENDING') as transfer_pending_in
      FROM students s
      LEFT JOIN student_sessions ss ON ss.student_id=s.id AND ss.batch_date=$2
      LEFT JOIN student_custody sc  ON sc.student_id=s.id
      LEFT JOIN users cu ON cu.id=sc.current_teacher_id
      LEFT JOIN zones cz ON cz.id=sc.current_zone_id
      LEFT JOIN zones z  ON z.id=s.zone_id
      LEFT JOIN ble_tags t ON t.student_id=s.id AND t.is_active=true
      LEFT JOIN presence_states ps ON ps.student_id=s.id
      WHERE s.teacher_id=$1 AND s.is_active=true
      ORDER BY s.last_name
    `, [teacherId, today]);

    // Teacher info + zones
    const teacher = await db.query(`
      SELECT u.id, u.username, u.full_name, u.teacher_type,
        z.name as zone_name, z.id as zone_id,
        JSON_AGG(JSON_BUILD_OBJECT(
          'zone_id',tz2.zone_id,'zone_name',z2.name,
          'zone_type',z2.zone_type,'zone_role',tz2.zone_role
        )) FILTER (WHERE tz2.zone_id IS NOT NULL) as all_zones
      FROM users u
      LEFT JOIN zones z ON z.id=u.zone_id
      LEFT JOIN teacher_zones tz2 ON tz2.teacher_id=u.id
      LEFT JOIN zones z2 ON z2.id=tz2.zone_id
      WHERE u.id=$1
      GROUP BY u.id, z.name, z.id
    `, [teacherId]);

    const students = r.rows;
    const inMyCustody   = students.filter(s=>s.current_teacher_id===teacherId);
    const withOther     = students.filter(s=>s.current_teacher_id!==teacherId && s.session_status==='ACCEPTED');
    const expected      = students.filter(s=>s.session_status==='EXPECTED');
    const checkedOut    = students.filter(s=>['CHECKOUT_PENDING','CHECKED_OUT'].includes(s.session_status));

    res.json({
      teacher:    teacher.rows[0],
      students,
      summary: {
        total:      students.length,
        expected:   expected.length,
        in_custody: inMyCustody.length,
        with_other: withOther.length,
        checked_out:checkedOut.length,
        missing:    inMyCustody.filter(s=>s.presence_state==='MISSING').length,
      }
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/session/accept/:studentId — teacher accepts individual student
router.post('/accept/:studentId', async (req, res) => {
  try {
    const today     = new Date().toISOString().split('T')[0];
    const teacherId = req.user.id;
    const sid       = req.params.studentId;

    // Verify student belongs to this teacher
    const sv = await db.query(
      'SELECT id,first_name,last_name,zone_id FROM students WHERE id=$1 AND teacher_id=$2',
      [sid, teacherId]);
    if (!sv.rows[0])
      return res.status(403).json({ error: 'Student not assigned to you' });
    const student = sv.rows[0];

    // Upsert session
    await db.query(`
      INSERT INTO student_sessions (student_id,home_teacher_id,batch_date,status,accepted_at)
      VALUES ($1,$2,$3,'ACCEPTED',NOW())
      ON CONFLICT (student_id,batch_date)
      DO UPDATE SET status='ACCEPTED', accepted_at=NOW()
    `, [sid, teacherId, today]);

    // Set/confirm custody
    await db.query(`
      INSERT INTO student_custody (student_id,current_teacher_id,current_zone_id,custody_since,updated_at)
      VALUES ($1,$2,$3,NOW(),NOW())
      ON CONFLICT (student_id)
      DO UPDATE SET current_teacher_id=$2, current_zone_id=$3, custody_since=NOW(), updated_at=NOW()
    `, [sid, teacherId, student.zone_id]);

    // Log events
    await logEvent('STUDENT_CHECKED_IN', {
      title: `${student.first_name} ${student.last_name} checked in — ${req.user.full_name||req.user.username}`,
      detail: { teacher: req.user.username, student_name: `${student.first_name} ${student.last_name}` },
      studentIds: [sid], actorId: teacherId, zoneId: student.zone_id,
    });

    // Check if this is the first acceptance (log SESSION_STARTED once)
    const others = await db.query(`
      SELECT COUNT(*) FROM student_sessions
      WHERE home_teacher_id=$1 AND batch_date=$2 AND status='ACCEPTED'`,
      [teacherId, today]);
    if (parseInt(others.rows[0].count) === 1) {
      await logEvent('SESSION_STARTED', {
        title: `Morning session started — ${req.user.full_name||req.user.username}`,
        detail: { teacher: req.user.username, zone: req.user.zone_name },
        actorId: teacherId,
      });
    }

    res.json({ success: true, student: sv.rows[0] });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/session/checkout/:studentId — initiate checkout
router.post('/checkout/:studentId', async (req, res) => {
  try {
    const today     = new Date().toISOString().split('T')[0];
    const teacherId = req.user.id;
    const sid       = req.params.studentId;

    // Verify custody
    const cv = await db.query(
      'SELECT sc.*, s.first_name, s.last_name FROM student_custody sc JOIN students s ON s.id=sc.student_id WHERE sc.student_id=$1 AND sc.current_teacher_id=$2',
      [sid, teacherId]);
    if (!cv.rows[0])
      return res.status(403).json({ error: 'Student not in your custody' });

    const student = cv.rows[0];
    const timeoutMin = await db.query(
      "SELECT value FROM school_settings WHERE key='checkout_exit_timeout_minutes'");
    const timeout = parseInt(timeoutMin.rows[0]?.value||'10');

    // Mark checkout pending
    await db.query(`
      INSERT INTO student_sessions (student_id,home_teacher_id,batch_date,status,checkout_initiated_at,checkout_initiated_by)
      VALUES ($1,$2,$3,'CHECKOUT_PENDING',NOW(),$4)
      ON CONFLICT (student_id,batch_date)
      DO UPDATE SET status='CHECKOUT_PENDING', checkout_initiated_at=NOW(), checkout_initiated_by=$4
    `, [sid, teacherId, today, teacherId]);

    // Log checkout initiated event
    await logEvent('STUDENT_CHECKED_OUT', {
      title: `${student.first_name} ${student.last_name} checked out`,
      detail: { student: `${student.first_name} ${student.last_name}`,
                checked_out_by: req.user.username,
                status: 'CHECKED_OUT' },
      studentIds: [sid], actorId: teacherId,
    }).catch(()=>{});
    // Schedule CRITICAL alert if student never reaches EXIT
    scheduleMissingAlert(sid, `${student.first_name} ${student.last_name}`, teacherId, timeout);
    console.log(`🚪 ${student.first_name} ${student.last_name} checked out zone (${timeout}min timeout)`);

    // Schedule timeout alert (fire and forget)
    setTimeout(async () => {
      try {
        const check = await db.query(
          "SELECT status FROM student_sessions WHERE student_id=$1 AND batch_date=$2",
          [sid, today]);
        if (check.rows[0]?.status === 'CHECKOUT_PENDING') {
          await logEvent('STUDENT_MISSING', {
            title: `Checkout timeout: ${student.first_name} ${student.last_name} never reached EXIT zone`,
            detail: { student_id: sid, initiated_by: req.user.username,
                      timeout_minutes: timeout },
            studentIds: [sid], actorId: null,
          });
        }
      } catch(e) {}
    }, timeout * 60 * 1000);

    res.json({ success: true, timeout_minutes: timeout });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/session/confirm-checkout/:studentId — called by system when EXIT zone detected
router.post('/confirm-checkout/:studentId', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];
    const sid   = req.params.studentId;

    const sv = await db.query(
      'SELECT s.*, ss.home_teacher_id FROM students s JOIN student_sessions ss ON ss.student_id=s.id WHERE s.id=$1 AND ss.batch_date=$2',
      [sid, today]);
    if (!sv.rows[0]) return res.status(404).json({ error: 'Session not found' });
    const student = sv.rows[0];

    // Confirm checkout
    await db.query(`
      UPDATE student_sessions
      SET status='CHECKED_OUT', exit_zone_detected_at=NOW(), checkout_confirmed_at=NOW()
      WHERE student_id=$1 AND batch_date=$2
    `, [sid, today]);

    // Remove custody
    await db.query('DELETE FROM student_custody WHERE student_id=$1', [sid]);

    // Log events
    await logEvent('STUDENT_CHECKED_OUT', {
      title: `${student.first_name} ${student.last_name} checked out — EXIT zone confirmed`,
      detail: { student_name: `${student.first_name} ${student.last_name}`,
                confirmed_via: 'BLE EXIT zone detection' },
      studentIds: [sid], actorId: null,
      zoneId: req.body.zone_id||null,
    });

    console.log(`✅ Checkout confirmed: ${student.first_name} ${student.last_name}`);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/session/incoming — incoming transfer requests for this teacher
router.get('/incoming', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT ct.transfer_group, ct.to_zone_id,
        fu.full_name as from_teacher_name, fu.username as from_teacher_username,
        z.name as to_zone_name,
        ct.initiated_at, ct.notes,
        EXTRACT(EPOCH FROM (ct.expires_at-NOW()))::int as seconds_remaining,
        JSON_AGG(JSON_BUILD_OBJECT(
          'id',s.id,'first_name',s.first_name,'last_name',s.last_name
        )) as students
      FROM custody_transfers ct
      JOIN students s  ON s.id=ct.student_id
      JOIN users fu    ON fu.id=ct.from_teacher_id
      JOIN zones z     ON z.id=ct.to_zone_id
      WHERE ct.to_teacher_id=$1 AND ct.status='PENDING' AND ct.expires_at>NOW()
      GROUP BY ct.transfer_group,ct.to_zone_id,fu.full_name,fu.username,
               z.name,ct.initiated_at,ct.notes,ct.expires_at
      ORDER BY ct.initiated_at
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/session/my-alerts — open alerts for my students
router.get('/my-alerts', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT de.id, de.event_type, de.severity, de.title, de.detail,
        de.requires_ack, de.acked_at, de.created_at,
        COALESCE((
          SELECT JSON_AGG(JSON_BUILD_OBJECT('id',s.id,'first_name',s.first_name,'last_name',s.last_name))
          FROM students s WHERE s.id=ANY(de.student_ids)
        ),'[]') as students
      FROM director_events de
      WHERE de.created_at >= CURRENT_DATE
        AND (
          de.severity IN ('CRITICAL','WARNING')
          OR de.event_type IN ('CUSTODY_TRANSFER_ACCEPTED','CUSTODY_TRANSFER_REJECTED',
                               'CUSTODY_TRANSFER_EXPIRED','STUDENT_CHECKED_IN','STUDENT_CHECKED_OUT')
        )
        AND EXISTS (
          SELECT 1 FROM student_sessions ss
          WHERE ss.home_teacher_id=$1
            AND ss.batch_date=CURRENT_DATE
            AND ss.student_id=ANY(de.student_ids)
        )
      ORDER BY de.created_at DESC
      LIMIT 30
    `, [req.user.id]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});


// GET /api/session/checkout-trail/:studentId
router.get('/checkout-trail/:studentId', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT ct.detected_at, ct.rssi, ct.zone_type,
        z.name as zone_name, bg.short_id as gateway_short_id
      FROM checkout_tracking ct
      LEFT JOIN zones z  ON z.id=ct.detected_zone_id
      LEFT JOIN ble_gateways bg ON bg.id=ct.detected_gateway_id
      WHERE ct.student_id=$1
        AND ct.detected_at >= NOW() - INTERVAL '24 hours'
      ORDER BY ct.detected_at ASC
    `, [req.params.studentId]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});


// POST /api/session/mark-absent/:studentId
router.post('/mark-absent/:studentId', async (req, res) => {
  try {
    const sid       = req.params.studentId;
    const teacherId = req.user.id;
    const today     = new Date().toISOString().split('T')[0];

    const sv = await db.query(
      'SELECT s.first_name, s.last_name FROM students s WHERE s.id=$1', [sid]);
    if (!sv.rows[0]) return res.status(404).json({ error: 'Student not found' });
    const name = sv.rows[0].first_name + ' ' + sv.rows[0].last_name;

    await db.query(`
      INSERT INTO student_sessions
        (student_id, home_teacher_id, batch_date, status)
      VALUES ($1,$2,$3,'ABSENT')
      ON CONFLICT (student_id, batch_date)
      DO UPDATE SET status='ABSENT'
    `, [sid, teacherId, today]);

    await logEvent('STUDENT_ABSENT', {
      title: `${name} marked absent by ${req.user.username}`,
      detail: { student: name, marked_by: req.user.username },
      studentIds: [sid], actorId: teacherId,
    }).catch(()=>{});

    console.log(`⚫ ${name} marked absent by ${req.user.username}`);
    res.json({ success: true, status: 'ABSENT' });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/session/undo-absent/:studentId
router.post('/undo-absent/:studentId', async (req, res) => {
  try {
    const sid   = req.params.studentId;
    const today = new Date().toISOString().split('T')[0];

    await db.query(`
      UPDATE student_sessions SET status='EXPECTED'
      WHERE student_id=$1 AND batch_date=$2 AND status='ABSENT'
    `, [sid, today]);

    res.json({ success: true, status: 'EXPECTED' });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
