const express = require('express');
const db      = require('./db');
const router  = express.Router();

const ICONS = {CLASSROOM:'🏫',CORRIDOR:'🚶',ENTRANCE:'🚪',EXIT:'🚨',LOBBY:'🏛️',OUTDOOR:'🌳',NURSE:'🏥',GYM:'🏋️',OFFICE:'💼',HALLWAY:'🚶',CAFETERIA:'🍽️',LIBRARY:'📚'};

router.get('/', async (req, res) => {
  try {
    const r = await db.query(`
      SELECT z.*, COUNT(DISTINCT g.id) as gateway_count,
        ARRAY_AGG(DISTINCT g.label) FILTER (WHERE g.label IS NOT NULL) as gateways
      FROM zones z
      LEFT JOIN ble_gateways g ON g.zone_id=z.id AND g.is_active=true
      WHERE z.is_active=true
      GROUP BY z.id ORDER BY z.zone_type, z.name
    `);
    res.json(r.rows);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.get('/types', async (req, res) => {
  try {
    const r = await db.query(`SELECT enumlabel as value FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='zone_type' ORDER BY enumsortorder`);
    res.json(r.rows.map(row => ({ value:row.value, icon:ICONS[row.value]||'📍', label:row.value.charAt(0)+row.value.slice(1).toLowerCase() })));
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.post('/', async (req, res) => {
  try {
    const { name, zone_type, description, floor } = req.body;
    if (!name || !zone_type) return res.status(400).json({ error: 'name and zone_type required' });
    const r = await db.query(
      `INSERT INTO zones (name,zone_type,description,floor) VALUES ($1,$2,$3,$4) RETURNING *`,
      [name, zone_type, description||null, floor||'1']
    );
    await db.query(`INSERT INTO audit_log (actor_id,actor_role,action,entity_type,entity_id) VALUES ($1,$2,'ZONE_CREATED','zone',$3)`,
      [req.user.id, req.user.role, r.rows[0].id]);
    console.log('✅ Zone created:', name);
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.put('/:id', async (req, res) => {
  try {
    const { name, zone_type, description, floor } = req.body;
    const r = await db.query(
      `UPDATE zones SET name=COALESCE($2,name),zone_type=COALESCE($3,zone_type),description=$4,floor=COALESCE($5,floor),updated_at=NOW() WHERE id=$1 RETURNING *`,
      [req.params.id, name, zone_type, description||null, floor]
    );
    if (!r.rows[0]) return res.status(404).json({ error: 'Not found' });
    res.json(r.rows[0]);
  } catch(e) { res.status(500).json({ error: e.message }); }
});

router.delete('/:id', async (req, res) => {
  try {
    const gw = await db.query('SELECT COUNT(*) FROM ble_gateways WHERE zone_id=$1 AND is_active=true',[req.params.id]);
    if (parseInt(gw.rows[0].count)>0)
      return res.status(400).json({ error: `Cannot delete — ${gw.rows[0].count} gateway(s) still assigned` });
    await db.query('UPDATE zones SET is_active=false,updated_at=NOW() WHERE id=$1',[req.params.id]);
    res.json({ success:true });
  } catch(e) { res.status(500).json({ error: e.message }); }
});

module.exports = router;
