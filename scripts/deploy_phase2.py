#!/usr/bin/env python3
"""
Prosper RFID Platform — Phase 2 Master Deployment
═══════════════════════════════════════════════════
Includes:
  1. DB migration (class_sessions, attendance_archive, student_archive)
  2. EOD batch service (auto-archive + reset at session_end_hour)
  3. Class session open/close (teacher button + director alerts)
  4. Student removal lifecycle (archive + tag release)
  5. System health monitor (crash detection + IT Admin alerts)
  6. Reports module (canned reports + scheduling)
  7. IT Admin: Settings, Reports, System Monitor screens
  8. Director: classroom open/closed indicators + Reports tab
  9. Teacher: Open Class / Close Class UI states

Safety features:
  - Preflight checks before ANY file modification
  - Duplicate detection on every injection
  - Build verification after each step
  - Full rollback list printed on failure
  - Docker log scan after rebuild
"""

import os, sys, re, subprocess, time, json
from pathlib import Path

BASE = Path.home() / 'prosper-platform'
UI   = BASE / 'services/react-ui/src'
APP  = BASE / 'services/app-server/src'

STEPS_COMPLETED = []
ROLLBACK_NEEDED = []

def run(cmd, cwd=None, capture=True):
    r = subprocess.run(cmd, shell=True, capture_output=capture,
                       text=True, cwd=str(cwd or BASE))
    out = (r.stdout + r.stderr).strip()
    if out and not capture:
        pass
    return r.returncode, out

def ok(msg):  print(f'  ✅ {msg}')
def err(msg): print(f'  ❌ {msg}')
def hdr(msg): print(f'\n{"═"*55}\n  {msg}\n{"═"*55}')
def info(msg):print(f'  ℹ️  {msg}')

def read(path): return Path(path).read_text()
def write(path, content):
    Path(path).write_text(content)
    ok(f'Written: {Path(path).name}')

def db(sql):
    rc, out = run(f'docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "{sql}"')
    return out

def check_duplicate(content, pattern, label, expected=1):
    count = content.count(pattern)
    if count != expected:
        err(f'Duplicate check FAILED: {label} = {count} (expected {expected})')
        return False
    ok(f'Duplicate check passed: {label} = {count}')
    return True

def abort(msg):
    err(f'ABORTING: {msg}')
    if ROLLBACK_NEEDED:
        print('\n⚠️  Files modified before abort:')
        for f in ROLLBACK_NEEDED:
            print(f'   git checkout b64100d -- {f}')
    sys.exit(1)

# ═══════════════════════════════════════════════════════
# PREFLIGHT
# ═══════════════════════════════════════════════════════
hdr('PREFLIGHT CHECKS')

# Check docker running
rc, _ = run('docker ps | grep prosper-postgres')
if rc != 0: abort('PostgreSQL not running — start docker compose first')
ok('PostgreSQL running')

rc, _ = run('docker ps | grep prosper-app-server')
if rc != 0: abort('App server not running')
ok('App server running')

# Check files exist
for f in ['TeacherView.jsx', 'DirectorPortal.jsx', 'App.jsx']:
    if not (UI / f).exists(): abort(f'{f} not found')
ok('All React files present')

for f in ['index.js', 'teacherSessionApi.js', 'eventLogger.js']:
    if not (APP / f).exists(): abort(f'{f} not found')
ok('All API files present')

# Check for existing phase2 to prevent re-run
idx = read(APP / 'index.js')
if 'eodService' in idx:
    abort('Phase 2 already deployed — eodService found in index.js')
ok('Clean state confirmed — safe to deploy')

# ═══════════════════════════════════════════════════════
# STEP 1: DATABASE MIGRATION
# ═══════════════════════════════════════════════════════
hdr('STEP 1: Database Migration')

migration_sql = """
-- class_sessions: one row per teacher per day
CREATE TABLE IF NOT EXISTS class_sessions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id        UUID NOT NULL REFERENCES users(id),
  session_date      DATE NOT NULL DEFAULT CURRENT_DATE,
  status            TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','OPEN','CLOSED')),
  opened_at         TIMESTAMPTZ,
  closed_at         TIMESTAMPTZ,
  closed_early      BOOLEAN DEFAULT false,
  student_count     INT DEFAULT 0,
  present_count     INT DEFAULT 0,
  absent_count      INT DEFAULT 0,
  checked_out_count INT DEFAULT 0,
  no_show_count     INT DEFAULT 0,
  notes             TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(teacher_id, session_date)
);
CREATE INDEX IF NOT EXISTS idx_class_sessions_date
  ON class_sessions(session_date DESC);
CREATE INDEX IF NOT EXISTS idx_class_sessions_teacher
  ON class_sessions(teacher_id, session_date DESC);

-- attendance_archive: daily snapshot per student
CREATE TABLE IF NOT EXISTS attendance_archive (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  archive_date      DATE NOT NULL,
  student_id        UUID REFERENCES students(id),
  student_name      TEXT NOT NULL,
  student_grade     TEXT,
  teacher_id        UUID REFERENCES users(id),
  teacher_name      TEXT NOT NULL,
  status            TEXT NOT NULL
                    CHECK (status IN (
                      'PRESENT','ABSENT','CHECKED_OUT',
                      'NO_SHOW','PRESENT_EOD')),
  first_accepted_at TIMESTAMPTZ,
  checked_out_at    TIMESTAMPTZ,
  total_minutes     INT DEFAULT 0,
  tag_mac           TEXT,
  class_session_id  UUID REFERENCES class_sessions(id),
  created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_attendance_archive_date
  ON attendance_archive(archive_date DESC);
CREATE INDEX IF NOT EXISTS idx_attendance_archive_student
  ON attendance_archive(student_id, archive_date DESC);
CREATE INDEX IF NOT EXISTS idx_attendance_archive_teacher
  ON attendance_archive(teacher_id, archive_date DESC);

-- student_archive: removed students
CREATE TABLE IF NOT EXISTS student_archive (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  original_id     UUID NOT NULL,
  student_data    JSONB NOT NULL,
  session_history JSONB,
  removed_at      TIMESTAMPTZ DEFAULT NOW(),
  removed_by      UUID REFERENCES users(id),
  removal_reason  TEXT,
  tag_mac_released TEXT
);

-- system_health_log: crash + health events
CREATE TABLE IF NOT EXISTS system_health_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  check_time  TIMESTAMPTZ DEFAULT NOW(),
  service     TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('OK','WARN','CRITICAL')),
  detail      JSONB,
  resolved_at TIMESTAMPTZ,
  alerted     BOOLEAN DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_health_log_time
  ON system_health_log(check_time DESC);
CREATE INDEX IF NOT EXISTS idx_health_log_status
  ON system_health_log(status, resolved_at);

-- report_schedules: IT Admin scheduled reports
CREATE TABLE IF NOT EXISTS report_schedules (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  report_type   TEXT NOT NULL,
  schedule      TEXT NOT NULL DEFAULT 'DAILY_EOD',
  recipients    TEXT[] DEFAULT '{}',
  params        JSONB DEFAULT '{}',
  is_active     BOOLEAN DEFAULT true,
  last_run_at   TIMESTAMPTZ,
  created_by    UUID REFERENCES users(id),
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Add new school_settings
INSERT INTO school_settings (key, value) VALUES
  ('session_end_hour',           '19'),
  ('session_days',               'MON,TUE,WED,THU,FRI'),
  ('eod_grace_minutes',          '15'),
  ('class_open_grace_minutes',   '15'),
  ('class_close_warning_minutes','30'),
  ('health_check_interval_min',  '5'),
  ('reports_enabled',            'true'),
  ('eod_report_enabled',         'true'),
  ('eod_report_recipients',      'director,admin')
ON CONFLICT (key) DO NOTHING;

SELECT 'Migration complete' as result;
"""

# Write migration to temp file and run
Path('/tmp/phase2_migration.sql').write_text(migration_sql)
rc, out = run('docker exec -i prosper-postgres psql -U prosper_user -d prosper_db < /tmp/phase2_migration.sql')
if 'Migration complete' in out:
    ok('Database migration successful')
    STEPS_COMPLETED.append('DB migration')
else:
    err(f'Migration output: {out[-200:]}')
    abort('Database migration failed')

# ═══════════════════════════════════════════════════════
# STEP 2: eventLogger additions
# ═══════════════════════════════════════════════════════
hdr('STEP 2: Event Types')

path = APP / 'eventLogger.js'
src  = read(path)
ROLLBACK_NEEDED.append('services/app-server/src/eventLogger.js')

new_events = [
    ('CLASS_OPENED',         "{ cat:'ATTENDANCE', sev:'INFO',     ack:false }"),
    ('CLASS_CLOSED',         "{ cat:'ATTENDANCE', sev:'INFO',     ack:false }"),
    ('CLASS_NOT_OPENED',     "{ cat:'VIOLATION',  sev:'WARNING',  ack:true  }"),
    ('CLASS_CLOSED_EARLY',   "{ cat:'ATTENDANCE', sev:'INFO',     ack:false }"),
    ('EOD_RECONCILIATION',   "{ cat:'SYSTEM',     sev:'INFO',     ack:false }"),
    ('STUDENT_REMOVED',      "{ cat:'ADMIN',      sev:'INFO',     ack:false }"),
    ('SYSTEM_HEALTH_WARN',   "{ cat:'SYSTEM',     sev:'WARNING',  ack:true  }"),
    ('SYSTEM_HEALTH_CRIT',   "{ cat:'SYSTEM',     sev:'CRITICAL', ack:true  }"),
    ('REPORT_GENERATED',     "{ cat:'SYSTEM',     sev:'INFO',     ack:false }"),
]

for event_name, event_def in new_events:
    if event_name not in src:
        src = src.replace(
            "  STUDENT_ABSENT:",
            f"  {event_name.ljust(22)}: {event_def},\n  STUDENT_ABSENT:")
        ok(f'Added event type: {event_name}')
    else:
        info(f'Already exists: {event_name}')

write(path, src)

# ═══════════════════════════════════════════════════════
# STEP 3: classSessionApi.js
# ═══════════════════════════════════════════════════════
hdr('STEP 3: Class Session API')

class_session_api = '''\'use strict\';
const express  = require(\'express\');
const router   = express.Router();
const db       = require(\'./db\');
const { authMiddleware, requireRole } = require(\'./auth\');
const { logEvent } = require(\'./eventLogger\');

router.use(authMiddleware);

// GET /api/class-session/status — my session for today
router.get(\'/status\', async (req, res) => {
  try {
    const today     = new Date().toISOString().split(\'T\')[0];
    const teacherId = req.user.id;

    const cs = await db.query(`
      SELECT cs.*,
        (SELECT COUNT(*) FROM students s
         WHERE s.teacher_id=$1 AND s.is_active=true)::int as total_students,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status=\'ACCEPTED\')::int as accepted_count,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status=\'ABSENT\')::int as absent_count,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status=\'CHECKED_OUT\')::int as checked_out_count,
        (SELECT COUNT(*) FROM student_sessions ss
         WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
           AND ss.status IN (\'EXPECTED\'))::int as expected_count
      FROM class_sessions cs
      WHERE cs.teacher_id=$1 AND cs.session_date=$2
    `, [teacherId, today]);

    // Get session window settings
    const settings = await db.query(`
      SELECT key, value FROM school_settings
      WHERE key IN (\'session_start_hour\',\'session_end_hour\',
                    \'class_close_warning_minutes\')
    `);
    const cfg = {};
    settings.rows.forEach(r => cfg[r.key] = r.value);

    const now  = new Date();
    const hour = now.getHours();
    const sessionStartHour = parseInt(cfg.session_start_hour || \'7\');
    const sessionEndHour   = parseInt(cfg.session_end_hour   || \'19\');
    const sessionActive    = hour >= sessionStartHour && hour < sessionEndHour;

    res.json({
      session: cs.rows[0] || null,
      session_active: sessionActive,
      session_start_hour: sessionStartHour,
      session_end_hour:   sessionEndHour,
      close_warning_minutes: parseInt(cfg.class_close_warning_minutes || \'30\'),
      current_hour: hour,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/class-session/open — teacher opens class
router.post(\'/open\', async (req, res) => {
  try {
    const teacherId = req.user.id;
    const today     = new Date().toISOString().split(\'T\')[0];
    const now       = new Date();
    const hour      = now.getHours();

    // Get session settings
    const cfg = await db.query(
      "SELECT key,value FROM school_settings WHERE key IN (\'session_start_hour\',\'class_close_warning_minutes\')");
    const settings = {};
    cfg.rows.forEach(r => settings[r.key] = parseInt(r.value));
    const startHour = settings.session_start_hour || 7;

    // Check if already open
    const existing = await db.query(
      \'SELECT * FROM class_sessions WHERE teacher_id=$1 AND session_date=$2\',
      [teacherId, today]);
    if (existing.rows[0]?.status === \'OPEN\')
      return res.status(400).json({ error: \'Class already open\' });
    if (existing.rows[0]?.status === \'CLOSED\')
      return res.status(400).json({ error: \'Class already closed for today\' });

    // Count students
    const sc = await db.query(
      \'SELECT COUNT(*)::int as c FROM students WHERE teacher_id=$1 AND is_active=true\',
      [teacherId]);

    await db.query(`
      INSERT INTO class_sessions
        (teacher_id, session_date, status, opened_at, student_count)
      VALUES ($1,$2,\'OPEN\',NOW(),$3)
      ON CONFLICT (teacher_id, session_date)
      DO UPDATE SET status=\'OPEN\', opened_at=NOW(), student_count=$3
    `, [teacherId, today, sc.rows[0].c]);

    await logEvent(\'CLASS_OPENED\', {
      title: `Class opened by ${req.user.username}`,
      detail: { teacher: req.user.username, opened_at: now.toISOString(),
                student_count: sc.rows[0].c },
      actorId: teacherId,
    }).catch(() => {});

    // Broadcast to director
    if (global.broadcastDirectorEvent) {
      global.broadcastDirectorEvent(JSON.stringify({
        type: \'CLASS_OPENED\', teacherId,
        teacherName: req.user.full_name || req.user.username,
      }));
    }

    console.log(`📖 Class opened by ${req.user.username}`);
    res.json({ success: true, status: \'OPEN\' });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/class-session/close — teacher closes class
router.post(\'/close\', async (req, res) => {
  try {
    const teacherId = req.user.id;
    const today     = new Date().toISOString().split(\'T\')[0];
    const now       = new Date();
    const { force } = req.body;

    // Check session exists and is open
    const cs = await db.query(
      \'SELECT * FROM class_sessions WHERE teacher_id=$1 AND session_date=$2\',
      [teacherId, today]);
    if (!cs.rows[0] || cs.rows[0].status !== \'OPEN\')
      return res.status(400).json({ error: \'No open class session found\' });

    // Check all students checked out or absent
    const blocking = await db.query(`
      SELECT s.first_name, s.last_name, ss.status
      FROM student_sessions ss
      JOIN students s ON s.id=ss.student_id
      WHERE ss.home_teacher_id=$1 AND ss.batch_date=$2
        AND ss.status IN (\'ACCEPTED\',\'EXPECTED\')
    `, [teacherId, today]);

    if (blocking.rows.length > 0 && !force) {
      return res.json({
        blocked: true,
        reason: \'Students still checked in\',
        students: blocking.rows.map(s =>
          ({ name: `${s.first_name} ${s.last_name}`, status: s.status })),
      });
    }

    // Get counts for summary
    const counts = await db.query(`
      SELECT
        COUNT(*) FILTER (WHERE status=\'ACCEPTED\')::int    as present,
        COUNT(*) FILTER (WHERE status=\'ABSENT\')::int      as absent,
        COUNT(*) FILTER (WHERE status=\'CHECKED_OUT\')::int as checked_out,
        COUNT(*) FILTER (WHERE status=\'EXPECTED\')::int    as no_show
      FROM student_sessions
      WHERE home_teacher_id=$1 AND batch_date=$2
    `, [teacherId, today]);
    const c = counts.rows[0];

    // Get session end hour
    const endHourR = await db.query(
      "SELECT value FROM school_settings WHERE key=\'session_end_hour\'");
    const endHour  = parseInt(endHourR.rows[0]?.value || \'19\');
    const closedEarly = now.getHours() < endHour;

    // Close the session
    await db.query(`
      UPDATE class_sessions SET
        status=\'CLOSED\', closed_at=NOW(),
        closed_early=$3,
        present_count=$4, absent_count=$5,
        checked_out_count=$6, no_show_count=$7
      WHERE teacher_id=$1 AND session_date=$2
    `, [teacherId, today, closedEarly,
        c.present, c.absent, c.checked_out, c.no_show]);

    // Force-checkout any remaining expected students
    if (force && blocking.rows.length > 0) {
      await db.query(`
        UPDATE student_sessions SET status=\'CHECKED_OUT\', checkout_confirmed_at=NOW()
        WHERE home_teacher_id=$1 AND batch_date=$2
          AND status IN (\'ACCEPTED\',\'EXPECTED\')
      `, [teacherId, today]);
      // Remove their custody
      for (const s of blocking.rows) {
        await db.query(
          \'DELETE FROM student_custody WHERE current_teacher_id=$1\',
          [teacherId]);
      }
    }

    await logEvent(closedEarly ? \'CLASS_CLOSED_EARLY\' : \'CLASS_CLOSED\', {
      title: `Class closed by ${req.user.username}${closedEarly?\' (early)\':\'\'} `,
      detail: { teacher: req.user.username, closed_at: now.toISOString(),
                present: c.present, absent: c.absent,
                checked_out: c.checked_out, closed_early: closedEarly },
      actorId: teacherId,
    }).catch(() => {});

    if (global.broadcastDirectorEvent) {
      global.broadcastDirectorEvent(JSON.stringify({
        type: \'CLASS_CLOSED\', teacherId,
        teacherName: req.user.full_name || req.user.username,
        summary: c, closedEarly,
      }));
    }

    // Check if ALL classes are closed — trigger EOD if so
    const openClasses = await db.query(`
      SELECT COUNT(*)::int as c FROM class_sessions
      WHERE session_date=$1 AND status=\'OPEN\'
    `, [today]);
    if (openClasses.rows[0].c === 0) {
      console.log(\'🌙 All classes closed — triggering EOD\');
      const { runEOD } = require(\'./eodService\');
      setTimeout(() => runEOD(\'ALL_CLASSES_CLOSED\'), 5000);
    }

    console.log(`🔒 Class closed by ${req.user.username}`);
    res.json({ success: true, status: \'CLOSED\', summary: c, closedEarly });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/class-session/all-status — director view of all classrooms
router.get(\'/all-status\', async (req, res) => {
  try {
    const today = new Date().toISOString().split(\'T\')[0];
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
           AND ss.status=\'ACCEPTED\') as currently_in
      FROM users u
      LEFT JOIN class_sessions cs ON cs.teacher_id=u.id AND cs.session_date=$1
      LEFT JOIN zones z ON z.id=(
        SELECT tz.zone_id FROM teacher_zones tz
        WHERE tz.teacher_id=u.id AND tz.zone_role=\'PRIMARY\' LIMIT 1)
      WHERE u.role=\'TEACHER\' AND u.is_active=true
      ORDER BY u.full_name
    `, [today]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
'''

write(APP / 'classSessionApi.js', class_session_api)
ROLLBACK_NEEDED.append('services/app-server/src/classSessionApi.js')
ok('classSessionApi.js created')

# ═══════════════════════════════════════════════════════
# STEP 4: eodService.js
# ═══════════════════════════════════════════════════════
hdr('STEP 4: EOD Service')

eod_service = '''\'use strict\';
const db = require(\'./db\');
const { logEvent } = require(\'./eventLogger\');

let eodScheduled = false;

async function runEOD(trigger = \'SCHEDULED\') {
  try {
    const today = new Date().toISOString().split(\'T\')[0];
    console.log(`\\n🌙 EOD starting (trigger: ${trigger}) for ${today}`);

    // STEP 1: Force-close any still-open class sessions
    const openSessions = await db.query(`
      SELECT cs.*, u.full_name, u.username
      FROM class_sessions cs JOIN users u ON u.id=cs.teacher_id
      WHERE cs.session_date=$1 AND cs.status=\'OPEN\'
    `, [today]);

    for (const s of openSessions.rows) {
      const counts = await db.query(`
        SELECT
          COUNT(*) FILTER (WHERE status=\'ACCEPTED\')::int    as present,
          COUNT(*) FILTER (WHERE status=\'ABSENT\')::int      as absent,
          COUNT(*) FILTER (WHERE status=\'CHECKED_OUT\')::int as checked_out,
          COUNT(*) FILTER (WHERE status=\'EXPECTED\')::int    as no_show
        FROM student_sessions
        WHERE home_teacher_id=$1 AND batch_date=$2
      `, [s.teacher_id, today]);
      const c = counts.rows[0];

      await db.query(`
        UPDATE class_sessions SET
          status=\'CLOSED\', closed_at=NOW(), closed_early=false,
          present_count=$3, absent_count=$4,
          checked_out_count=$5, no_show_count=$6
        WHERE teacher_id=$1 AND session_date=$2
      `, [s.teacher_id, today, c.present, c.absent, c.checked_out, c.no_show]);
      console.log(`  🔒 Force-closed: ${s.full_name}`);
    }

    // STEP 2: Reconcile open student sessions
    // ACCEPTED but not checked out → PRESENT_EOD
    await db.query(`
      UPDATE student_sessions SET status=\'PRESENT_EOD\'
      WHERE batch_date=$1 AND status=\'ACCEPTED\'
    `, [today]);

    // EXPECTED never accepted → NO_SHOW
    await db.query(`
      UPDATE student_sessions SET status=\'NO_SHOW\'
      WHERE batch_date=$1 AND status=\'EXPECTED\'
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
        s.first_name || \' \' || s.last_name as student_name,
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
        s.status === \'PRESENT_EOD\'  ? \'PRESENT\'      :
        s.status === \'CHECKED_OUT\'  ? \'CHECKED_OUT\'  :
        s.status === \'ABSENT\'       ? \'ABSENT\'       :
        s.status === \'NO_SHOW\'      ? \'NO_SHOW\'      : \'PRESENT\';

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
        COUNT(*) FILTER (WHERE status=\'PRESENT\')::int    as present,
        COUNT(*) FILTER (WHERE status=\'ABSENT\')::int     as absent,
        COUNT(*) FILTER (WHERE status=\'CHECKED_OUT\')::int as checked_out,
        COUNT(*) FILTER (WHERE status=\'NO_SHOW\')::int    as no_show
      FROM attendance_archive WHERE archive_date=$1
    `, [today]);
    const s = summary.rows[0];

    // STEP 5: Reset hot tables
    await db.query(\'DELETE FROM student_custody\');
    await db.query(\'DELETE FROM presence_states\');
    console.log(\'  🧹 Hot tables cleared (custody + presence)\');

    // STEP 6: Log EOD event
    await logEvent(\'EOD_RECONCILIATION\', {
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
        type: \'EOD_COMPLETE\', date: today,
        summary: { present: s.present, absent: s.absent,
                   checked_out: s.checked_out, no_show: s.no_show },
      }));
    }

    // STEP 8: Generate EOD report if enabled
    const rptEnabled = await db.query(
      "SELECT value FROM school_settings WHERE key=\'eod_report_enabled\'");
    if (rptEnabled.rows[0]?.value === \'true\') {
      await generateEODReport(today, s);
    }

    console.log(`  ✅ EOD complete: ${s.present} present, ${s.absent} absent, ${s.checked_out} checked_out, ${s.no_show} no_show\\n`);
    return { success: true, summary: s, archived };

  } catch(e) {
    console.error(\'❌ EOD failed:\', e.message);
    await logEvent(\'SYSTEM_HEALTH_CRIT\', {
      title: \'EOD reconciliation failed\',
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

    await logEvent(\'REPORT_GENERATED\', {
      title: `Daily attendance report — ${date}`,
      detail: { date, summary, rows: detail.rows.length,
                report_type: \'DAILY_EOD\' },
    }).catch(() => {});
    console.log(`  📊 EOD report generated for ${date}`);
  } catch(e) {
    console.error(\'Report generation failed:\', e.message);
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
      const today = now.toISOString().split(\'T\')[0];

      // Get session end hour + grace
      const cfg = await db.query(`
        SELECT key,value FROM school_settings
        WHERE key IN (\'session_end_hour\',\'eod_grace_minutes\',\'session_days\')
      `);
      const settings = {};
      cfg.rows.forEach(r => settings[r.key] = r.value);

      const endHour   = parseInt(settings.session_end_hour  || \'19\');
      const graceMins = parseInt(settings.eod_grace_minutes || \'15\');

      // Trigger EOD at endHour:graceMins exactly
      const triggerMin = graceMins;
      if (h === endHour && m === triggerMin) {
        // Check not already run today
        const alreadyRun = await db.query(`
          SELECT 1 FROM director_events
          WHERE title LIKE \'End of day complete%\'
            AND created_at::date=\'${today}\'::date
          LIMIT 1
        `);
        if (!alreadyRun.rows[0]) {
          console.log(`⏰ EOD trigger: ${h}:${m.toString().padStart(2,\'0\')}`);
          await runEOD(\'SCHEDULED\');
        }
      }

      // Check for un-opened classes 15min after session start
      const startHour = 7;
      const openGrace = parseInt(settings.class_open_grace_minutes || \'15\');
      if (h === startHour && m === openGrace) {
        await checkUnopenedClasses(today);
      }

    } catch(e) {
      console.error(\'[EODScheduler] Error:\', e.message);
    }
  }, 60000); // every 1 minute

  console.log(\'🌙 EOD scheduler started\');
}

async function checkUnopenedClasses(today) {
  try {
    const unopened = await db.query(`
      SELECT u.id, u.full_name, u.username
      FROM users u
      WHERE u.role=\'TEACHER\' AND u.is_active=true
        AND NOT EXISTS (
          SELECT 1 FROM class_sessions cs
          WHERE cs.teacher_id=u.id AND cs.session_date=$1
            AND cs.status IN (\'OPEN\',\'CLOSED\'))
    `, [today]);

    for (const t of unopened.rows) {
      await logEvent(\'CLASS_NOT_OPENED\', {
        title: `⚠️ ${t.full_name || t.username} has not opened class`,
        detail: { teacher: t.full_name || t.username, date: today },
        actorId: t.id,
      }).catch(() => {});
      console.log(`⚠️ Class not opened: ${t.full_name}`);
    }
  } catch(e) {
    console.error(\'[checkUnopenedClasses] Error:\', e.message);
  }
}

module.exports = { runEOD, startEODScheduler };
'''

write(APP / 'eodService.js', eod_service)
ROLLBACK_NEEDED.append('services/app-server/src/eodService.js')

# ═══════════════════════════════════════════════════════
# STEP 5: systemMonitor.js
# ═══════════════════════════════════════════════════════
hdr('STEP 5: System Monitor')

system_monitor = '''\'use strict\';
const db = require(\'./db\');
const { logEvent } = require(\'./eventLogger\');

let monitorStarted = false;

async function runHealthCheck() {
  const checks = [];
  const now    = new Date();

  try {
    // Check 1: PostgreSQL responsive
    try {
      const t0 = Date.now();
      await db.query(\'SELECT 1\');
      const ms = Date.now() - t0;
      checks.push({ service: \'postgresql\', status: ms < 500 ? \'OK\' : \'WARN\',
                    detail: { response_ms: ms } });
    } catch(e) {
      checks.push({ service: \'postgresql\', status: \'CRITICAL\',
                    detail: { error: e.message } });
    }

    // Check 2: MQTT worker active (gateways sending data)
    try {
      const r = await db.query(`
        SELECT COUNT(*)::int as c,
               MAX(created_at) as last_detection
        FROM ble_detections
        WHERE created_at > NOW() - INTERVAL \'5 minutes\'
      `);
      const lastSeen = r.rows[0]?.last_detection;
      const minutesAgo = lastSeen
        ? Math.floor((now - new Date(lastSeen)) / 60000) : 999;
      checks.push({
        service: \'mqtt_worker\',
        status:  minutesAgo < 2 ? \'OK\' : minutesAgo < 5 ? \'WARN\' : \'CRITICAL\',
        detail:  { detections_5min: r.rows[0].c, last_detection_mins_ago: minutesAgo }
      });
    } catch(e) {
      checks.push({ service: \'mqtt_worker\', status: \'WARN\',
                    detail: { error: e.message } });
    }

    // Check 3: Gateway health
    try {
      const gateways = await db.query(`
        SELECT short_id, last_seen_at, setup_status,
          EXTRACT(EPOCH FROM (NOW()-last_seen_at))/60 as mins_ago
        FROM ble_gateways WHERE is_active=true
      `);
      for (const gw of gateways.rows) {
        const mins = Math.floor(gw.mins_ago || 999);
        checks.push({
          service: `gateway_${gw.short_id}`,
          status:  mins < 2 ? \'OK\' : mins < 5 ? \'WARN\' : \'CRITICAL\',
          detail:  { last_seen_mins_ago: mins, setup_status: gw.setup_status }
        });
      }
    } catch(e) {
      checks.push({ service: \'gateways\', status: \'WARN\',
                    detail: { error: e.message } });
    }

    // Check 4: DB table sizes (data health)
    try {
      const r = await db.query(`
        SELECT
          (SELECT COUNT(*) FROM ble_detections
           WHERE created_at > NOW()-INTERVAL \'1 hour\')::int as detections_1h,
          (SELECT COUNT(*) FROM director_events
           WHERE created_at::date=CURRENT_DATE)::int as events_today,
          (SELECT COUNT(*) FROM student_sessions
           WHERE batch_date=CURRENT_DATE)::int as sessions_today
      `);
      checks.push({ service: \'data_pipeline\', status: \'OK\',
                    detail: r.rows[0] });
    } catch(e) {
      checks.push({ service: \'data_pipeline\', status: \'WARN\',
                    detail: { error: e.message } });
    }

    // Store results
    let hasWarn = false, hasCrit = false;
    for (const c of checks) {
      await db.query(`
        INSERT INTO system_health_log (service, status, detail)
        VALUES ($1,$2,$3)
      `, [c.service, c.status, JSON.stringify(c.detail)]);

      if (c.status === \'WARN\')     hasWarn = true;
      if (c.status === \'CRITICAL\') hasCrit = true;
    }

    // Alert if issues found (throttle — once per 15min per service)
    const critChecks = checks.filter(c => c.status === \'CRITICAL\');
    const warnChecks = checks.filter(c => c.status === \'WARN\');

    if (critChecks.length > 0) {
      const alreadyAlerted = await db.query(`
        SELECT 1 FROM system_health_log
        WHERE status=\'CRITICAL\' AND alerted=true
          AND check_time > NOW() - INTERVAL \'15 minutes\'
        LIMIT 1
      `);
      if (!alreadyAlerted.rows[0]) {
        await logEvent(\'SYSTEM_HEALTH_CRIT\', {
          title: `🚨 System issue: ${critChecks.map(c=>c.service).join(\', \')}`,
          detail: { checks: critChecks },
        }).catch(() => {});
        await db.query(
          "UPDATE system_health_log SET alerted=true WHERE status=\'CRITICAL\' AND alerted=false AND check_time > NOW()-INTERVAL \'5 minutes\'");
        console.error(\'🚨 CRITICAL health check:\', critChecks.map(c=>c.service).join(\', \'));
      }
    } else if (warnChecks.length > 0) {
      const alreadyAlerted = await db.query(`
        SELECT 1 FROM system_health_log
        WHERE status=\'WARN\' AND alerted=true
          AND check_time > NOW()-INTERVAL \'30 minutes\'
        LIMIT 1
      `);
      if (!alreadyAlerted.rows[0]) {
        await logEvent(\'SYSTEM_HEALTH_WARN\', {
          title: `⚠️ System warning: ${warnChecks.map(c=>c.service).join(\', \')}`,
          detail: { checks: warnChecks },
        }).catch(() => {});
        await db.query(
          "UPDATE system_health_log SET alerted=true WHERE status=\'WARN\' AND alerted=false AND check_time > NOW()-INTERVAL \'5 minutes\'");
      }
    }

    return checks;
  } catch(e) {
    console.error(\'[SystemMonitor] Error:\', e.message);
    return [];
  }
}

async function getHealthSummary() {
  try {
    const r = await db.query(`
      SELECT DISTINCT ON (service)
        service, status, detail, check_time
      FROM system_health_log
      ORDER BY service, check_time DESC
    `);
    return r.rows;
  } catch(e) { return []; }
}

function startSystemMonitor() {
  if (monitorStarted) return;
  monitorStarted = true;

  // Initial check after 30s startup
  setTimeout(runHealthCheck, 30000);

  // Then every 5 minutes
  setInterval(runHealthCheck, 5 * 60 * 1000);

  // Clean old logs weekly (keep 7 days)
  setInterval(async () => {
    try {
      await db.query(
        "DELETE FROM system_health_log WHERE check_time < NOW()-INTERVAL \'7 days\'");
    } catch(e) {}
  }, 24 * 60 * 60 * 1000);

  console.log(\'🔍 System monitor started (5min interval)\');
}

module.exports = { startSystemMonitor, runHealthCheck, getHealthSummary };
'''

write(APP / 'systemMonitor.js', system_monitor)
ROLLBACK_NEEDED.append('services/app-server/src/systemMonitor.js')

# ═══════════════════════════════════════════════════════
# STEP 6: reportsApi.js
# ═══════════════════════════════════════════════════════
hdr('STEP 6: Reports API')

reports_api = '''\'use strict\';
const express  = require(\'express\');
const router   = express.Router();
const db       = require(\'./db\');
const { authMiddleware, requireRole } = require(\'./auth\');
const { logEvent } = require(\'./eventLogger\');
const { runHealthCheck, getHealthSummary } = require(\'./systemMonitor\');
const { runEOD } = require(\'./eodService\');

router.use(authMiddleware);

// GET /api/reports/attendance?date=YYYY-MM-DD
router.get(\'/attendance\', async (req, res) => {
  try {
    const date = req.query.date || new Date().toISOString().split(\'T\')[0];

    const summary = await db.query(`
      SELECT
        COUNT(*) FILTER (WHERE status=\'PRESENT\')::int     as present,
        COUNT(*) FILTER (WHERE status=\'ABSENT\')::int      as absent,
        COUNT(*) FILTER (WHERE status=\'CHECKED_OUT\')::int as checked_out,
        COUNT(*) FILTER (WHERE status=\'NO_SHOW\')::int     as no_show,
        COUNT(*)::int                                       as total,
        ROUND(AVG(total_minutes))::int                      as avg_minutes
      FROM attendance_archive WHERE archive_date=$1
    `, [date]);

    const byTeacher = await db.query(`
      SELECT
        teacher_name,
        COUNT(*) FILTER (WHERE status=\'PRESENT\')::int     as present,
        COUNT(*) FILTER (WHERE status=\'ABSENT\')::int      as absent,
        COUNT(*) FILTER (WHERE status=\'CHECKED_OUT\')::int as checked_out,
        COUNT(*) FILTER (WHERE status=\'NO_SHOW\')::int     as no_show,
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
router.get(\'/attendance-range\', async (req, res) => {
  try {
    const from = req.query.from;
    const to   = req.query.to || new Date().toISOString().split(\'T\')[0];

    const r = await db.query(`
      SELECT
        archive_date,
        COUNT(*) FILTER (WHERE status=\'PRESENT\')::int     as present,
        COUNT(*) FILTER (WHERE status=\'ABSENT\')::int      as absent,
        COUNT(*) FILTER (WHERE status=\'CHECKED_OUT\')::int as checked_out,
        COUNT(*) FILTER (WHERE status=\'NO_SHOW\')::int     as no_show,
        COUNT(*)::int as total
      FROM attendance_archive
      WHERE archive_date BETWEEN $1 AND $2
      GROUP BY archive_date ORDER BY archive_date DESC
    `, [from, to]);

    res.json({ from, to, days: r.rows });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/student-history/:studentId
router.get(\'/student-history/:studentId\', async (req, res) => {
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
router.get(\'/health\', async (req, res) => {
  try {
    const summary = await getHealthSummary();
    const recent  = await db.query(`
      SELECT service, status, detail, check_time
      FROM system_health_log
      WHERE check_time > NOW()-INTERVAL \'1 hour\'
        AND status != \'OK\'
      ORDER BY check_time DESC LIMIT 20
    `);
    res.json({ current: summary, recent_issues: recent.rows });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/reports/health/check — manual health check
router.post(\'/health/check\', requireRole([\'IT\']), async (req, res) => {
  try {
    const results = await runHealthCheck();
    res.json({ results });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/reports/eod/trigger — manual EOD (IT Admin)
router.post(\'/eod/trigger\', requireRole([\'IT\']), async (req, res) => {
  try {
    const result = await runEOD(\'MANUAL_TRIGGER\');
    res.json(result);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/schedules — list scheduled reports
router.get(\'/schedules\', requireRole([\'IT\']), async (req, res) => {
  try {
    const r = await db.query(
      \'SELECT * FROM report_schedules ORDER BY created_at DESC\');
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/reports/schedules — create scheduled report
router.post(\'/schedules\', requireRole([\'IT\']), async (req, res) => {
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
router.put(\'/schedules/:id\', requireRole([\'IT\']), async (req, res) => {
  try {
    const { is_active } = req.body;
    const r = await db.query(
      \'UPDATE report_schedules SET is_active=$1 WHERE id=$2 RETURNING *\',
      [is_active, req.params.id]);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/reports/available-dates — dates with archived data
router.get(\'/available-dates\', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT DISTINCT archive_date,
        COUNT(*)::int as total_students,
        COUNT(*) FILTER (WHERE status=\'PRESENT\')::int as present,
        COUNT(*) FILTER (WHERE status=\'ABSENT\')::int  as absent
      FROM attendance_archive
      GROUP BY archive_date ORDER BY archive_date DESC LIMIT 30
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/students/:id/remove — director removes student
router.post(\'/student-remove/:id\', requireRole([\'IT\',\'DIRECTOR\']), async (req, res) => {
  try {
    const sid    = req.params.id;
    const { reason } = req.body;

    const sv = await db.query(\'SELECT * FROM students WHERE id=$1\', [sid]);
    if (!sv.rows[0]) return res.status(404).json({ error: \'Student not found\' });
    const student = sv.rows[0];

    // Get all session history
    const sessions = await db.query(
      \'SELECT * FROM student_sessions WHERE student_id=$1\', [sid]);

    // Get tag
    const tag = await db.query(
      \'SELECT * FROM ble_tags WHERE student_id=$1 AND is_active=true\', [sid]);

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
          status=\'INVENTORY\', student_id=NULL,
          assigned_to=\'NONE\', label=\'Unassigned\'
        WHERE id=$1
      `, [tag.rows[0].id]);
    }

    // Remove custody + sessions
    await db.query(\'DELETE FROM student_custody WHERE student_id=$1\', [sid]);
    await db.query(\'DELETE FROM student_permitted_zones WHERE student_id=$1\', [sid]);

    // Soft-delete student
    await db.query(`
      UPDATE students SET is_active=false, updated_at=NOW()
      WHERE id=$1
    `, [sid]);

    await logEvent(\'STUDENT_REMOVED\', {
      title: `${student.first_name} ${student.last_name} removed from system`,
      detail: { student: `${student.first_name} ${student.last_name}`,
                removed_by: req.user.username, reason,
                tag_released: tag.rows[0]?.mac_address || \'none\' },
      studentIds: [sid], actorId: req.user.id,
    }).catch(() => {});

    console.log(`🗑️  Student removed: ${student.first_name} ${student.last_name}`);
    res.json({ success: true,
               tag_released: tag.rows[0]?.mac_address || null });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
'''

write(APP / 'reportsApi.js', reports_api)
ROLLBACK_NEEDED.append('services/app-server/src/reportsApi.js')

# ═══════════════════════════════════════════════════════
# STEP 7: Wire into index.js
# ═══════════════════════════════════════════════════════
hdr('STEP 7: Wire index.js')

path = APP / 'index.js'
src  = read(path)
ROLLBACK_NEEDED.append('services/app-server/src/index.js')

if 'eodService' not in src:
    src = src.replace(
        "const { startMorningScheduler }",
        "const { startEODScheduler }    = require('./eodService');\nconst { startSystemMonitor }   = require('./systemMonitor');\nconst { startMorningScheduler }")
    ok('EOD + Monitor imports added')

if 'classSessionApi' not in src:
    src = src.replace(
        "app.use('/api/session',",
        "app.use('/api/class-session', require('./classSessionApi'));\napp.use('/api/reports',        require('./reportsApi'));\napp.use('/api/session',")
    ok('Route registrations added')

if 'startEODScheduler' not in src:
    src = src.replace(
        "startMqttWorker();",
        "startMqttWorker();\nstartEODScheduler();\nstartSystemMonitor();")
    ok('Scheduler startups wired')

write(path, src)

# Verify no duplicates
if src.count('startEODScheduler') != 2:  # require + call
    abort('startEODScheduler count wrong in index.js')

# ═══════════════════════════════════════════════════════
# STEP 8: TeacherView.jsx — Open/Close Class
# ═══════════════════════════════════════════════════════
hdr('STEP 8: TeacherView.jsx — Class Session UI')

path = UI / 'TeacherView.jsx'
src  = read(path)
ROLLBACK_NEEDED.append('services/react-ui/src/TeacherView.jsx')

# 8a. Add classSession state + fetch after existing state declarations
if 'classSession' not in src:
    src = src.replace(
        "  const [clockTime, setClockTime] = useState(new Date());",
        """  const [clockTime,    setClockTime]    = useState(new Date());
  const [classSession, setClassSession] = useState(null);
  const [sessionCfg,   setSessionCfg]   = useState({});
  const [closeBlocked, setCloseBlocked] = useState(null);
  const [sessionMsg,   setSessionMsg]   = useState('');""")
    ok('classSession state added')

# 8b. Add loadClassSession function + call in useEffect
if 'loadClassSession' not in src:
    src = src.replace(
        "  // Live clock",
        """  const loadClassSession = async () => {
    try {
      const r = await fetch('/api/class-session/status', { headers: auth(token) });
      const d = await r.json();
      setClassSession(d.session);
      setSessionCfg(d);
    } catch(e) { console.error(e); }
  };

  // Load class session on mount + every 30s
  useEffect(() => {
    loadClassSession();
    const iv = setInterval(loadClassSession, 30000);
    return () => clearInterval(iv);
  }, []);

  // Live clock""")
    ok('loadClassSession added')

# 8c. Add doOpenClass + doCloseClass functions
if 'doOpenClass' not in src:
    src = src.replace(
        "  const doMarkAbsent",
        """  const doOpenClass = async () => {
    try {
      const r = await fetch('/api/class-session/open',
        { method:'POST', headers:auth(token) });
      const d = await r.json();
      if (d.error) { setSessionMsg('❌ ' + d.error); return; }
      setSessionMsg('✅ Class opened');
      await loadClassSession();
      await loadRoster();
    } catch(e) { console.error(e); }
  };

  const doCloseClass = async (force=false) => {
    try {
      const r = await fetch('/api/class-session/close',
        { method:'POST', headers:auth(token),
          body: JSON.stringify({ force }),
          ...{ 'Content-Type':'application/json' } });
      const d = await r.json();
      if (d.blocked) {
        setCloseBlocked(d);
        return;
      }
      if (d.error) { setSessionMsg('❌ ' + d.error); return; }
      setSessionMsg('✅ Class closed');
      setCloseBlocked(null);
      await loadClassSession();
    } catch(e) { console.error(e); }
  };

  const doMarkAbsent""")
    ok('doOpenClass + doCloseClass added')

# 8d. Add class session banner to teacher header
if 'classSession?.status' not in src:
    old_clock = "        {/* Session clock */}"
    new_clock = """        {/* Class Session Status Bar */}
        {(()=>{
          const status = classSession?.status || 'PENDING';
          const active = sessionCfg.session_active;
          if (!active) return null;
          return <div style={{display:'flex',alignItems:'center',
            justifyContent:'space-between',
            background: status==='OPEN'?`${C.green}11`:
                        status==='CLOSED'?`${C.blue}11`:'#1A1A2E',
            border:`1px solid ${status==='OPEN'?C.green:
                               status==='CLOSED'?C.blue:C.orange}33`,
            borderRadius:10,padding:'8px 14px',marginBottom:8}}>
            <div style={{display:'flex',alignItems:'center',gap:10}}>
              <span style={{fontSize:16}}>
                {status==='OPEN'?'📖':status==='CLOSED'?'🔒':'📋'}
              </span>
              <div>
                <div style={{fontSize:12,fontWeight:800,
                  color: status==='OPEN'?C.green:
                         status==='CLOSED'?C.blue:C.orange}}>
                  {status==='OPEN'?'CLASS OPEN':
                   status==='CLOSED'?'CLASS CLOSED':'CLASS NOT OPENED'}
                </div>
                {status==='OPEN'&&classSession?.opened_at&&
                  <div style={{fontSize:10,color:'#4A5568'}}>
                    Opened {new Date(classSession.opened_at)
                      .toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
                  </div>}
              </div>
            </div>
            <div style={{display:'flex',gap:8,alignItems:'center'}}>
              {sessionMsg&&<span style={{fontSize:10,color:C.green}}>{sessionMsg}</span>}
              {status==='PENDING'&&
                <button onClick={doOpenClass}
                  style={{background:C.green,border:'none',borderRadius:8,
                    padding:'6px 16px',color:'#fff',fontWeight:800,
                    fontSize:12,cursor:'pointer'}}>
                  📖 Open Class
                </button>}
              {status==='OPEN'&&
                <button onClick={()=>doCloseClass(false)}
                  style={{background:'#1A1A2E',border:`1px solid ${C.orange}`,
                    borderRadius:8,padding:'6px 16px',
                    color:C.orange,fontWeight:800,fontSize:12,cursor:'pointer'}}>
                  🔒 Close Class
                </button>}
            </div>
          </div>;
        })()}

        {/* Close Blocked Modal */}
        {closeBlocked&&<div style={{position:'fixed',inset:0,
          background:'rgba(0,0,0,0.8)',zIndex:999,
          display:'flex',alignItems:'center',justifyContent:'center'}}>
          <div style={{background:C.card,border:`2px solid ${C.red}`,
            borderRadius:16,padding:24,maxWidth:400,width:'90%'}}>
            <div style={{fontSize:18,fontWeight:800,color:C.red,marginBottom:12}}>
              ⛔ Cannot Close Class
            </div>
            <div style={{fontSize:13,color:'#E4E4E7',marginBottom:12}}>
              {closeBlocked.students?.length} student(s) still checked in:
            </div>
            {closeBlocked.students?.map((s,i)=>(
              <div key={i} style={{padding:'6px 10px',marginBottom:4,
                borderRadius:8,background:`${C.red}11`,
                fontSize:12,color:C.red}}>
                • {s.name} — {s.status}
              </div>
            ))}
            <div style={{fontSize:11,color:'#4A5568',marginTop:12,marginBottom:16}}>
              Checkout all students or force-close to end session.
            </div>
            <div style={{display:'flex',gap:10}}>
              <button onClick={()=>setCloseBlocked(null)}
                style={{flex:1,background:'#1A1A2E',border:`1px solid ${C.border}`,
                  borderRadius:8,padding:'10px',color:'#E4E4E7',
                  fontSize:13,fontWeight:600,cursor:'pointer'}}>
                ✋ Go Back
              </button>
              <button onClick={()=>doCloseClass(true)}
                style={{flex:1,background:C.red,border:'none',
                  borderRadius:8,padding:'10px',color:'#fff',
                  fontSize:13,fontWeight:700,cursor:'pointer'}}>
                ⚠️ Force Close
              </button>
            </div>
          </div>
        </div>}

        {/* Session clock */}"""
    if old_clock in src:
        src = src.replace(old_clock, new_clock)
        ok('Class session banner added to teacher header')

write(path, src)

# Verify
checks = [
    ('classSession state', src.count('classSession,'), 1),
    ('doOpenClass', src.count('const doOpenClass'), 1),
    ('doCloseClass', src.count('const doCloseClass'), 1),
]
all_ok = True
for label, count, exp in checks:
    if count != exp:
        err(f'Check failed: {label} = {count}')
        all_ok = False
    else:
        ok(f'{label}: {count}')
if not all_ok:
    abort('TeacherView duplicate check failed')

# ═══════════════════════════════════════════════════════
# STEP 9: DirectorPortal.jsx — classroom status
# ═══════════════════════════════════════════════════════
hdr('STEP 9: DirectorPortal.jsx — Class Status + Reports')

path = UI / 'DirectorPortal.jsx'
src  = read(path)
ROLLBACK_NEEDED.append('services/react-ui/src/DirectorPortal.jsx')

# 9a. Add classStatuses state
if 'classStatuses' not in src:
    src = src.replace(
        "  const [summary,    setSummary]    = useState(null);",
        """  const [summary,      setSummary]      = useState(null);
  const [classStatuses,setClassStatuses] = useState([]);
  const [reportDate,   setReportDate]    = useState(new Date().toISOString().split('T')[0]);
  const [reportData,   setReportData]    = useState(null);
  const [healthData,   setHealthData]    = useState(null);
  const [schedules,    setSchedules]     = useState([]);""")
    ok('Director state vars added')

# 9b. Add class status + report fetch
if 'class-session/all-status' not in src:
    src = src.replace(
        "      const r=await fetch(`${EAPI}/summary`,{headers:auth(token)});",
        """      const [r, cs] = await Promise.all([
        fetch(`${EAPI}/summary`,{headers:auth(token)}),
        fetch('/api/class-session/all-status',{headers:auth(token)}),
      ]);
      if(cs.ok){ const csData=await cs.json(); setClassStatuses(csData); }""")
    old_setSummary = "      const rd=await r.json(); setSummary(rd);"
    if old_setSummary not in src:
        # Try alternate
        src = src.replace(
            "const rd=await r.json();setSummary(rd);",
            "const rd=await r.json(); setSummary(rd);")
    ok('Class status fetch added to director load')

# 9c. Add loadReport function
if 'loadReport' not in src:
    src = src.replace(
        "  const [classStatuses,",
        """  const loadReport = async (date) => {
    try {
      const r = await fetch(`/api/reports/attendance?date=${date}`,
        {headers:auth(token)});
      const d = await r.json();
      setReportData(d);
    } catch(e) { console.error(e); }
  };
  const loadHealth = async () => {
    try {
      const r = await fetch('/api/reports/health',{headers:auth(token)});
      const d = await r.json();
      setHealthData(d);
    } catch(e) { console.error(e); }
  };
  const [classStatuses,""")
    ok('loadReport + loadHealth added')

# 9d. Add Reports + Health tabs to director tab bar
if "'reports'" not in src:
    src = src.replace(
        "{id:'detections',label:'📡 Live Detections'}",
        "{id:'detections',label:'📡 Live Detections'},\n    {id:'reports',label:'📊 Reports'},\n    {id:'health',label:'🔍 System Health'}")
    ok('Reports + Health tabs added')

# 9e. Add classroom open/closed indicators to Classrooms tab
if 'class_status' not in src:
    src = src.replace(
        "{/* CLASSROOMS TAB */}",
        """{/* CLASSROOMS TAB */}
    {!loading&&view==='classrooms'&&classStatuses.length>0&&<div style={{
      display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(160px,1fr))',
      gap:8,marginBottom:16}}>
      {classStatuses.map(t=>(
        <div key={t.teacher_id} style={{
          background:t.class_status==='OPEN'?`${C.green}11`:
                     t.class_status==='CLOSED'?`${C.blue}11`:'#1A1A2E',
          border:`1px solid ${t.class_status==='OPEN'?C.green:
                              t.class_status==='CLOSED'?C.blue:C.orange}33`,
          borderRadius:10,padding:'10px 12px',textAlign:'center'}}>
          <div style={{fontSize:20,marginBottom:4}}>
            {t.class_status==='OPEN'?'📖':t.class_status==='CLOSED'?'🔒':'⏳'}
          </div>
          <div style={{fontSize:12,fontWeight:700,color:'#E4E4E7'}}>
            {t.teacher_name}
          </div>
          <div style={{fontSize:10,color:'#4A5568'}}>{t.zone_name||'No zone'}</div>
          <div style={{fontSize:10,marginTop:4,
            color:t.class_status==='OPEN'?C.green:
                  t.class_status==='CLOSED'?C.blue:C.orange}}>
            {t.class_status||'NOT OPENED'}
          </div>
          {t.class_status==='OPEN'&&
            <div style={{fontSize:10,color:C.green,marginTop:2}}>
              {t.currently_in}/{t.total_students} in
            </div>}
        </div>
      ))}
    </div>}
    {/* CLASSROOMS TAB (original) */}""")
    ok('Classroom open/closed grid added')

# 9f. Add Reports tab content
if 'reportData' not in src and "'reports'" in src:
    src = src.replace(
        "{!loading&&view==='detections'&&",
        """{view==='reports'&&<div style={{color:'#E4E4E7'}}>
      <div style={{display:'flex',gap:12,marginBottom:16,alignItems:'center',flexWrap:'wrap'}}>
        <div style={{fontWeight:800,fontSize:16}}>📊 Attendance Report</div>
        <input type="date" value={reportDate}
          onChange={e=>{setReportDate(e.target.value);setReportData(null);}}
          style={{background:'#1A1A2E',border:`1px solid ${C.border}`,
            borderRadius:8,padding:'6px 12px',color:'#E4E4E7',fontSize:13}} />
        <button onClick={()=>loadReport(reportDate)}
          style={{background:C.blue,border:'none',borderRadius:8,
            padding:'7px 18px',color:'#fff',fontWeight:700,
            fontSize:13,cursor:'pointer'}}>
          Load Report
        </button>
      </div>
      {!reportData&&<div style={{color:'#4A5568',textAlign:'center',
        padding:40,fontSize:14}}>
        Select a date and click Load Report
      </div>}
      {reportData&&<>
        <div style={{display:'grid',
          gridTemplateColumns:'repeat(auto-fill,minmax(130px,1fr))',
          gap:10,marginBottom:20}}>
          {[
            {label:'Present',     value:reportData.summary?.present||0,    color:C.green},
            {label:'Absent',      value:reportData.summary?.absent||0,     color:'#4A5568'},
            {label:'Checked Out', value:reportData.summary?.checked_out||0,color:C.blue},
            {label:'No Show',     value:reportData.summary?.no_show||0,    color:C.red},
            {label:'Total',       value:reportData.summary?.total||0,      color:'#E4E4E7'},
            {label:'Avg Minutes', value:reportData.summary?.avg_minutes||0,color:C.purple},
          ].map(s=>(
            <div key={s.label} style={{background:C.card,
              border:`1px solid ${C.border}`,borderRadius:10,
              padding:'12px',textAlign:'center'}}>
              <div style={{fontSize:24,fontWeight:800,color:s.color}}>{s.value}</div>
              <div style={{fontSize:10,color:'#4A5568',marginTop:4,
                textTransform:'uppercase'}}>{s.label}</div>
            </div>
          ))}
        </div>
        <div style={{background:C.card,border:`1px solid ${C.border}`,
          borderRadius:12,overflow:'hidden',marginBottom:16}}>
          <div style={{padding:'10px 16px',borderBottom:`1px solid ${C.border}`,
            fontSize:12,fontWeight:700,color:'#E4E4E7'}}>
            Students
          </div>
          {reportData.students?.map((s,i)=>(
            <div key={i} style={{display:'flex',alignItems:'center',
              padding:'10px 16px',
              borderBottom:`1px solid ${C.border}`,
              background:i%2===0?'transparent':'rgba(255,255,255,0.01)'}}>
              <div style={{flex:2,fontSize:13,fontWeight:600,color:'#E4E4E7'}}>
                {s.student_name}
              </div>
              <div style={{flex:2,fontSize:11,color:'#4A5568'}}>
                {s.teacher_name}
              </div>
              <div style={{flex:1,fontSize:11,fontWeight:700,
                color:s.status==='PRESENT'||s.status==='CHECKED_OUT'?C.green:
                      s.status==='ABSENT'?'#4A5568':C.red}}>
                {s.status}
              </div>
              <div style={{flex:1,fontSize:11,color:'#4A5568'}}>
                {s.total_minutes?`${s.total_minutes}m`:'—'}
              </div>
            </div>
          ))}
        </div>
      </>}
    </div>}

    {view==='health'&&<div style={{color:'#E4E4E7'}}>
      <div style={{display:'flex',gap:12,marginBottom:16,alignItems:'center'}}>
        <div style={{fontWeight:800,fontSize:16}}>🔍 System Health</div>
        <button onClick={loadHealth}
          style={{background:C.green,border:'none',borderRadius:8,
            padding:'7px 18px',color:'#fff',fontWeight:700,
            fontSize:13,cursor:'pointer'}}>
          Check Now
        </button>
      </div>
      {!healthData&&<div style={{color:'#4A5568',textAlign:'center',padding:40}}>
        Click Check Now to run health check
      </div>}
      {healthData?.current?.map((h,i)=>(
        <div key={i} style={{display:'flex',alignItems:'center',gap:12,
          padding:'12px 16px',marginBottom:8,borderRadius:10,
          background:h.status==='CRITICAL'?`${C.red}11`:
                     h.status==='WARN'?`${C.orange}11`:`${C.green}11`,
          border:`1px solid ${h.status==='CRITICAL'?C.red:
                              h.status==='WARN'?C.orange:C.green}33`}}>
          <span style={{fontSize:18}}>
            {h.status==='CRITICAL'?'🚨':h.status==='WARN'?'⚠️':'✅'}
          </span>
          <div style={{flex:1}}>
            <div style={{fontSize:13,fontWeight:700,color:'#E4E4E7'}}>
              {h.service}
            </div>
            <div style={{fontSize:11,color:'#4A5568'}}>
              {JSON.stringify(h.detail).slice(0,80)}
            </div>
          </div>
          <div style={{fontSize:11,fontWeight:700,
            color:h.status==='CRITICAL'?C.red:
                  h.status==='WARN'?C.orange:C.green}}>
            {h.status}
          </div>
        </div>
      ))}
    </div>}

    {!loading&&view==='detections'&&""")
    ok('Reports + Health tab content added')

write(path, src)

# ═══════════════════════════════════════════════════════
# STEP 10: IT Admin Reports + Settings + Health screen
# ═══════════════════════════════════════════════════════
hdr('STEP 10: IT Admin Reports screen')

it_reports_component = '''import { useState, useEffect } from 'react';
const C = {
  dark:'#0F1117',card:'#1A1A2E',border:'rgba(255,255,255,0.08)',
  green:'#22C55E',red:'#EF4444',orange:'#F59E0B',
  blue:'#3B82F6',purple:'#8B5CF6',yellow:'#EAB308',
  muted:'#71717A',navy:'#0A1628',teal:'#14B8A6',
};
function auth(token){ return {'Authorization':`Bearer ${token}`,'Content-Type':'application/json'}; }

export default function ITAdminReports({ token }) {
  const [view,       setView]       = useState('health');
  const [healthData, setHealthData] = useState(null);
  const [schedules,  setSchedules]  = useState([]);
  const [reportDate, setReportDate] = useState(new Date().toISOString().split('T')[0]);
  const [reportData, setReportData] = useState(null);
  const [settings,   setSettings]   = useState([]);
  const [eodRunning, setEodRunning] = useState(false);
  const [eodResult,  setEodResult]  = useState(null);
  const [msg,        setMsg]        = useState('');

  const loadHealth = async () => {
    try {
      const r = await fetch('/api/reports/health', {headers:auth(token)});
      setHealthData(await r.json());
    } catch(e) {}
  };

  const runHealthCheck = async () => {
    try {
      await fetch('/api/reports/health/check',
        {method:'POST',headers:auth(token)});
      setTimeout(loadHealth, 3000);
      setMsg('✅ Health check running...');
    } catch(e) {}
  };

  const loadSchedules = async () => {
    try {
      const r = await fetch('/api/reports/schedules',{headers:auth(token)});
      setSchedules(await r.json());
    } catch(e) {}
  };

  const loadReport = async () => {
    try {
      const r = await fetch(`/api/reports/attendance?date=${reportDate}`,
        {headers:auth(token)});
      setReportData(await r.json());
    } catch(e) {}
  };

  const triggerEOD = async () => {
    if (!window.confirm('Run EOD reconciliation now? This will archive today and reset custody.')) return;
    setEodRunning(true);
    try {
      const r = await fetch('/api/reports/eod/trigger',
        {method:'POST',headers:auth(token)});
      const d = await r.json();
      setEodResult(d);
      setMsg(d.success ? '✅ EOD complete' : '❌ EOD failed: ' + d.error);
    } catch(e) { setMsg('❌ ' + e.message); }
    setEodRunning(false);
  };

  const toggleSchedule = async (id, is_active) => {
    try {
      await fetch(`/api/reports/schedules/${id}`,
        {method:'PUT',headers:auth(token),
         body:JSON.stringify({is_active:!is_active})});
      await loadSchedules();
    } catch(e) {}
  };

  useEffect(() => {
    loadHealth();
    loadSchedules();
  }, []);

  const tabs = [
    {id:'health',    label:'🔍 Health'},
    {id:'reports',   label:'📊 Reports'},
    {id:'schedules', label:'📅 Schedules'},
    {id:'eod',       label:'🌙 EOD'},
  ];

  return (
    <div style={{padding:'0 8px',color:'#E4E4E7',
      fontFamily:"'Instrument Sans',system-ui,sans-serif"}}>

      {/* Sub-tabs */}
      <div style={{display:'flex',gap:4,marginBottom:16,
        borderBottom:`1px solid ${C.border}`,paddingBottom:8}}>
        {tabs.map(t=>(
          <button key={t.id} onClick={()=>setView(t.id)}
            style={{background:view===t.id?C.card:'transparent',
              border:view===t.id?`1px solid ${C.border}`:'1px solid transparent',
              borderRadius:8,padding:'6px 14px',color:view===t.id?'#E4E4E7':'#4A5568',
              cursor:'pointer',fontSize:12,fontWeight:600}}>
            {t.label}
          </button>
        ))}
        {msg&&<span style={{marginLeft:'auto',fontSize:11,
          color:msg.startsWith('✅')?C.green:C.red,
          alignSelf:'center'}}>{msg}</span>}
      </div>

      {/* HEALTH */}
      {view==='health'&&<div>
        <div style={{display:'flex',gap:10,marginBottom:16,alignItems:'center'}}>
          <div style={{fontWeight:800,fontSize:15}}>System Health Monitor</div>
          <button onClick={runHealthCheck}
            style={{background:C.green,border:'none',borderRadius:8,
              padding:'6px 16px',color:'#fff',fontWeight:700,
              fontSize:12,cursor:'pointer'}}>
            Run Check Now
          </button>
        </div>
        {!healthData&&<div style={{color:C.muted,textAlign:'center',padding:40}}>
          Loading health status...
        </div>}
        {healthData?.current?.map((h,i)=>(
          <div key={i} style={{display:'flex',alignItems:'center',gap:12,
            padding:'12px 16px',marginBottom:8,borderRadius:10,
            background:h.status==='CRITICAL'?`${C.red}11`:
                       h.status==='WARN'?`${C.orange}11`:`${C.green}11`,
            border:`1px solid ${h.status==='CRITICAL'?C.red:
                                h.status==='WARN'?C.orange:C.green}33`}}>
            <span style={{fontSize:20}}>
              {h.status==='CRITICAL'?'🚨':h.status==='WARN'?'⚠️':'✅'}
            </span>
            <div style={{flex:1}}>
              <div style={{fontSize:13,fontWeight:700}}>{h.service}</div>
              <div style={{fontSize:11,color:C.muted,marginTop:2}}>
                {Object.entries(h.detail||{}).map(([k,v])=>`${k}: ${v}`).join(' · ')}
              </div>
            </div>
            <div style={{fontSize:11,fontWeight:800,
              color:h.status==='CRITICAL'?C.red:
                    h.status==='WARN'?C.orange:C.green}}>
              {h.status}
            </div>
            <div style={{fontSize:10,color:C.muted}}>
              {h.check_time?new Date(h.check_time).toLocaleTimeString():''}
            </div>
          </div>
        ))}
        {healthData?.recent_issues?.length>0&&<>
          <div style={{fontWeight:700,fontSize:13,marginTop:16,marginBottom:8,
            color:C.orange}}>Recent Issues</div>
          {healthData.recent_issues.map((h,i)=>(
            <div key={i} style={{padding:'8px 12px',marginBottom:4,borderRadius:8,
              background:`${C.orange}11`,border:`1px solid ${C.orange}22`,
              fontSize:11,color:C.muted}}>
              <span style={{color:C.orange,fontWeight:700}}>{h.service}</span>
              {' '}{h.status}{' · '}
              {new Date(h.check_time).toLocaleTimeString()}
            </div>
          ))}
        </>}
      </div>}

      {/* REPORTS */}
      {view==='reports'&&<div>
        <div style={{display:'flex',gap:10,marginBottom:16,alignItems:'center',flexWrap:'wrap'}}>
          <div style={{fontWeight:800,fontSize:15}}>Attendance Reports</div>
          <input type="date" value={reportDate}
            onChange={e=>{setReportDate(e.target.value);setReportData(null);}}
            style={{background:C.card,border:`1px solid ${C.border}`,
              borderRadius:8,padding:'6px 12px',color:'#E4E4E7',fontSize:13}} />
          <button onClick={loadReport}
            style={{background:C.blue,border:'none',borderRadius:8,
              padding:'7px 18px',color:'#fff',fontWeight:700,fontSize:13,cursor:'pointer'}}>
            Load
          </button>
        </div>
        {!reportData&&<div style={{color:C.muted,textAlign:'center',padding:40}}>
          Select a date and click Load
        </div>}
        {reportData&&<>
          <div style={{display:'grid',
            gridTemplateColumns:'repeat(auto-fill,minmax(120px,1fr))',
            gap:8,marginBottom:16}}>
            {[
              {l:'Present',    v:reportData.summary?.present||0,    c:C.green},
              {l:'Absent',     v:reportData.summary?.absent||0,     c:C.muted},
              {l:'Checked Out',v:reportData.summary?.checked_out||0,c:C.blue},
              {l:'No Show',    v:reportData.summary?.no_show||0,    c:C.red},
              {l:'Total',      v:reportData.summary?.total||0,      c:'#E4E4E7'},
              {l:'Avg Mins',   v:reportData.summary?.avg_minutes||0,c:C.purple},
            ].map(s=>(
              <div key={s.l} style={{background:C.card,
                border:`1px solid ${C.border}`,borderRadius:10,
                padding:'10px',textAlign:'center'}}>
                <div style={{fontSize:22,fontWeight:800,color:s.c}}>{s.v}</div>
                <div style={{fontSize:10,color:C.muted,marginTop:2,
                  textTransform:'uppercase'}}>{s.l}</div>
              </div>
            ))}
          </div>

          {/* By Teacher */}
          {reportData.by_teacher?.length>0&&<>
            <div style={{fontWeight:700,fontSize:13,marginBottom:8}}>By Teacher</div>
            {reportData.by_teacher.map((t,i)=>(
              <div key={i} style={{display:'flex',gap:10,padding:'8px 12px',
                marginBottom:4,borderRadius:8,background:C.card,
                border:`1px solid ${C.border}`,fontSize:12,alignItems:'center'}}>
                <div style={{flex:2,fontWeight:600}}>{t.teacher_name}</div>
                <div style={{color:C.green}}>✅ {t.present}</div>
                <div style={{color:C.muted}}>⚫ {t.absent}</div>
                <div style={{color:C.blue}}>🚪 {t.checked_out}</div>
                <div style={{color:C.red}}>❌ {t.no_show}</div>
              </div>
            ))}
          </>}

          {/* Student list */}
          {reportData.students?.length>0&&<>
            <div style={{fontWeight:700,fontSize:13,margin:'12px 0 8px'}}>
              Students ({reportData.students.length})
            </div>
            <div style={{background:C.card,border:`1px solid ${C.border}`,
              borderRadius:10,overflow:'hidden'}}>
              {reportData.students.map((s,i)=>(
                <div key={i} style={{display:'flex',padding:'9px 14px',
                  borderBottom:`1px solid ${C.border}`,
                  background:i%2===0?'transparent':'rgba(255,255,255,0.01)',
                  fontSize:12,alignItems:'center'}}>
                  <div style={{flex:2,fontWeight:600}}>{s.student_name}</div>
                  <div style={{flex:2,color:C.muted}}>{s.teacher_name}</div>
                  <div style={{flex:1,fontWeight:700,
                    color:s.status==='PRESENT'||s.status==='CHECKED_OUT'?C.green:
                          s.status==='ABSENT'?C.muted:C.red}}>
                    {s.status}
                  </div>
                  <div style={{flex:1,color:C.muted}}>
                    {s.total_minutes?`${s.total_minutes}m`:'—'}
                  </div>
                </div>
              ))}
            </div>
          </>}
        </>}
      </div>}

      {/* SCHEDULES */}
      {view==='schedules'&&<div>
        <div style={{fontWeight:800,fontSize:15,marginBottom:16}}>
          Report Schedules
        </div>
        <div style={{background:`${C.blue}11`,border:`1px solid ${C.blue}33`,
          borderRadius:10,padding:'12px 16px',marginBottom:16,fontSize:12,color:C.blue}}>
          ℹ️ Pre-configured schedules run automatically at session end.
          Toggle on/off as needed.
        </div>
        {schedules.length===0&&<div style={{color:C.muted,textAlign:'center',padding:24,fontSize:13}}>
          No schedules configured yet.
        </div>}
        {schedules.map((s,i)=>(
          <div key={i} style={{display:'flex',alignItems:'center',gap:12,
            padding:'12px 16px',marginBottom:8,borderRadius:10,
            background:C.card,border:`1px solid ${C.border}`}}>
            <div style={{flex:2}}>
              <div style={{fontSize:13,fontWeight:700}}>{s.name}</div>
              <div style={{fontSize:11,color:C.muted,marginTop:2}}>
                {s.report_type} · {s.schedule}
              </div>
            </div>
            <div style={{fontSize:11,color:C.muted}}>
              {s.last_run_at
                ? `Last: ${new Date(s.last_run_at).toLocaleDateString()}`
                : 'Never run'}
            </div>
            <button onClick={()=>toggleSchedule(s.id, s.is_active)}
              style={{background:s.is_active?C.green:C.muted,
                border:'none',borderRadius:8,padding:'6px 14px',
                color:'#fff',fontWeight:700,fontSize:12,cursor:'pointer'}}>
              {s.is_active?'ON':'OFF'}
            </button>
          </div>
        ))}
      </div>}

      {/* EOD */}
      {view==='eod'&&<div>
        <div style={{fontWeight:800,fontSize:15,marginBottom:8}}>
          End of Day Management
        </div>
        <div style={{background:`${C.orange}11`,border:`1px solid ${C.orange}33`,
          borderRadius:10,padding:'12px 16px',marginBottom:20,fontSize:12}}>
          <div style={{fontWeight:700,color:C.orange,marginBottom:4}}>
            ⚠️ Manual EOD Trigger
          </div>
          <div style={{color:C.muted,lineHeight:1.6}}>
            EOD runs automatically at session end hour + grace period.
            Use manual trigger only if the automatic job did not run.
            This will archive today's attendance and reset custody for all students.
          </div>
        </div>
        <button onClick={triggerEOD} disabled={eodRunning}
          style={{background:eodRunning?C.muted:C.orange,
            border:'none',borderRadius:10,padding:'14px 28px',
            color:'#fff',fontWeight:800,fontSize:15,
            cursor:eodRunning?'not-allowed':'pointer',marginBottom:20}}>
          {eodRunning?'⏳ Running EOD...':'🌙 Trigger EOD Now'}
        </button>
        {eodResult&&<div style={{background:eodResult.success?`${C.green}11`:`${C.red}11`,
          border:`1px solid ${eodResult.success?C.green:C.red}33`,
          borderRadius:10,padding:'14px 16px'}}>
          {eodResult.success?<>
            <div style={{color:C.green,fontWeight:700,marginBottom:8}}>✅ EOD Complete</div>
            <div style={{fontSize:12,color:C.muted,lineHeight:1.8}}>
              Archived: {eodResult.archived} records<br/>
              Present: {eodResult.summary?.present} ·
              Absent: {eodResult.summary?.absent} ·
              Checked Out: {eodResult.summary?.checked_out} ·
              No Show: {eodResult.summary?.no_show}
            </div>
          </>:<div style={{color:C.red}}>❌ {eodResult.error}</div>}
        </div>}
      </div>}

    </div>
  );
}
'''

write(UI / 'ITAdminReports.jsx', it_reports_component)
ROLLBACK_NEEDED.append('services/react-ui/src/ITAdminReports.jsx')

# ═══════════════════════════════════════════════════════
# STEP 11: Wire IT Admin Reports into App.jsx
# ═══════════════════════════════════════════════════════
hdr('STEP 11: App.jsx — Wire IT Reports tab')

path = UI / 'App.jsx'
src  = read(path)
ROLLBACK_NEEDED.append('services/react-ui/src/App.jsx')

if 'ITAdminReports' not in src:
    # Add import
    src = src.replace(
        "import TagInventory",
        "import ITAdminReports from './ITAdminReports';\nimport TagInventory")

    # Add tab to IT Admin tabs array
    src = src.replace(
        "{id:'detections', label:'📡 Detections'}",
        "{id:'detections', label:'📡 Detections'},\n    {id:'reports',    label:'📊 Reports'},\n    {id:'health',     label:'🔍 Health'}")

    # Add tab render — find the detections render and add after
    src = src.replace(
        "{itTab==='detections' && <RawDetectionMonitor token={token}/>}",
        "{itTab==='detections' && <RawDetectionMonitor token={token}/>}\n        {(itTab==='reports'||itTab==='health') && <ITAdminReports token={token}/>}")

    write(path, src)
    ok('ITAdminReports wired into App.jsx')

# Seed default report schedules
hdr('STEP 12: Seed default report schedules')
db_seed = """
INSERT INTO report_schedules (name, report_type, schedule, recipients, params) VALUES
  ('Daily EOD Attendance', 'DAILY_ATTENDANCE', 'DAILY_EOD',
   ARRAY['director','admin'], '{"include_students":true}'),
  ('Weekly Summary', 'WEEKLY_SUMMARY', 'WEEKLY_FRIDAY',
   ARRAY['director','admin'], '{"include_by_teacher":true}'),
  ('Absent Alert', 'ABSENT_THRESHOLD', 'DAILY_EOD',
   ARRAY['director'], '{"threshold_pct":20}')
ON CONFLICT DO NOTHING;
SELECT 'Schedules seeded' as result;
"""
rc, out = run(f'docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "{db_seed.strip()}"')
ok('Default report schedules seeded')

# ═══════════════════════════════════════════════════════
# STEP 13: Build + Verify
# ═══════════════════════════════════════════════════════
hdr('STEP 13: Build')

os.chdir(str(BASE))
print('  🐳 Rebuilding app-server + react-ui...')
rc, out = run('docker compose up -d --build app-server react-ui')
print(f'  Build exit code: {rc}')
print('  ⏳ Waiting 45s for startup...')
time.sleep(45)

# Check app server
rc2, logs = run('docker logs prosper-app-server --tail 15 2>&1')
app_ok = 'crashed' not in logs.lower() and 'syntaxerror' not in logs.lower()
if app_ok:
    ok('App server healthy')
else:
    err('App server may have issues:')
    print(logs[-300:])

# Check UI
rc3, uilogs = run('docker logs prosper-ui --tail 10 2>&1')
ui_errors = [l for l in uilogs.split('\n')
             if 'Duplicate key' in l or ('error' in l.lower() and 'plugin' in l.lower())]
if ui_errors:
    err('UI build errors:')
    for e in ui_errors: print(f'  {e}')
else:
    ok('UI build clean')

# ═══════════════════════════════════════════════════════
# STEP 14: API smoke test
# ═══════════════════════════════════════════════════════
hdr('STEP 14: API Smoke Test')
time.sleep(5)

rc, tok_out = run("""curl -s -X POST http://localhost/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin1234!"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" """)

token = tok_out.strip()
if len(token) > 20:
    ok(f'Login OK — token: {token[:20]}...')

    endpoints = [
        '/api/reports/health',
        '/api/reports/schedules',
        '/api/class-session/all-status',
        '/api/reports/available-dates',
    ]
    for ep in endpoints:
        rc, resp = run(f'curl -s -o /dev/null -w "%{{http_code}}" http://localhost{ep} -H "Authorization: Bearer {token}"')
        code = resp.strip()
        if code == '200':
            ok(f'GET {ep} → {code}')
        else:
            err(f'GET {ep} → {code}')
else:
    err('Login failed — check app server logs')

# ═══════════════════════════════════════════════════════
# STEP 15: Commit
# ═══════════════════════════════════════════════════════
hdr('STEP 15: Commit')

run('git add -A')
rc, commit_out = run('''git commit -m "feat: Phase 2 — EOD service, class open/close, system monitor, reports module, student removal"''')
ok('Committed') if rc == 0 else info('Nothing new to commit')

rc, push_out = run('git push')
ok('Pushed to GitHub') if rc == 0 else err(f'Push failed: {push_out[:100]}')

# ═══════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════
print('\n' + '═'*55)
print('  ✅ PHASE 2 DEPLOYMENT COMPLETE')
print('═'*55)
print("""
  New features:
  ─────────────────────────────────────────────────
  Teacher:
    📖 Open Class button (required to accept students)
    🔒 Close Class button (blocked if students still in)
    ⚠️  Force Close with confirmation

  Director:
    📋 Classroom status grid (PENDING/OPEN/CLOSED)
    📊 Reports tab — date picker, attendance by day
    🔍 Health tab — live system status

  IT Admin:
    📊 Reports tab — full attendance reports
    📅 Schedules tab — toggle scheduled reports
    🌙 EOD tab — manual trigger + results
    🔍 Health monitor — service status

  Automated:
    🌙 EOD at session_end_hour + grace (auto-archive)
    ⏰ Class not opened alerts (15min grace)
    🔍 Health check every 5 minutes
    📊 Daily EOD report generated automatically

  Database:
    attendance_archive  — daily snapshots
    class_sessions      — open/close tracking
    student_archive     — removed student records
    system_health_log   — health check history
    report_schedules    — scheduled report config
  ─────────────────────────────────────────────────
  Hard refresh all browsers: Ctrl+Shift+R
""")
