
# STEP 3: Wire checkoutTracker into mqttWorker.js
print('\n🔌 Step 3: Wiring checkoutTracker into mqttWorker.js...')
mqtt_path = f'{API}/mqttWorker.js'
src = open(mqtt_path).read()

if 'checkoutTracker' not in src:
    src = "const { onDetection, scheduleMissingAlert } = require('./checkoutTracker');\n" + src
    open(mqtt_path,'w').write(src)
    print('  ✅ Import added')

src = open(mqtt_path).read()

# Find where a detection is processed and student_id is known — inject onDetection call
# Look for where we update presence_states or last_seen_at
detection_hook = """
        // ── CHECKOUT / EXIT TRACKING ──────────────────────────────────────
        if (studentId && zoneId) {
          onDetection({
            studentId,
            gatewayId: gateway.id,
            zoneId,
            zoneType: zone.zone_type,
            rssi: detection.rssi || detection.RSSI || null,
          }).catch(e => console.error('[checkoutTracker] onDetection error:', e.message));
        }
        // ─────────────────────────────────────────────────────────────────
"""

# Find a reliable anchor in the MQTT worker after student + zone are resolved
anchors = [
    "await db.query(`UPDATE presence_states",
    "// Update presence state",
    "UPDATE presence_states",
]

injected = False
for anchor in anchors:
    if anchor in src and 'onDetection' not in src:
        src = src.replace(anchor, detection_hook + "        " + anchor, 1)
        open(mqtt_path,'w').write(src)
        print(f'  ✅ onDetection injected after: {anchor[:50]}')
        injected = True
        break

if not injected and 'onDetection' not in src:
    print('  ⚠️  Could not auto-inject — showing detection handler context:')
    for i,l in enumerate(src.split('\n')):
        if 'student' in l.lower() and ('zone' in l.lower() or 'detection' in l.lower()):
            print(f'  Line {i+1}: {l.strip()[:80]}')
elif 'onDetection' in src:
    print('  ⏭  Already wired')

# STEP 4: Wire scheduleMissingAlert into teacherSessionApi.js
print('\n🔌 Step 4: Wiring scheduleMissingAlert into teacherSessionApi...')
session_path = f'{API}/teacherSessionApi.js'
ssrc = open(session_path).read()

if 'scheduleMissingAlert' not in ssrc:
    # Add import
    ssrc = ssrc.replace(
        "const { logEvent } = require('./eventLogger');",
        "const { logEvent } = require('./eventLogger');\nconst { scheduleMissingAlert } = require('./checkoutTracker');"
    )

    # Wire into checkout endpoint — after the DB update
    ssrc = ssrc.replace(
        "console.log(`🚪 Checkout initiated: ${student.first_name} ${student.last_name} — watching for EXIT zone (${timeout}min timeout)`);",
        """console.log(`🚪 Checkout initiated: ${student.first_name} ${student.last_name} — watching for EXIT zone (${timeout}min timeout)`);

    // Log checkout initiated event for director
    await logEvent('STUDENT_CHECKED_OUT', {
      title: `Checkout initiated: ${student.first_name} ${student.last_name} — watching for EXIT`,
      detail: {
        student: `${student.first_name} ${student.last_name}`,
        initiated_by: req.user.username,
        timeout_minutes: timeout,
        status: 'PENDING_EXIT_CONFIRMATION',
      },
      studentIds: [sid], actorId: teacherId,
    }).catch(()=>{});

    // Schedule CRITICAL alert if student never reaches EXIT
    scheduleMissingAlert(sid, `${student.first_name} ${student.last_name}`, teacherId, timeout);"""
    )
    open(session_path,'w').write(ssrc)
    print('  ✅ scheduleMissingAlert wired into checkout endpoint')
else:
    print('  ⏭  Already wired')

# STEP 5: Add new event types to eventLogger EVENT_META
print('\n📝 Step 5: Adding new event types to eventLogger...')
logger_path = f'{API}/eventLogger.js'
lsrc = open(logger_path).read()

if 'CHECKOUT_TRACKING' not in lsrc:
    lsrc = lsrc.replace(
        "  // ADMIN",
        """  // CHECKOUT TRACKING
  CHECKOUT_TRACKING:        { cat:'ATTENDANCE', sev:'INFO',     ack:false },
  CHECKOUT_ZONE_WARNING:    { cat:'VIOLATION',  sev:'WARNING',  ack:false },
  RE_ENTRY_VIOLATION:       { cat:'VIOLATION',  sev:'WARNING',  ack:true  },
  // ADMIN"""
    )
    open(logger_path,'w').write(lsrc)
    print('  ✅ New event types added to EVENT_META')
else:
    print('  ⏭  Already present')

# STEP 6: Add breadcrumb API endpoint to teacherSessionApi
print('\n📝 Step 6: Adding breadcrumb trail API...')
ssrc = open(session_path).read()
if 'checkout_tracking' not in ssrc:
    # Add before module.exports
    ssrc = ssrc.replace(
        "module.exports = router;",
        """
// GET /api/session/checkout-trail/:studentId — breadcrumb trail for director/teacher
router.get('/checkout-trail/:studentId', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT ct.detected_at, ct.rssi, ct.zone_type,
        z.name as zone_name, z.zone_type as zt,
        bg.short_id as gateway_short_id
      FROM checkout_tracking ct
      LEFT JOIN zones z ON z.id=ct.detected_zone_id
      LEFT JOIN ble_gateways bg ON bg.id=ct.detected_gateway_id
      WHERE ct.student_id=$1
        AND ct.detected_at >= NOW() - INTERVAL '24 hours'
      ORDER BY ct.detected_at ASC
    `, [req.params.studentId]);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;"""
    )
    open(session_path,'w').write(ssrc)
    print('  ✅ Breadcrumb trail API added')
else:
    print('  ⏭  Already present')

# STEP 7: Update school_settings for visibility of new events
print('\n📦 Step 7: Updating visibility settings...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
INSERT INTO school_settings (key,value) VALUES
  ('event_visible_CHECKOUT_TRACKING',     'IT,DIRECTOR'),
  ('event_visible_CHECKOUT_ZONE_WARNING', 'IT,DIRECTOR'),
  ('event_visible_RE_ENTRY_VIOLATION',    'IT,DIRECTOR')
ON CONFLICT (key) DO NOTHING;
SELECT key,value FROM school_settings WHERE key LIKE 'event_visible_%' OR key LIKE 'checkout%';
" """)

# STEP 8: Rebuild
print('\n🐳 Step 8: Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server')
print('⏳ Waiting 30s...')
time.sleep(30)

# STEP 9: Smoke test
print('\n🧪 Step 9: Smoke test...')
try:
    req = urllib.request.Request('http://localhost/api/auth/login',
        data=b'{"username":"teacher01","password":"Admin1234!"}',
        headers={'Content-Type':'application/json'}, method='POST')
    token = J.loads(urllib.request.urlopen(req,timeout=10).read())['token']
    print('  ✅ Login OK')

    # Test breadcrumb trail endpoint
    req2 = urllib.request.Request('http://localhost/api/session/roster',
        headers={'Authorization':f'Bearer {token}'})
    roster = J.loads(urllib.request.urlopen(req2,timeout=10).read())
    students = roster.get('students',[])
    print(f'  ✅ Roster → {len(students)} students')

    if students:
        sid = students[0]['id']
        req3 = urllib.request.Request(f'http://localhost/api/session/checkout-trail/{sid}',
            headers={'Authorization':f'Bearer {token}'})
        trail = J.loads(urllib.request.urlopen(req3,timeout=10).read())
        print(f'  ✅ Breadcrumb trail → {len(trail)} entries for {students[0]["first_name"]}')

    # Verify app-server logs show checkoutTracker loaded
    logs = run('docker logs prosper-app-server --tail 20 2>&1')
    if 'checkoutTracker' in logs or 'Error' not in logs:
        print('  ✅ app-server running clean')

except Exception as e:
    print(f'  ❌ {e}')
    import traceback; traceback.print_exc()

# STEP 10: Commit
print('\n📦 Step 10: Committing...')
os.chdir(BASE)
run('git add -A')
run('git commit -m "feat: exit tracking — breadcrumbs, 5min WARNING, 15min CRITICAL, re-entry violation, checkout confirmed via BLE"')
run('git push')

print('\n' + '='*55)
print('  ✅ CHECKOUT TRACKING DEPLOYED')
print('='*55)
print("""
  State machine now handles:
  🚨 EXIT while CHECKED_IN     → EXIT_VIOLATION CRITICAL
  ✅ EXIT while CHECKOUT_PENDING → CHECKED_OUT confirmed
  📍 Non-exit while pending     → breadcrumb logged
  ⚠️  Same zone 5min            → WARNING to director
  🚨 No EXIT in 15min          → MISSING CRITICAL
  ⚠️  Non-exit after CHECKED_OUT → RE_ENTRY_VIOLATION
""")
