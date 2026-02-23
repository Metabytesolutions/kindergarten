import os, subprocess, sys, time

BASE = os.path.expanduser('~/prosper-platform')
UI   = f'{BASE}/services/react-ui/src'
APP  = f'{BASE}/services/app-server/src'

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=BASE)
    out = (r.stdout + r.stderr).strip()
    if out: print(f'  {out[:300]}')
    return r.returncode == 0

def read(p): return open(p).read()
def write(p, s): open(p,'w').write(s); print(f'  ✅ {os.path.basename(p)}')

print('\n=== Absent Feature Deploy ===\n')

# Preflight
tv = read(f'{UI}/TeacherView.jsx')
dp = read(f'{UI}/DirectorPortal.jsx')
if tv.count("ABSENT:           {color:'#4A5568'") > 0 or dp.count("{label:'ABSENT'") > 0:
    print('❌ Files already patched — restore from git first:')
    print('   git checkout b64100d -- services/react-ui/src/TeacherView.jsx')
    print('   git checkout b64100d -- services/react-ui/src/DirectorPortal.jsx')
    print('   git checkout b64100d -- services/app-server/src/directorApi.js')
    sys.exit(1)
print('✅ Preflight passed')

# Step 1: DB
print('\n[1] Database constraint...')
run("""docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
  ALTER TABLE student_sessions DROP CONSTRAINT IF EXISTS student_sessions_status_check;
  ALTER TABLE student_sessions ADD CONSTRAINT student_sessions_status_check
    CHECK (status IN ('EXPECTED','ACCEPTED','ABSENT','CHECKOUT_PENDING','CHECKED_OUT'));
  SELECT 'done' as result;" """)

# Step 2: eventLogger
print('\n[2] eventLogger.js...')
p = f'{APP}/eventLogger.js'; s = read(p)
if 'STUDENT_ABSENT' not in s:
    write(p, s.replace(
        "  STUDENT_CHECKED_IN:",
        "  STUDENT_ABSENT:        { cat:'ATTENDANCE', sev:'INFO', ack:false },\n  STUDENT_CHECKED_IN:"))

# Step 3: teacherSessionApi
print('\n[3] teacherSessionApi.js...')
p = f'{APP}/teacherSessionApi.js'; s = read(p)
if 'mark-absent' not in s:
    endpoints = """
// POST /api/session/mark-absent/:studentId
router.post('/mark-absent/:studentId', async (req, res) => {
  try {
    const sid = req.params.studentId;
    const teacherId = req.user.id;
    const today = new Date().toISOString().split('T')[0];
    const sv = await db.query('SELECT first_name, last_name FROM students WHERE id=$1', [sid]);
    if (!sv.rows[0]) return res.status(404).json({ error: 'Student not found' });
    const name = sv.rows[0].first_name + ' ' + sv.rows[0].last_name;
    await db.query(`
      INSERT INTO student_sessions (student_id,home_teacher_id,batch_date,status)
      VALUES ($1,$2,$3,'ABSENT')
      ON CONFLICT (student_id,batch_date) DO UPDATE SET status='ABSENT'
    `, [sid, teacherId, today]);
    await logEvent('STUDENT_ABSENT', {
      title: `${name} marked absent by ${req.user.username}`,
      detail: { student: name, marked_by: req.user.username },
      studentIds: [sid], actorId: teacherId,
    }).catch(() => {});
    res.json({ success: true, status: 'ABSENT' });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/session/undo-absent/:studentId
router.post('/undo-absent/:studentId', async (req, res) => {
  try {
    const sid = req.params.studentId;
    const today = new Date().toISOString().split('T')[0];
    await db.query(
      "UPDATE student_sessions SET status='EXPECTED' WHERE student_id=$1 AND batch_date=$2 AND status='ABSENT'",
      [sid, today]);
    res.json({ success: true, status: 'EXPECTED' });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

"""
    write(p, s.replace('module.exports = router;', endpoints + 'module.exports = router;'))

# Step 4: directorApi
print('\n[4] directorApi.js...')
p = f'{APP}/directorApi.js'; s = read(p)
if "absent:" not in s:
    # Add session_status join if missing
    if 'ss.status as session_status' not in s:
        s = s.replace(
            'ps.state as presence_state',
            'ps.state as presence_state,\n          ss.status as session_status')
        s = s.replace(
            'LEFT JOIN presence_states ps ON ps.student_id=s.id',
            'LEFT JOIN presence_states ps ON ps.student_id=s.id\n        LEFT JOIN student_sessions ss ON ss.student_id=s.id AND ss.batch_date=CURRENT_DATE')
    # Add absent to summary
    s = s.replace(
        "        present:  states.filter(s=>",
        "        absent:   students.rows.filter(s=>s.session_status==='ABSENT').length,\n        present:  states.filter(s=>")
    write(p, s)

# Step 5: TeacherView
print('\n[5] TeacherView.jsx...')
p = f'{UI}/TeacherView.jsx'; s = read(p)

# 5a statusConfig
s = s.replace(
    "    EXPECTED:         {color:'#4A5568', label:'Expected',         bg:'#1A1A2E'},",
    "    EXPECTED:         {color:'#4A5568', label:'Expected',         bg:'#1A1A2E'},\n    ABSENT:           {color:'#4A5568', label:'Absent',           bg:'#0D0D0D'},")

# 5b opacity — single line replacing existing
s = s.replace(
    "    opacity:sess==='CHECKED_OUT'?0.5:1,marginBottom:6}}>",
    "    opacity:(sess==='ABSENT'||sess==='CHECKED_OUT')?0.5:1,marginBottom:6}}>")

# 5c border for absent
s = s.replace(
    "    border:`1.5px solid ${sess==='EXPECTED'?C.border:sc.color+'44'}`,",
    "    border:`1.5px solid ${sess==='ABSENT'?'#2A2A2A':sess==='EXPECTED'?C.border:sc.color+'44'}`,")

# 5d buttons
s = s.replace(
    "      {sess==='EXPECTED'&&\n        <Btn small color={C.green} onClick={()=>onAction('accept',student)}>✓ Accept</Btn>}",
    "      {sess==='EXPECTED'&&<>\n        <Btn small color={C.green} onClick={()=>onAction('accept',student)}>✓ Accept</Btn>\n        <Btn small color='#4A5568' onClick={()=>onAction('mark-absent',student)}>✗ Absent</Btn>\n      </>}\n      {sess==='ABSENT'&&\n        <Btn small color='#E67E22' onClick={()=>onAction('undo-absent',student)}>↩ Undo</Btn>}")

# 5e functions
fns = """  const doMarkAbsent = async (student) => {
    try {
      await fetch(`/api/session/mark-absent/${student.id}`,{method:'POST',headers:auth(token)});
      await loadRoster();
    } catch(e) { console.error(e); }
  };
  const doUndoAbsent = async (student) => {
    try {
      await fetch(`/api/session/undo-absent/${student.id}`,{method:'POST',headers:auth(token)});
      await loadRoster();
    } catch(e) { console.error(e); }
  };
"""
s = s.replace('  const doAccept', fns + '  const doAccept', 1)

# 5f action handler
s = s.replace(
    "    if(action==='accept')",
    "    if(action==='mark-absent'){doMarkAbsent(student);return;}\n    if(action==='undo-absent'){doUndoAbsent(student);return;}\n    if(action==='accept')", 1)

# 5g filter
s = s.replace(
    "s.current_teacher_id===teacher.id||s.session_status==='EXPECTED')",
    "s.current_teacher_id===teacher.id||s.session_status==='EXPECTED'||s.session_status==='ABSENT')")

# 5h absent section
s = s.replace(
    "        {myStudents.filter(s=>s.session_status==='EXPECTED').length>0&&<>",
    """        {myStudents.filter(s=>s.session_status==='ABSENT').length>0&&<>
          <div style={{fontSize:11,fontWeight:700,color:'#4A5568',
            textTransform:'uppercase',letterSpacing:'0.08em',
            padding:'8px 0 4px',marginTop:8,borderTop:'1px solid #1E3A5F'}}>
            ⚫ Absent ({myStudents.filter(s=>s.session_status==='ABSENT').length})
          </div>
          {myStudents.filter(s=>s.session_status==='ABSENT')
            .map(s=><StudentRow key={s.id} student={s} onAction={onAction} teacher={teacher}/>)}
        </>}
        {myStudents.filter(s=>s.session_status==='EXPECTED').length>0&&<>""")

write(p, s)

# Duplicate check
checks = [
    ('ABSENT in statusConfig', s.count("ABSENT:           {color:'#4A5568'"), 1),
    ('opacity:sess lines',     s.count("opacity:sess==="),                    1),
    ('doMarkAbsent count',     s.count("const doMarkAbsent"),                 1),
    ('doUndoAbsent count',     s.count("const doUndoAbsent"),                 1),
]
ok = True
for label, count, exp in checks:
    status = '✅' if count==exp else '❌'
    if count!=exp: ok=False
    print(f'  {status} {label}: {count}')
if not ok:
    print('\n❌ Duplicate check failed — aborting before rebuild'); sys.exit(1)

# Step 6: DirectorPortal
print('\n[6] DirectorPortal.jsx...')
p = f'{UI}/DirectorPortal.jsx'; s = read(p)
s = s.replace(
    "ROAMING:C.blue,MISSING:C.red,UNKNOWN:'#4A5568',EXIT_CONFIRMED:C.red};",
    "ROAMING:C.blue,MISSING:C.red,UNKNOWN:'#4A5568',EXIT_CONFIRMED:C.red,ABSENT:'#4A5568'};")
s = s.replace(
    "    {label:'PRESENT', value:summary?.present||0,  color:C.green},",
    "    {label:'PRESENT', value:summary?.present||0,  color:C.green},\n    {label:'ABSENT',  value:summary?.absent||0,   color:'#4A5568'},")
write(p, s)

# Step 7: Rebuild
print('\n[7] Rebuilding...')
os.chdir(BASE)
run('docker compose up -d --build app-server react-ui')
print('⏳ Waiting 40s...'); time.sleep(40)

r = subprocess.run('docker logs prosper-ui --tail 15 2>&1',
    shell=True, capture_output=True, text=True)
out = r.stdout + r.stderr
errors = [l for l in out.split('\n') if 'Duplicate' in l or 'Error' in l]
if errors:
    print('❌ Build errors:')
    for e in errors: print(f'  {e}')
    sys.exit(1)
print('✅ Build clean')

# Step 8: Commit
run('git add -A')
run('git commit -m "feat: absent feature — teacher mark/undo, director summary bar"')
run('git push')

print('\n=== ✅ ABSENT FEATURE DEPLOYED ===')
