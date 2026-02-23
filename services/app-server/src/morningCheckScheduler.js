'use strict';
const db        = require('./db');
const { logEvent } = require('./eventLogger');

let schedulerStarted = false;

async function checkMorningAttendance() {
  try {
    const now   = new Date();
    const hour  = now.getHours();
    const today = now.toISOString().split('T')[0];

    // Only run between 7am and 10am
    if (hour < 7 || hour >= 10) return;

    // Get session start time from settings
    const setting = await db.query(
      "SELECT value FROM school_settings WHERE key='session_start_hour'");
    const startHour = parseInt(setting.rows[0]?.value || '7');

    // Minutes since session start
    const sessionStart = new Date(now);
    sessionStart.setHours(startHour, 0, 0, 0);
    const minutesSinceStart = Math.floor((now - sessionStart) / 60000);

    if (minutesSinceStart < 0) return; // Session not started yet

    // Get all teachers with their pending students
    const r = await db.query(`
      SELECT
        u.id as teacher_id,
        u.full_name as teacher_name,
        u.username,
        COUNT(s.id)::int as total_students,
        COUNT(s.id) FILTER (
          WHERE ss.status='ACCEPTED'
        )::int as accepted,
        COUNT(s.id) FILTER (
          WHERE ss.status IS NULL OR ss.status='EXPECTED'
        )::int as pending,
        ARRAY_AGG(s.first_name||' '||s.last_name
          ORDER BY s.last_name) FILTER (
          WHERE ss.status IS NULL OR ss.status='EXPECTED'
        ) as pending_students
      FROM users u
      JOIN students s ON s.teacher_id=u.id AND s.is_active=true
      LEFT JOIN student_sessions ss
        ON ss.student_id=s.id AND ss.batch_date=$1
      WHERE u.role='TEACHER' AND u.is_active=true
      GROUP BY u.id, u.full_name, u.username
      HAVING COUNT(s.id) > 0
    `, [today]);

    const teachersWithPending = r.rows.filter(t => t.pending > 0);

    if (teachersWithPending.length === 0) {
      console.log(`✅ [MorningCheck] All students checked in`);
      return;
    }

    // Determine severity based on time
    const severity = minutesSinceStart >= 60 ? 'CRITICAL'
                   : minutesSinceStart >= 30 ? 'WARNING'
                   : 'INFO';

    const reminderNum = Math.floor(minutesSinceStart / 15) + 1;

    console.log(`⏰ [MorningCheck] ${teachersWithPending.length} teachers have pending check-ins (${minutesSinceStart}min since start, severity: ${severity})`);

    // Alert each teacher with pending students
    for (const teacher of teachersWithPending) {
      const names = teacher.pending_students?.join(', ') || '';

      await logEvent('STUDENT_MISSING', {
        title: `⏰ Reminder #${reminderNum}: ${teacher.pending} student${teacher.pending>1?'s':''} not yet checked in`,
        detail: {
          teacher: teacher.teacher_name,
          pending_count: teacher.pending,
          accepted_count: teacher.accepted,
          total: teacher.total_students,
          pending_students: names,
          minutes_since_start: minutesSinceStart,
          reminder_number: reminderNum,
        },
        studentIds: [],
        actorId: teacher.teacher_id,
        zoneId: null,
      }).catch(()=>{});
    }

    // Director summary alert
    const totalPending = teachersWithPending.reduce((s,t)=>s+t.pending, 0);
    const summary = teachersWithPending
      .map(t=>`${t.teacher_name}: ${t.pending} pending`)
      .join(' | ');

    await logEvent('STUDENT_MISSING', {
      title: `⏰ Morning check-in reminder: ${totalPending} student${totalPending>1?'s':''} not yet accepted`,
      detail: {
        reminder_number: reminderNum,
        minutes_since_start: minutesSinceStart,
        severity,
        teachers_with_pending: teachersWithPending.length,
        summary,
        breakdown: teachersWithPending.map(t=>({
          teacher: t.teacher_name,
          pending: t.pending,
          accepted: t.accepted,
          students: t.pending_students,
        })),
      },
      studentIds: [],
      actorId: null,
    }).catch(()=>{});

    // Broadcast to director if CRITICAL
    if (severity === 'CRITICAL' && global.broadcastDirectorEvent) {
      global.broadcastDirectorEvent(JSON.stringify({
        type: 'MORNING_CHECKIN_CRITICAL',
        totalPending,
        minutesSinceStart,
        teachers: teachersWithPending.map(t=>({
          name: t.teacher_name,
          pending: t.pending,
        })),
      }));
    }

  } catch(e) {
    console.error('[MorningCheckScheduler] Error:', e.message);
  }
}

// Also seed today's sessions if not done yet
async function seedTodaySessions() {
  try {
    const today = new Date().toISOString().split('T')[0];

    // Check if already seeded
    const existing = await db.query(
      'SELECT COUNT(*) FROM student_sessions WHERE batch_date=$1', [today]);
    if (parseInt(existing.rows[0].count) > 0) return;

    // Seed EXPECTED sessions for all active students
    const students = await db.query(`
      SELECT s.id, s.teacher_id
      FROM students s
      WHERE s.is_active=true AND s.teacher_id IS NOT NULL
    `);

    if (students.rows.length === 0) return;

    for (const s of students.rows) {
      await db.query(`
        INSERT INTO student_sessions
          (student_id, home_teacher_id, batch_date, status)
        VALUES ($1, $2, $3, 'EXPECTED')
        ON CONFLICT (student_id, batch_date) DO NOTHING
      `, [s.id, s.teacher_id, today]);
    }

    console.log(`📋 [MorningCheck] Seeded ${students.rows.length} sessions for ${today}`);
  } catch(e) {
    console.error('[seedTodaySessions] Error:', e.message);
  }
}

function startMorningScheduler() {
  if (schedulerStarted) return;
  schedulerStarted = true;

  console.log('⏰ Morning check-in scheduler started');

  // Seed sessions at startup
  seedTodaySessions();

  // Re-seed at 7am every day
  setInterval(() => {
    const h = new Date().getHours();
    const m = new Date().getMinutes();
    if (h === 7 && m === 0) seedTodaySessions();
  }, 60000);

  // Check every 15 minutes
  setInterval(checkMorningAttendance, 15 * 60 * 1000);

  // Also run immediately if between 7-10am
  const h = new Date().getHours();
  if (h >= 7 && h < 10) {
    setTimeout(checkMorningAttendance, 5000); // 5s after startup
  }

  console.log('⏰ Reminder schedule: every 15min between 7am-10am');
}

module.exports = { startMorningScheduler, seedTodaySessions, checkMorningAttendance };
