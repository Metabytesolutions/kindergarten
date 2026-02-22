require('dotenv').config();
const http      = require('http');
const express   = require('express');
const helmet    = require('helmet');
const cors      = require('cors');
const rateLimit = require('express-rate-limit');
const db        = require('./db');
const wsModule  = require('./websocket');
const mqttWorker = require('./mqttWorker');
const alertEngine = require('./alertEngine');
const { router: authRouter, requireAuth, requireRole } = require('./auth');

const app    = express();
const server = http.createServer(app);

// Init WebSocket on same HTTP server
wsModule.init(server);

app.use(helmet());
app.use(cors({ origin: process.env.APP_URL }));
app.use(express.json());

// Rate limiting
const loginLimiter = rateLimit({ windowMs: 15*60*1000, max: 10 });
const apiLimiter   = rateLimit({ windowMs: 60*1000, max: 200 });
app.use('/api/', apiLimiter);
app.use('/api/auth/login', loginLimiter);

// Auth routes
app.use('/api/auth', authRouter);

// Health
app.get('/api/health', async (req, res) => {
  try {
    await db.query('SELECT 1');
    res.json({ status: 'ok', service: 'prosper-api', timestamp: new Date().toISOString(), db: 'connected' });
  } catch {
    res.status(500).json({ status: 'error', db: 'disconnected' });
  }
});

// Gateways
app.get('/api/gateways', requireAuth, requireRole('IT','DIRECTOR'), async (req, res) => {
  const r = await db.query('SELECT id,mac_address,label,ip_address,health_state,last_heartbeat_at,firmware_version,is_active FROM ble_gateways ORDER BY created_at DESC');
  res.json(r.rows);
});

// Tags
app.get('/api/tags', requireAuth, requireRole('IT','DIRECTOR'), async (req, res) => {
  const r = await db.query('SELECT id,mac_address,tag_type,assigned_to,is_active,last_seen_at,last_rssi,battery_mv FROM ble_tags ORDER BY last_seen_at DESC NULLS LAST');
  res.json(r.rows);
});

// Recent detections
app.get('/api/detections/recent', requireAuth, requireRole('IT'), async (req, res) => {
  const r = await db.query(`
    SELECT d.id,d.tag_mac,d.rssi,d.battery_mv,d.detected_at,g.label as gateway_label
    FROM detections d JOIN ble_gateways g ON g.id=d.gateway_id
    ORDER BY d.detected_at DESC LIMIT 50`);
  res.json(r.rows);
});

// Live presence
app.get('/api/presence/live', requireAuth, async (req, res) => {
  const r = await db.query(`
    SELECT s.first_name,s.last_name,t.mac_address,t.last_rssi,t.battery_mv,t.last_seen_at,
      CASE
        WHEN t.last_seen_at > NOW() - INTERVAL '10 seconds' THEN 'PRESENT'
        WHEN t.last_seen_at > NOW() - INTERVAL '30 seconds' THEN 'PROBABLE'
        ELSE 'MISSING'
      END as presence_state,
      EXTRACT(EPOCH FROM (NOW()-t.last_seen_at))::int as seconds_ago
    FROM ble_tags t JOIN students s ON s.id=t.student_id
    WHERE t.is_active=true ORDER BY s.first_name`);
  res.json(r.rows);
});

// Students
app.get('/api/students', requireAuth, async (req, res) => {
  const r = await db.query(`
    SELECT s.id,s.first_name,s.last_name,s.dob,
           t.mac_address,t.is_active,t.last_seen_at,t.last_rssi
    FROM students s LEFT JOIN ble_tags t ON t.student_id=s.id
    WHERE s.is_active=true ORDER BY s.first_name`);
  res.json(r.rows);
});

// Start
const PORT = process.env.API_PORT || 3000;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`✅ Prosper API + WebSocket running on port ${PORT}`);
  mqttWorker.start(wsModule);
  alertEngine.start(wsModule);
});

// ── ALERTS ────────────────────────────────────────────
app.get('/api/alerts', requireAuth, async (req, res) => {
  const r = await db.query(`
    SELECT a.id, a.alert_type, a.severity, a.status,
           a.title, a.description, a.evidence, a.created_at,
           s.first_name, s.last_name, g.label as gateway_label
    FROM alerts a
    LEFT JOIN students s ON s.id = a.student_id
    LEFT JOIN ble_gateways g ON g.id = a.gateway_id
    WHERE a.status = 'OPEN'
    ORDER BY a.created_at DESC
  `);
  res.json(r.rows);
});

app.post('/api/alerts/:id/ack', requireAuth, async (req, res) => {
  await db.query(`
    UPDATE alerts SET status='ACKED', acked_by=$2, acked_at=NOW() WHERE id=$1
  `, [req.params.id, req.user.id]);
  res.json({ success: true });
});

// ── SESSIONS & BATCH WORKFLOW ─────────────────────────
const sessionsRouter = require('./sessions');
app.use('/api/sessions', requireAuth, sessionsRouter);

// ── ADMIN GATEWAY MANAGEMENT ──────────────────────────────────────────────
const adminGatewaysRouter = require('./adminGateways');
app.use('/api/admin/gateways', requireAuth, adminGatewaysRouter);

const adminZonesRouter = require('./adminZones');
app.use('/api/admin/zones', requireAuth, adminZonesRouter);
