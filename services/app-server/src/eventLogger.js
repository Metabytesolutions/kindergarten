
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
  CHECKOUT_TRACKING:     { cat:'ATTENDANCE', sev:'INFO',    ack:false },
  CHECKOUT_ZONE_WARNING: { cat:'VIOLATION',  sev:'WARNING', ack:false },
  RE_ENTRY_VIOLATION:    { cat:'VIOLATION',  sev:'WARNING', ack:true  },
  STUDENT_ABSENT:    { cat:'ATTENDANCE', sev:'INFO',    ack:false },
  MORNING_REMINDER: { cat:'ATTENDANCE', sev:'WARNING', ack:false },
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

    // WebSocket broadcast — CRITICAL immediately via global broadcast
    const bcast = global.broadcastDirectorEvent;
    if (bcast && meta.sev === 'CRITICAL') {
      bcast(JSON.stringify({
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

    return event;
  } catch(e) {
    console.error(`[eventLogger] Failed to log ${eventType}:`, e.message);
    return null;
  }
}

module.exports = { logEvent, EVENT_META };
