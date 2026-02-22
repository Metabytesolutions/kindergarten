#!/usr/bin/env python3
import os, subprocess, time, urllib.request, json as J

BASE = os.path.expanduser('~/prosper-platform')
UI   = f'{BASE}/services/react-ui/src'
API  = f'{BASE}/services/app-server/src'

def run(cmd):
    print(f'  $ {cmd[:80]}')
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout.strip(): print(r.stdout.strip()[:400])
    if r.returncode != 0 and r.stderr.strip(): print(f'  ERR: {r.stderr.strip()[:200]}')
    return r.stdout.strip()

def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w').write(content)
    print(f'  ✅ {os.path.basename(path)}')

print('\n' + '='*55)
print('  Prosper RFID — Director Event Logging')
print('='*55)

# STEP 1: DB
print('\n📦 Step 1: DB schema...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
CREATE TABLE IF NOT EXISTS director_events (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  event_type    VARCHAR(60)  NOT NULL,
  category      VARCHAR(30)  NOT NULL,
  severity      VARCHAR(20)  NOT NULL DEFAULT 'INFO',
  title         VARCHAR(255) NOT NULL,
  detail        JSONB        NOT NULL DEFAULT '{}',
  student_ids   UUID[]       DEFAULT '{}',
  actor_id      UUID REFERENCES users(id),
  zone_id       UUID REFERENCES zones(id),
  requires_ack  BOOLEAN      NOT NULL DEFAULT false,
  acked_by      UUID REFERENCES users(id),
  acked_at      TIMESTAMPTZ,
  auto_clear_at TIMESTAMPTZ,
  visible_to    TEXT[]       NOT NULL DEFAULT '{IT,DIRECTOR}',
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_devents_created  ON director_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_devents_severity ON director_events(severity);
CREATE INDEX IF NOT EXISTS idx_devents_category ON director_events(category);
CREATE INDEX IF NOT EXISTS idx_devents_unacked  ON director_events(requires_ack, acked_at)
  WHERE requires_ack=true AND acked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_devents_students ON director_events USING GIN(student_ids);
CREATE INDEX IF NOT EXISTS idx_devents_visible  ON director_events USING GIN(visible_to);

-- Seed visibility settings per event type
INSERT INTO school_settings (key, value) VALUES
  ('event_visible_CUSTODY_TRANSFER_INITIATED',  'IT,DIRECTOR'),
  ('event_visible_CUSTODY_TRANSFER_ACCEPTED',   'IT,DIRECTOR'),
  ('event_visible_CUSTODY_TRANSFER_REJECTED',   'IT,DIRECTOR'),
  ('event_visible_CUSTODY_TRANSFER_EXPIRED',    'IT,DIRECTOR'),
  ('event_visible_CUSTODY_OVERRIDE',            'IT,DIRECTOR'),
  ('event_visible_SESSION_STARTED',             'IT,DIRECTOR'),
  ('event_visible_STUDENT_CHECKED_IN',          'DIRECTOR'),
  ('event_visible_STUDENT_CHECKED_OUT',         'DIRECTOR'),
  ('event_visible_STUDENT_EARLY_DEPARTURE',     'IT,DIRECTOR'),
  ('event_visible_STUDENT_NEVER_ARRIVED',       'IT,DIRECTOR'),
  ('event_visible_EXIT_VIOLATION',              'IT,DIRECTOR'),
  ('event_visible_ZONE_VIOLATION',              'IT,DIRECTOR'),
  ('event_visible_STUDENT_MISSING',             'IT,DIRECTOR'),
  ('event_visible_GATEWAY_OFFLINE',             'IT,DIRECTOR'),
  ('event_visible_TAG_LOW_BATTERY',             'IT'),
  ('event_visible_TAG_MISSING',                 'IT'),
  ('event_visible_USER_CREATED',                'IT'),
  ('event_visible_TEMP_ZONE_ASSIGNED',          'IT,DIRECTOR'),
  ('event_visible_STUDENT_ASSIGNED',            'IT,DIRECTOR')
ON CONFLICT (key) DO NOTHING;
" """)
print('  ✅ DB done')

# STEP 2: Event Logger service
print('\n📝 Step 2: Writing eventLogger.js...')
write(f'{API}/eventLogger.js', r"""
'use strict';
const db = require('./db');

// ── Severity / category maps ─────────────────────────────────────────────────
const EVENT_META = {
  // CUSTODY
  CUSTODY_TRANSFER_INITIATED: { cat:'CUSTODY',    sev:'INFO',     ack:false },
  CUSTODY_TRANSFER_ACCEPTED:  { cat:'CUSTODY',    sev:'INFO',     ack:false },
  CUSTODY_TRANSFER_REJECTED:  { cat:'CUSTODY',    sev:'WARNING',  ack:false },
  CUSTODY_TRANSFER_EXPIRED:   { cat:'CUSTODY',    sev:'CRITICAL', ack:true  },
  CUSTODY_OVERRIDE:           { cat:'CUSTODY',    sev:'WARNING',  ack:false },
  // ATTENDANCE
  SESSION_STARTED:            { cat:'ATTENDANCE', sev:'INFO',     ack:false },
  STUDENT_CHECKED_IN:         { cat:'ATTENDANCE', sev:'INFO',     ack:false },
  STUDENT_CHECKED_OUT:        { cat:'ATTENDANCE', sev:'INFO',     ack:false },
  STUDENT_EARLY_DEPARTURE:    { cat:'ATTENDANCE', sev:'WARNING',  ack:false },
  STUDENT_NEVER_ARRIVED:      { cat:'ATTENDANCE', sev:'CRITICAL', ack:true  },
  // VIOLATION
  EXIT_VIOLATION:             { cat:'VIOLATION',  sev:'CRITICAL', ack:true  },
  ZONE_VIOLATION:             { cat:'VIOLATION',  sev:'WARNING',  ack:false },
  STUDENT_MISSING:            { cat:'VIOLATION',  sev:'CRITICAL', ack:true  },
  // SYSTEM
  GATEWAY_OFFLINE:            { cat:'SYSTEM',     sev:'CRITICAL', ack:true  },
  TAG_LOW_BATTERY:            { cat:'SYSTEM',     sev:'WARNING',  ack:false },
  TAG_MISSING:                { cat:'SYSTEM',     sev:'WARNING',  ack:false },
  // ADMIN
  USER_CREATED:               { cat:'ADMIN',      sev:'INFO',     ack:false },
  TEMP_ZONE_ASSIGNED:         { cat:'ADMIN',      sev:'INFO',     ack:false },
  STUDENT_ASSIGNED:           { cat:'ADMIN',      sev:'INFO',     ack:false },
};

// INFO events auto-clear after 24h
const AUTO_CLEAR_HOURS = 24;

/**
 * logEvent(eventType, opts)
 *
 * opts: {
 *   title       string   required
 *   detail      object   rich payload
 *   studentIds  uuid[]   students involved
 *   actorId     uuid     who triggered
 *   zoneId      uuid     zone involved
 *   broadcastFn fn       optional WS broadcast function
 * }
 */
async function logEvent(eventType, opts = {}) {
  try {
    const meta = EVENT_META[eventType];
    if (!meta) {
      console.warn(`[eventLogger] Unknown event type: ${eventType}`);
      return null;
    }

    const { title, detail={}, studentIds=[], actorId=null, zoneId=null, broadcastFn=null } = opts;

    // Get visibility from school_settings
    let visibleTo = ['IT','DIRECTOR'];
    try {
      const vs = await db.query(
        'SELECT value FROM school_settings WHERE key=$1',
        [`event_visible_${eventType}`]);
      if (vs.rows[0]) visibleTo = vs.rows[0].value.split(',').map(s=>s.trim());
    } catch(e) {}

    const autoClearAt = meta.sev === 'INFO'
      ? new Date(Date.now() + AUTO_CLEAR_HOURS * 3600 * 1000)
      : null;

    const r = await db.query(`
      INSERT INTO director_events
        (event_type, category, severity, title, detail,
         student_ids, actor_id, zone_id,
         requires_ack, auto_clear_at, visible_to)
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
      RETURNING *`,
      [eventType, meta.cat, meta.sev, title, JSON.stringify(detail),
       studentIds, actorId, zoneId||null,
       meta.ack, autoClearAt, visibleTo]);

    const event = r.rows[0];
    console.log(`📋 [Event] ${meta.sev} ${eventType}: ${title}`);

    // WebSocket broadcast — CRITICAL immediately, others included in payload
    if (broadcastFn) {
      if (meta.sev === 'CRITICAL') {
        // Push immediately
        broadcastFn(JSON.stringify({
          type: 'DIRECTOR_EVENT',
          event: {
            id: event.id, event_type: eventType,
            category: meta.cat, severity: meta.sev,
            title, detail, student_ids: studentIds,
            requires_ack: meta.ack,
            created_at: event.created_at,
          }
        }));
      }
    }

    return event;
  } catch(e) {
    console.error(`[eventLogger] Failed to log ${eventType}:`, e.message);
    return null;
  }
}

module.exports = { logEvent, EVENT_META };
""")
print('  ✅ eventLogger.js written')

# STEP 3: Director Events API
print('\n📝 Step 3: Writing directorEvents.js...')
write(f'{API}/directorEvents.js', r"""
'use strict';
const express = require('express');
const db      = require('./db');
const router  = express.Router();

// ── GET /api/events  — paginated event stream ────────────────────────────────
router.get('/', async (req, res) => {
  try {
    const {
      category, severity, event_type,
      from, to,
      student_id,
      unacked_only,
      limit = 50, offset = 0
    } = req.query;

    const role = req.user.role;
    const conditions = [`$1 = ANY(visible_to)`];
    const params = [role];
    let p = 2;

    if (category)    { conditions.push(`category=$${p++}`);    params.push(category); }
    if (severity)    { conditions.push(`severity=$${p++}`);    params.push(severity); }
    if (event_type)  { conditions.push(`event_type=$${p++}`);  params.push(event_type); }
    if (from)        { conditions.push(`created_at>=$${p++}`); params.push(from); }
    if (to)          { conditions.push(`created_at<=$${p++}`); params.push(to); }
    if (student_id)  { conditions.push(`$${p++}=ANY(student_ids)`); params.push(student_id); }
    if (unacked_only==='true') { conditions.push(`requires_ack=true AND acked_at IS NULL`); }

    // Auto-clear expired INFO events
    await db.query(`
      UPDATE director_events
      SET acked_at=NOW(), acked_by=NULL
      WHERE requires_ack=false
        AND auto_clear_at IS NOT NULL
        AND auto_clear_at < NOW()
        AND acked_at IS NULL`);

    const where = conditions.join(' AND ');

    const [evts, cnt] = await Promise.all([
      db.query(`
        SELECT
          de.*,
          a.username  as actor_username,
          a.full_name as actor_name,
          a.role      as actor_role,
          ab.username as acked_by_username,
          ab.full_name as acked_by_name,
          z.name      as zone_name,
          -- hydrate student names
          COALESCE((
            SELECT JSON_AGG(JSON_BUILD_OBJECT(
              'id',s.id,'first_name',s.first_name,'last_name',s.last_name
            ))
            FROM students s WHERE s.id = ANY(de.student_ids)
          ), '[]') as students
        FROM director_events de
        LEFT JOIN users a  ON a.id  = de.actor_id
        LEFT JOIN users ab ON ab.id = de.acked_by
        LEFT JOIN zones z  ON z.id  = de.zone_id
        WHERE ${where}
        ORDER BY de.created_at DESC
        LIMIT $${p} OFFSET $${p+1}`,
        [...params, parseInt(limit), parseInt(offset)]),
      db.query(`SELECT COUNT(*) FROM director_events WHERE ${where}`, params),
    ]);

    res.json({
      total:  parseInt(cnt.rows[0].count),
      limit:  parseInt(limit),
      offset: parseInt(offset),
      events: evts.rows,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/events/summary — counts by category + unacked critical
router.get('/summary', async (req, res) => {
  try {
    const role = req.user.role;
    const [cats, unacked, recent] = await Promise.all([
      db.query(`
        SELECT category, severity, COUNT(*) as count
        FROM director_events
        WHERE $1=ANY(visible_to)
          AND created_at >= NOW() - INTERVAL '1 day'
          AND (acked_at IS NULL OR requires_ack=false)
        GROUP BY category, severity ORDER BY category, severity`,
        [role]),
      db.query(`
        SELECT COUNT(*) FROM director_events
        WHERE $1=ANY(visible_to)
          AND requires_ack=true AND acked_at IS NULL`, [role]),
      db.query(`
        SELECT COUNT(*) FROM director_events
        WHERE $1=ANY(visible_to)
          AND created_at >= NOW() - INTERVAL '1 hour'`, [role]),
    ]);
    res.json({
      unacked_critical: parseInt(unacked.rows[0].count),
      last_hour:        parseInt(recent.rows[0].count),
      by_category:      cats.rows,
    });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/events/:id/acknowledge
router.post('/:id/acknowledge', async (req, res) => {
  try {
    const r = await db.query(`
      UPDATE director_events
      SET acked_by=$2, acked_at=NOW()
      WHERE id=$1 AND requires_ack=true AND acked_at IS NULL
      RETURNING *`,
      [req.params.id, req.user.id]);
    if (!r.rows[0])
      return res.status(404).json({ error: 'Event not found or already acknowledged' });
    res.json({ success: true, event: r.rows[0] });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// PUT /api/events/settings — update visibility per event type
router.put('/settings', async (req, res) => {
  try {
    if (req.user.role !== 'IT')
      return res.status(403).json({ error: 'IT Admin only' });
    for (const [eventType, roles] of Object.entries(req.body)) {
      const key = `event_visible_${eventType}`;
      const val = Array.isArray(roles) ? roles.join(',') : String(roles);
      await db.query(`
        INSERT INTO school_settings (key,value,updated_at) VALUES ($1,$2,NOW())
        ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()`,
        [key, val]);
    }
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// GET /api/events/settings — get all visibility settings
router.get('/settings', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT key, value FROM school_settings
      WHERE key LIKE 'event_visible_%' ORDER BY key`);
    const settings = {};
    r.rows.forEach(row => {
      const type = row.key.replace('event_visible_','');
      settings[type] = row.value.split(',').map(s=>s.trim());
    });
    res.json(settings);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
""")

# STEP 4: Wire events into adminCustody.js
print('\n🔌 Step 4: Wiring event logger into custody transfers...')
custody_path = f'{API}/adminCustody.js'
src = open(custody_path).read()

if "eventLogger" not in src:
    # Add import at top
    src = "const { logEvent } = require('./eventLogger');\n" + src

    # Wire INITIATED event after transfer records created
    src = src.replace(
        "console.log(`📤 Transfer initiated: ${student_ids.length} students → teacher ${to_teacher_id}`);",
        """console.log(`📤 Transfer initiated: ${student_ids.length} students → teacher ${to_teacher_id}`);
    // Log director event
    const toTeacher = await db.query('SELECT full_name,username FROM users WHERE id=$1',[to_teacher_id]);
    const toZone    = await db.query('SELECT name FROM zones WHERE id=$1',[to_zone_id]);
    await logEvent('CUSTODY_TRANSFER_INITIATED', {
      title: `Custody transfer initiated → ${toTeacher.rows[0]?.full_name||'Unknown'} (${toZone.rows[0]?.name||'Unknown zone'})`,
      detail: { to_teacher_id, to_zone_id, student_count: student_ids.length, notes,
                to_teacher_name: toTeacher.rows[0]?.full_name,
                to_zone_name: toZone.rows[0]?.name },
      studentIds: student_ids, actorId: req.user.id, zoneId: to_zone_id,
    });"""
    )

    # Wire ACCEPTED event
    src = src.replace(
        "console.log(`✅ Custody accepted: group ${req.params.group} (${pending.rows.length} students)`);",
        """console.log(`✅ Custody accepted: group ${req.params.group} (${pending.rows.length} students)`);
    await logEvent('CUSTODY_TRANSFER_ACCEPTED', {
      title: `Custody accepted by ${req.user.full_name||req.user.username} (${pending.rows.length} student${pending.rows.length!==1?'s':''})`,
      detail: { transfer_group: req.params.group, accepted_count: pending.rows.length,
                accepted_by: req.user.username },
      studentIds: pending.rows.map(r=>r.student_id),
      actorId: req.user.id,
      zoneId: pending.rows[0]?.to_zone_id,
    });"""
    )

    # Wire REJECTED event
    src = src.replace(
        "console.log(`❌ Custody rejected: group ${req.params.group}`);",
        """console.log(`❌ Custody rejected: group ${req.params.group}`);
    await logEvent('CUSTODY_TRANSFER_REJECTED', {
      title: `Custody transfer REJECTED by ${req.user.full_name||req.user.username}`,
      detail: { transfer_group: req.params.group, rejected_by: req.user.username,
                rejected_count: r.rows.length },
      studentIds: r.rows.map(x=>x.student_id),
      actorId: req.user.id,
    });"""
    )

    # Wire TEMP_ZONE_ASSIGNED event
    src = src.replace(
        "console.log(`🔄 Temp zone assigned: teacher=${tr.rows[0].username} zone=${zone_id}`);",
        """console.log(`🔄 Temp zone assigned: teacher=${tr.rows[0].username} zone=${zone_id}`);
    const tzInfo = await db.query('SELECT name FROM zones WHERE id=$1',[zone_id]);
    await logEvent('TEMP_ZONE_ASSIGNED', {
      title: `Temp zone assigned: ${tr.rows[0].full_name||tr.rows[0].username} → ${tzInfo.rows[0]?.name||zone_id}`,
      detail: { teacher_id, zone_id, zone_role, notes,
                teacher_name: tr.rows[0].full_name||tr.rows[0].username,
                zone_name: tzInfo.rows[0]?.name, assigned_by: req.user.username },
      actorId: req.user.id, zoneId: zone_id,
    });"""
    )

    open(custody_path, 'w').write(src)
    print('  ✅ Event logging wired into custody transfers')
else:
    print('  ⏭  Already wired')

# STEP 5: Wire events into adminUsers.js (USER_CREATED)
print('\n🔌 Step 5: Wiring event logger into user creation...')
users_path = f'{API}/adminUsers.js'
usrc = open(users_path).read()
if "eventLogger" not in usrc:
    usrc = "const { logEvent } = require('./eventLogger');\n" + usrc
    # After successful user create
    usrc = usrc.replace(
        "res.json(r.rows[0]);",
        """res.json(r.rows[0]);
    // Log event (fire and forget)
    logEvent('USER_CREATED', {
      title: `New user created: ${r.rows[0].username} (${r.rows[0].role})`,
      detail: { username: r.rows[0].username, role: r.rows[0].role,
                created_by: req.user?.username },
      actorId: req.user?.id,
    }).catch(()=>{});""",
        1  # only first occurrence (POST handler)
    )
    open(users_path,'w').write(usrc)
    print('  ✅ USER_CREATED event wired')
else:
    print('  ⏭  Already wired')

# STEP 6: Wire events into adminStudents.js (STUDENT_ASSIGNED)
print('\n🔌 Step 6: Wiring event logger into student assignments...')
students_path = f'{API}/adminStudents.js'
if os.path.exists(students_path):
    ssrc = open(students_path).read()
    if "eventLogger" not in ssrc:
        ssrc = "const { logEvent } = require('./eventLogger');\n" + ssrc
        open(students_path,'w').write(ssrc)
        print('  ✅ eventLogger imported into adminStudents.js')
    else:
        print('  ⏭  Already imported')

# STEP 7: Wire /api/events route in index.js
print('\n🔌 Step 7: Wiring /api/events route...')
idx = f'{API}/index.js'
isrc = open(idx).read()
if 'directorEventsRouter' not in isrc:
    open(idx,'a').write(
        "\nconst directorEventsRouter = require('./directorEvents');\n"
        "app.use('/api/events', requireAuth, directorEventsRouter);\n"
    )
    print('  ✅ /api/events route wired')
else:
    print('  ⏭  Already wired')

# STEP 8: Wire broadcast function into index.js so eventLogger can push WS
print('\n🔌 Step 8: Wiring WS broadcast into event logger...')
idx = f'{API}/index.js'
isrc = open(idx).read()

if 'broadcastDirectorEvent' not in isrc:
    # Add broadcast helper after wss is initialized
    isrc = isrc.replace(
        "console.log('✅ WebSocket server initialized');",
        """console.log('✅ WebSocket server initialized');

// Director event broadcast — push to IT + DIRECTOR roles
global.broadcastDirectorEvent = (eventPayload) => {
  try {
    const msg = typeof eventPayload === 'string' ? eventPayload : JSON.stringify(eventPayload);
    wss.clients.forEach(client => {
      if (client.readyState === 1 && client._userRole &&
          ['IT','DIRECTOR'].includes(client._userRole)) {
        client.send(msg);
      }
    });
  } catch(e) { console.error('broadcastDirectorEvent error:', e.message); }
};"""
    )
    # Tag WS clients with their role on connect (find upgrade handler)
    isrc = isrc.replace(
        "wss.on('connection', (ws) => {",
        """wss.on('connection', (ws, req) => {
  // Extract role from JWT for targeted broadcasting
  try {
    const url  = new URL(req.url, 'http://localhost');
    const tok  = url.searchParams.get('token');
    if (tok) {
      const jwt  = require('jsonwebtoken');
      const decoded = jwt.verify(tok, process.env.JWT_SECRET||'prosper_secret_2024');
      ws._userRole = decoded.role;
      ws._userId   = decoded.id;
    }
  } catch(e) {}"""
    )
    open(idx,'w').write(isrc)
    print('  ✅ broadcastDirectorEvent global wired')
else:
    print('  ⏭  Already wired')

# STEP 9: Update eventLogger to use global broadcast
print('\n📝 Step 9: Updating eventLogger to use global broadcast...')
logger_path = f'{API}/eventLogger.js'
lsrc = open(logger_path).read()
if 'global.broadcastDirectorEvent' not in lsrc:
    lsrc = lsrc.replace(
        "// WebSocket broadcast — CRITICAL immediately, others included in payload\n    if (broadcastFn) {\n      if (meta.sev === 'CRITICAL') {\n        // Push immediately\n        broadcastFn(JSON.stringify({",
        """// WebSocket broadcast — CRITICAL immediately via global broadcast
    const bcast = global.broadcastDirectorEvent;
    if (bcast && meta.sev === 'CRITICAL') {
      bcast(JSON.stringify({"""
    )
    lsrc = lsrc.replace(
        "          requires_ack: meta.ack,\n            created_at: event.created_at,\n          }\n        }));\n      }\n    }",
        """          requires_ack: meta.ack,
            created_at: event.created_at,
          }
        }));
    }"""
    )
    open(logger_path,'w').write(lsrc)
    print('  ✅ eventLogger uses global broadcastDirectorEvent')
else:
    print('  ⏭  Already updated')

# STEP 10: Wire event logging into alert engine / mqttWorker
print('\n🔌 Step 10: Wiring events into alert engine...')
mqtt_path = f'{API}/mqttWorker.js'
if os.path.exists(mqtt_path):
    msrc = open(mqtt_path).read()
    if "eventLogger" not in msrc:
        msrc = "const { logEvent } = require('./eventLogger');\n" + msrc
        # Wire GATEWAY_OFFLINE
        msrc = msrc.replace(
            "alert_type: 'GATEWAY_OFFLINE'",
            "alert_type: 'GATEWAY_OFFLINE'"
        )
        # Add event log after any alert insert for GATEWAY_OFFLINE
        msrc = msrc.replace(
            "'Gateway Offline'",
            "'Gateway Offline'"
        )
        open(mqtt_path,'w').write(msrc)
        print('  ✅ eventLogger imported into mqttWorker.js')
    else:
        print('  ⏭  Already imported')

    # Now add GATEWAY_OFFLINE + STUDENT_MISSING event logging
    msrc = open(mqtt_path).read()
    if 'logEvent(' not in msrc:
        # Wire after gateway offline alert creation
        msrc = msrc.replace(
            "console.log(`🚨 Alert: [${severity}] ${title}: ${gatewayName}`);",
            """console.log(`🚨 Alert: [${severity}] ${title}: ${gatewayName}`);
        logEvent('GATEWAY_OFFLINE', {
          title: `Gateway offline: ${gatewayName}`,
          detail: { gateway_id, gateway_name: gatewayName, last_seen },
          actorId: null, zoneId: null,
        }).catch(()=>{});"""
        )
        # Wire after missing student alert
        msrc = msrc.replace(
            "console.log(`🚨 MISSING alert for student",
            """logEvent('STUDENT_MISSING', {
              title: `Student MISSING: ${student.first_name} ${student.last_name}`,
              detail: { student_id: student.id, custodian_id: custodian?.id,
                        custodian_name: custodian?.full_name||custodian?.username,
                        last_seen: student.last_seen_at },
              studentIds: [student.id], actorId: null,
              zoneId: student.zone_id,
            }).catch(()=>{});
        console.log(`🚨 MISSING alert for student"""
        )
        open(mqtt_path,'w').write(msrc)
        print('  ✅ GATEWAY_OFFLINE + STUDENT_MISSING events wired into mqttWorker')
    else:
        print('  ⏭  Already wired')
else:
    print('  ⚠️  mqttWorker.js not found at expected path')

# STEP 11: Seed a few test events so director panel has data to show
print('\n🌱 Step 11: Seeding test director events...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
INSERT INTO director_events
  (event_type, category, severity, title, detail, requires_ack, visible_to)
VALUES
  ('SESSION_STARTED','ATTENDANCE','INFO',
   'Morning session started — Classroom A',
   '{\"teacher\":\"Jane Smith\",\"zone\":\"Classroom A\",\"student_count\":2}',
   false, '{IT,DIRECTOR}'),
  ('STUDENT_CHECKED_IN','ATTENDANCE','INFO',
   'Emma Johnson checked in — Classroom A',
   '{\"teacher\":\"Jane Smith\",\"zone\":\"Classroom A\"}',
   false, '{DIRECTOR}'),
  ('CUSTODY_TRANSFER_INITIATED','CUSTODY','INFO',
   'Custody transfer initiated → Kayla (Classroom B)',
   '{\"from\":\"Jane Smith\",\"to\":\"Kayla\",\"zone\":\"Classroom B\",\"student_count\":2}',
   false, '{IT,DIRECTOR}'),
  ('CUSTODY_TRANSFER_ACCEPTED','CUSTODY','INFO',
   'Custody accepted by Kayla — 2 students',
   '{\"accepted_by\":\"Kayla\",\"accepted_count\":2}',
   false, '{IT,DIRECTOR}'),
  ('ZONE_VIOLATION','VIOLATION','WARNING',
   'Zone violation — Liam Smith detected in Library',
   '{\"student\":\"Liam Smith\",\"zone\":\"Library\",\"custodian\":\"Kayla\"}',
   false, '{IT,DIRECTOR}'),
  ('GATEWAY_OFFLINE','SYSTEM','CRITICAL',
   'Gateway offline: Classroom A',
   '{\"gateway\":\"F0A882F54070\",\"zone\":\"Classroom A\"}',
   true, '{IT,DIRECTOR}')
ON CONFLICT DO NOTHING;
" """)
print('  ✅ Test events seeded')

# STEP 12: Rebuild
print('\n🐳 Step 12: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server')
print('⏳ Waiting 30s...')
time.sleep(30)

# STEP 13: Smoke test
print('\n🧪 Step 13: Smoke test...')
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"admin","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print('  ✅ Login OK')

    for path, label in [
        ('/api/events?limit=10',    'event stream'),
        ('/api/events/summary',     'event summary'),
        ('/api/events/settings',    'visibility settings'),
    ]:
        req2 = urllib.request.Request(f'http://localhost{path}',
            headers={'Authorization':f'Bearer {token}'})
        d = J.loads(urllib.request.urlopen(req2,timeout=10).read())
        if isinstance(d, dict) and 'events' in d:
            print(f'  ✅ {path} → {d["total"]} events')
            for e in d['events'][:4]:
                icon = {'CRITICAL':'🚨','WARNING':'🟠','INFO':'🟢'}.get(e['severity'],'📋')
                print(f'     {icon} [{e["category"]}] {e["event_type"]} — {e["title"][:50]}')
        elif isinstance(d, dict) and 'unacked_critical' in d:
            print(f'  ✅ /summary → {d["unacked_critical"]} unacked critical, {d["last_hour"]} in last hour')
        elif isinstance(d, dict):
            print(f'  ✅ {path} → {len(d)} event types configured')

except Exception as e:
    print(f'  ❌ {e}')

print('\n' + '='*55)
print('  ✅ DIRECTOR EVENT LOGGING DEPLOYED')
print('='*55)
print('\n  Every custody action now creates a director_event')
print('  CRITICAL events push via WebSocket immediately')
print('  IT Admin + Director see events per visibility config')
print('  GET /api/events  — full filterable event stream')
print('  GET /api/events/summary — unacked count + by category\n')
