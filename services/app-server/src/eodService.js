'use strict';
const db = require('./db');
const { logEvent } = require('./eventLogger');

let eodScheduled = false;

async function runEOD(trigger = 'SCHEDULED') {
  try {
    const today = new Date().toISOString().split('T')[0];
    console.log(`\n🌙 EOD starting (trigger: ${trigger}) for ${today}`);

    // STEP 1: Force-close any still-open class sessions
    const openSessions = await db.query(`
      SELECT cs.*, u.full_name, u.username
      FROM class_sessions cs JOIN users u ON u.id=cs.teacher_id
      WHERE cs.session_date=$1 AND cs.status='OPEN'
    `, [today]);

    for (const s of openSessions.rows) {
      const counts = await db.query(`
        SELECT
          COUNT(*) FILTER (WHERE status='ACCEPTED')::int    as present,
          COUNT(*) FILTER (WHERE status='ABSENT')::int      as absent,
          COUNT(*) FILTER (WHERE status='CHECKED_OUT')::int as checked_out,
          COUNT(*) FILTER (WHERE status='EXPECTED')::int    as no_show
        FROM student_sessions
        WHERE home_teacher_id=$1 AND batch_date=$2
      `, [s.teacher_id, today]);
      const c = counts.rows[0];

      await db.query(`
        UPDATE class_sessions SET
          status='CLOSED', closed_at=NOW(), closed_early=false,
          present_count=$3, absent_count=$4,
          checked_out_count=$5, no_show_count=$6
        WHERE teacher_id=$1 AND session_date=$2
      `, [s.teacher_id, today, c.present, c.absent, c.checked_out, c.no_show]);
      console.log(`  🔒 Force-closed: ${s.full_name}`);
    }

    // STEP 2: Reconcile open student sessions
    // ACCEPTED but not checked out → PRESENT_EOD
    await db.query(`
      UPDATE student_sessions SET status='PRESENT_EOD'
      WHERE batch_date=$1 AND status='ACCEPTED'
    `, [today]);

    // EXPECTED never accepted → NO_SHOW
    await db.query(`
      UPDATE student_sessions SET status='NO_SHOW'
      WHERE batch_date=$1 AND status='EXPECTED'
    `, [today]);

    // STEP 3: Snapshot to attendance_archive
    const sessions = await db.query(`
      SELECT
        ss.student_id, ss.home_teacher_id, ss.status,
        ss.accepted_at as first_accepted_at,
        ss.checkout_confirmed_at as checked_out_at,
        CASE WHEN ss.accepted_at IS NOT NULL AND ss.checkout_confirmed_at IS NOT NULL
          THEN EXTRACT(EPOCH FROM (ss.checkout_confirmed_at - ss.accepted_at))/60
          WHEN ss.accepted_at IS NOT NULL
          THEN EXTRACT(EPOCH FROM (NOW() - ss.accepted_at))/60
          ELSE 0 END::int as total_minutes,
        s.first_name || ' ' || s.last_name as student_name,
        s.grade as student_grade,
        u.full_name as teacher_name,
        t.mac_address as tag_mac,
        cs.id as class_session_id
      FROM student_sessions ss
      JOIN students s ON s.id=ss.student_id
      JOIN users u ON u.id=ss.home_teacher_id
      LEFT JOIN ble_tags t ON t.student_id=ss.student_id AND t.is_active=true
      LEFT JOIN class_sessions cs ON cs.teacher_id=ss.home_teacher_id
        AND cs.session_date=ss.batch_date
      WHERE ss.batch_date=$1
    `, [today]);

    let archived = 0;
    for (const s of sessions.rows) {
      // Map session status to archive status
      const archiveStatus =
        s.status === 'PRESENT_EOD'  ? 'PRESENT'      :
        s.status === 'CHECKED_OUT'  ? 'CHECKED_OUT'  :
        s.status === 'ABSENT'       ? 'ABSENT'       :
        s.status === 'NO_SHOW'      ? 'NO_SHOW'      : 'PRESENT';

      await db.query(`
        INSERT INTO attendance_archive (
          archive_date, student_id, student_name, student_grade,
          teacher_id, teacher_name, status,
          first_accepted_at, checked_out_at, total_minutes,
          tag_mac, class_session_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT DO NOTHING
      `, [today, s.student_id, s.student_name, s.student_grade,
          s.home_teacher_id, s.teacher_name, archiveStatus,
          s.first_accepted_at, s.checked_out_at, s.total_minutes,
          s.tag_mac, s.class_session_id]);
      archived++;
    }
    console.log(`  📦 Archived ${archived} attendance records`);

    // STEP 4: Summary counts
    const summary = await db.query(`
      SELECT
        COUNT(*) FILTER (WHERE status='PRESENT')::int    as present,
        COUNT(*) FILTER (WHERE status='ABSENT')::int     as absent,
        COUNT(*) FILTER (WHERE status='CHECKED_OUT')::int as checked_out,
        COUNT(*) FILTER (WHERE status='NO_SHOW')::int    as no_show
      FROM attendance_archive WHERE archive_date=$1
    `, [today]);
    const s = summary.rows[0];

    // STEP 5: Reset hot tables
    await db.query('DELETE FROM student_custody');
    await db.query('DELETE FROM presence_states');
    console.log('  🧹 Hot tables cleared (custody + presence)');

    // STEP 6: Log EOD event
    await logEvent('EOD_RECONCILIATION', {
      title: `End of day complete — ${today}`,
      detail: { trigger, archived,
                present: s.present, absent: s.absent,
                checked_out: s.checked_out, no_show: s.no_show,
                completed_at: new Date().toISOString() },
      actorId: null,
    }).catch(() => {});

    // STEP 7: Broadcast to all connected clients
    if (global.broadcastDirectorEvent) {
      global.broadcastDirectorEvent(JSON.stringify({
        type: 'EOD_COMPLETE', date: today,
        summary: { present: s.present, absent: s.absent,
                   checked_out: s.checked_out, no_show: s.no_show },
      }));
    }

    // STEP 8: Generate EOD report if enabled
    const rptEnabled = await db.query(
      "SELECT value FROM school_settings WHERE key='eod_report_enabled'");
    if (rptEnabled.rows[0]?.value === 'true') {
      await generateEODReport(today, s);
    }

    console.log(`  ✅ EOD complete: ${s.present} present, ${s.absent} absent, ${s.checked_out} checked_out, ${s.no_show} no_show\n`);
    return { success: true, summary: s, archived };

  } catch(e) {
    console.error('❌ EOD failed:', e.message);
    await logEvent('SYSTEM_HEALTH_CRIT', {
      title: 'EOD reconciliation failed',
      detail: { error: e.message, trigger },
    }).catch(() => {});
    return { success: false, error: e.message };
  }
}

async function generateEODReport(date, summary) {
  try {
    const detail = await db.query(`
      SELECT student_name, teacher_name, status, total_minutes,
             first_accepted_at, checked_out_at
      FROM attendance_archive
      WHERE archive_date=$1
      ORDER BY teacher_name, student_name
    `, [date]);

    await logEvent('REPORT_GENERATED', {
      title: `Daily attendance report — ${date}`,
      detail: { date, summary, rows: detail.rows.length,
                report_type: 'DAILY_EOD' },
    }).catch(() => {});
    console.log(`  📊 EOD report generated for ${date}`);
  } catch(e) {
    console.error('Report generation failed:', e.message);
  }
}

function startEODScheduler() {
  if (eodScheduled) return;
  eodScheduled = true;

  // Check every minute
  setInterval(async () => {
    try {
      const now   = new Date();
      const h     = now.getHours();
      const m     = now.getMinutes();
      const today = now.toISOString().split('T')[0];

      // Get session end hour + grace
      const cfg = await db.query(`
        SELECT key,value FROM school_settings
        WHERE key IN ('session_end_hour','eod_grace_minutes','session_days')
      `);
      const settings = {};
      cfg.rows.forEach(r => settings[r.key] = r.value);

      const endHour   = parseInt(settings.session_end_hour  || '19');
      const graceMins = parseInt(settings.eod_grace_minutes || '15');

      // Trigger EOD at endHour:graceMins exactly
      const triggerMin = graceMins;
      if (h === endHour && m === triggerMin) {
        // Check not already run today
        const alreadyRun = await db.query(`
          SELECT 1 FROM director_events
          WHERE title LIKE 'End of day complete%'
            AND created_at::date='${today}'::date
          LIMIT 1
        `);
        if (!alreadyRun.rows[0]) {
          console.log(`⏰ EOD trigger: ${h}:${m.toString().padStart(2,'0')}`);
          await runEOD('SCHEDULED');
        }
      }

      // Check for un-opened classes 15min after session start
      const startHour = 7;
      const openGrace = parseInt(settings.class_open_grace_minutes || '15');
      if (h === startHour && m === openGrace) {
        await checkUnopenedClasses(today);
      }

    } catch(e) {
      console.error('[EODScheduler] Error:', e.message);
    }
  }, 60000); // every 1 minute

  console.log('🌙 EOD scheduler started');
}

async function checkUnopenedClasses(today) {
  try {
    const unopened = await db.query(`
      SELECT u.id, u.full_name, u.username
      FROM users u
      WHERE u.role='TEACHER' AND u.is_active=true
        AND NOT EXISTS (
          SELECT 1 FROM class_sessions cs
          WHERE cs.teacher_id=u.id AND cs.session_date=$1
            AND cs.status IN ('OPEN','CLOSED'))
    `, [today]);

    for (const t of unopened.rows) {
      await logEvent('CLASS_NOT_OPENED', {
        title: `⚠️ ${t.full_name || t.username} has not opened class`,
        detail: { teacher: t.full_name || t.username, date: today },
        actorId: t.id,
      }).catch(() => {});
      console.log(`⚠️ Class not opened: ${t.full_name}`);
    }
  } catch(e) {
    console.error('[checkUnopenedClasses] Error:', e.message);
  }
}

module.exports = { runEOD, startEODScheduler };
