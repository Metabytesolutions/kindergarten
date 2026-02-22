'use strict';
const db           = require('./db');
const { logEvent } = require('./eventLogger');

const stationaryTracker = {};

async function onDetection({ studentId, gatewayId, zoneId, zoneType, rssi }) {
  try {
    const today = new Date().toISOString().split('T')[0];
    const ss = await db.query(`
      SELECT ss.id, ss.status, ss.checkout_initiated_at, ss.home_teacher_id,
        s.first_name, s.last_name, sc.current_teacher_id
      FROM student_sessions ss
      JOIN students s ON s.id=ss.student_id
      LEFT JOIN student_custody sc ON sc.student_id=ss.student_id
      WHERE ss.student_id=$1 AND ss.batch_date=$2
    `, [studentId, today]);

    if (!ss.rows[0]) return;
    const session = ss.rows[0];
    const status  = session.status;
    const name    = `${session.first_name} ${session.last_name}`;
    const isExit  = zoneType === 'EXIT';

    // CASE 1: EXIT while CHECKED IN → EXIT_VIOLATION
    if (isExit && status === 'ACCEPTED') {
      console.log(`🚨 EXIT_VIOLATION: ${name} at EXIT zone while checked in`);
      await logEvent('EXIT_VIOLATION', {
        title: `EXIT VIOLATION: ${name} at exit — not checked out`,
        detail: { student: name, zone_id: zoneId, custodian_id: session.current_teacher_id },
        studentIds: [studentId], actorId: null, zoneId,
      });
      if (global.broadcastDirectorEvent) {
        global.broadcastDirectorEvent(JSON.stringify({
          type: 'EXIT_VIOLATION', studentId, studentName: name,
          zoneId, teacherId: session.current_teacher_id,
        }));
      }
      return;
    }

    // CASE 2: EXIT while CHECKOUT_PENDING → confirm checkout
    if (isExit && status === 'CHECKOUT_PENDING') {
      console.log(`✅ Checkout confirmed: ${name} passed EXIT zone`);
      await db.query(`
        UPDATE student_sessions
        SET status='CHECKED_OUT', exit_zone_detected_at=NOW(), checkout_confirmed_at=NOW()
        WHERE student_id=$1 AND batch_date=$2
      `, [studentId, today]);
      await db.query('DELETE FROM student_custody WHERE student_id=$1', [studentId]);
      await db.query(`
        INSERT INTO checkout_tracking
          (student_id, session_id, detected_zone_id, detected_gateway_id, zone_type, rssi)
        VALUES ($1,$2,$3,$4,$5,$6)
      `, [studentId, session.id, zoneId, gatewayId, zoneType, rssi]);
      await logEvent('STUDENT_CHECKED_OUT', {
        title: `${name} checked out — EXIT zone confirmed ✅`,
        detail: { student: name, confirmed_via: 'BLE EXIT detection',
                  checkout_initiated: session.checkout_initiated_at },
        studentIds: [studentId], actorId: null, zoneId,
      });
      delete stationaryTracker[studentId];
      if (global.broadcastDirectorEvent) {
        global.broadcastDirectorEvent(JSON.stringify({
          type: 'CHECKOUT_CONFIRMED', studentId, studentName: name,
          zoneId, teacherId: session.home_teacher_id,
        }));
      }
      return;
    }

    // CASE 3: Non-exit while CHECKOUT_PENDING → breadcrumb + escalation
    if (!isExit && status === 'CHECKOUT_PENDING') {
      const now = new Date();
      await db.query(`
        INSERT INTO checkout_tracking
          (student_id, session_id, detected_zone_id, detected_gateway_id, zone_type, rssi)
        VALUES ($1,$2,$3,$4,$5,$6)
      `, [studentId, session.id, zoneId, gatewayId, zoneType, rssi]);

      const zn = await db.query('SELECT name FROM zones WHERE id=$1', [zoneId]);
      const zoneName = zn.rows[0]?.name || 'Unknown zone';
      console.log(`📍 Breadcrumb: ${name} in ${zoneName} (checkout pending)`);

      const tracker = stationaryTracker[studentId];
      if (!tracker || tracker.zone_id !== zoneId) {
        stationaryTracker[studentId] = { zone_id: zoneId, zone_name: zoneName,
          first_seen_at: now, warned5: false, warned15: false, last_log_at: null };
      } else {
        const mins = (now - tracker.first_seen_at) / 60000;

        if (mins >= 5 && !tracker.warned5) {
          tracker.warned5 = true;
          await logEvent('CHECKOUT_ZONE_WARNING', {
            title: `⚠️ ${name} in ${zoneName} for ${Math.floor(mins)}min — checkout pending`,
            detail: { student: name, zone: zoneName, minutes: Math.floor(mins) },
            studentIds: [studentId], actorId: null, zoneId,
          });
        }
        if (mins >= 15 && !tracker.warned15) {
          tracker.warned15 = true;
          await logEvent('STUDENT_MISSING', {
            title: `🚨 ${name} checkout ${Math.floor(mins)}min ago — stuck in ${zoneName}, never reached EXIT`,
            detail: { student: name, zone: zoneName, minutes: Math.floor(mins) },
            studentIds: [studentId], actorId: null, zoneId,
          });
        }
      }

      // Log breadcrumb event (throttled 2min)
      const tr = stationaryTracker[studentId];
      if (!tr?.last_log_at || (now - tr.last_log_at) > 120000) {
        if (tr) tr.last_log_at = now;
        await logEvent('CHECKOUT_TRACKING', {
          title: `📍 ${name} spotted in ${zoneName} — checkout pending`,
          detail: { student: name, zone: zoneName, rssi },
          studentIds: [studentId], actorId: null, zoneId,
        });
      }
      return;
    }

    // CASE 4: Non-exit detection AFTER checkout confirmed → re-entry
    if (!isExit && status === 'CHECKED_OUT') {
      const zn = await db.query('SELECT name FROM zones WHERE id=$1', [zoneId]);
      const zoneName = zn.rows[0]?.name || 'Unknown zone';
      await logEvent('RE_ENTRY_VIOLATION', {
        title: `⚠️ Re-entry: ${name} detected in ${zoneName} after checkout`,
        detail: { student: name, zone: zoneName, rssi },
        studentIds: [studentId], actorId: null, zoneId,
      });
    }

  } catch(e) {
    console.error(`[checkoutTracker] Error:`, e.message);
  }
}

async function scheduleMissingAlert(studentId, studentName, homeTeacherId, timeoutMinutes=15) {
  setTimeout(async () => {
    try {
      const today = new Date().toISOString().split('T')[0];
      const check = await db.query(
        'SELECT status FROM student_sessions WHERE student_id=$1 AND batch_date=$2',
        [studentId, today]);
      if (check.rows[0]?.status === 'CHECKOUT_PENDING') {
        const trail = await db.query(`
          SELECT z.name as zone_name, ct.detected_at
          FROM checkout_tracking ct
          LEFT JOIN zones z ON z.id=ct.detected_zone_id
          WHERE ct.student_id=$1 AND ct.detected_at > NOW() - INTERVAL '1 hour'
          ORDER BY ct.detected_at DESC LIMIT 5
        `, [studentId]);
        const breadcrumbs = trail.rows
          .map(r=>`${r.zone_name} @ ${new Date(r.detected_at).toLocaleTimeString()}`)
          .join(' → ') || 'No detections';
        await logEvent('STUDENT_MISSING', {
          title: `🚨 CRITICAL: ${studentName} never reached EXIT zone after ${timeoutMinutes}min`,
          detail: { student: studentName, timeout_minutes: timeoutMinutes,
                    last_known_path: breadcrumbs, home_teacher_id: homeTeacherId },
          studentIds: [studentId], actorId: null,
        });
        delete stationaryTracker[studentId];
      }
    } catch(e) { console.error('[scheduleMissingAlert]', e.message); }
  }, timeoutMinutes * 60 * 1000);
}

module.exports = { onDetection, scheduleMissingAlert };
