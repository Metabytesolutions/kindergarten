const { logEvent } = require('./eventLogger');
const express = require('express');
const db      = require('./db');
const bcrypt  = require('bcrypt');
const router  = express.Router();

// GET /api/admin/users
router.get('/', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT u.id, u.username, u.email, u.role, u.full_name, u.phone,
             u.is_active, u.created_at, u.last_login_at, u.updated_at,
             z.name as zone_name, u.zone_id
      FROM users u
      LEFT JOIN zones z ON z.id = u.zone_id
      ORDER BY u.role, u.username
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/users
router.post('/', async (req, res) => {
  try {
    const { username, email, password, role, full_name, phone, zone_id } = req.body;
    if (!username || !email || !password || !role)
      return res.status(400).json({ error: 'username, email, password, role required' });
    if (!['TEACHER','DIRECTOR','IT'].includes(role))
      return res.status(400).json({ error: 'Invalid role' });
    const hash = await bcrypt.hash(password, 10);
    const r = await db.query(
      `INSERT INTO users (username,email,password_hash,role,full_name,phone,zone_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id,username,email,role,full_name,is_active,created_at`,
      [username, email, hash, role, full_name||null, phone||null, zone_id||null]
    );
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'USER_CREATED','user',$3)`,
      [req.user.id, req.user.role, r.rows[0].id]);
    console.log(`✅ User created: ${username} (${role})`);
    res.json(r.rows[0]);
    // Log event (fire and forget)
    logEvent('USER_CREATED', {
      title: `New user created: ${r.rows[0].username} (${r.rows[0].role})`,
      detail: { username: r.rows[0].username, role: r.rows[0].role,
                created_by: req.user?.username },
      actorId: req.user?.id,
    }).catch(()=>{});
  } catch(e) {
    if (e.code === '23505') return res.status(400).json({ error: 'Username or email already exists' });
    res.status(500).json({ error: e.message });
  }
});

// PUT /api/admin/users/:id
router.put('/:id', async (req, res) => {
  try {
    const { full_name, email, phone, role, zone_id, primary_zone_id, teacher_type, is_active } = req.body;
    const r = await db.query(
      `UPDATE users SET full_name=COALESCE($2,full_name), email=COALESCE($3,email),
       phone=$4, role=COALESCE($5,role), zone_id=COALESCE($6,zone_id), primary_zone_id=COALESCE($6,zone_id),
       is_active=COALESCE($7,is_active), teacher_type=COALESCE($8,teacher_type), updated_at=NOW()
       WHERE id=$1 RETURNING id,username,email,role,full_name,is_active,zone_id`,
      [req.params.id, full_name, email, phone||null, role, zone_id||null, is_active, teacher_type||null]
    );
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// POST /api/admin/users/:id/reset-password
router.post('/:id/reset-password', async (req, res) => {
  try {
    const { password } = req.body;
    if (!password || password.length < 8)
      return res.status(400).json({ error: 'Password must be at least 8 characters' });
    const hash = await bcrypt.hash(password, 10);
    await db.query('UPDATE users SET password_hash=$2, updated_at=NOW() WHERE id=$1', [req.params.id, hash]);
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'PASSWORD_RESET','user',$3)`,
      [req.user.id, req.user.role, req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

// DELETE /api/admin/users/:id  (soft deactivate)
router.delete('/:id', async (req, res) => {
  try {
    if (req.params.id === req.user.id)
      return res.status(400).json({ error: 'Cannot deactivate your own account' });
    await db.query('UPDATE users SET is_active=false, updated_at=NOW() WHERE id=$1', [req.params.id]);
    res.json({ success: true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
