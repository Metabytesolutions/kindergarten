const { WebSocketServer } = require('ws');
const jwt = require('jsonwebtoken');

const SECRET = process.env.JWT_SECRET;
let wss;

function init(server) {
  wss = new WebSocketServer({ server, path: '/ws' });

  wss.on('connection', (ws, req) => {
    // Verify token from query string
    const url    = new URL(req.url, 'http://localhost');
    const token  = url.searchParams.get('token');

    try {
      const user = jwt.verify(token, SECRET);
      ws.user    = user;
      ws.isAlive = true;
      console.log(`🔌 WS connected: ${user.username} (${user.role})`);
    } catch {
      console.log('🔌 WS rejected: invalid token');
      ws.close(1008, 'Invalid token');
      return;
    }

    ws.on('pong', () => { ws.isAlive = true });
    ws.on('close', () => console.log(`🔌 WS disconnected: ${ws.user?.username}`));
    ws.on('error', (err) => console.error('WS error:', err.message));

    // Send welcome message
    ws.send(JSON.stringify({ type: 'CONNECTED', message: 'WebSocket connected' }));
  });

  // Heartbeat to detect dead connections
  setInterval(() => {
    wss.clients.forEach(ws => {
      if (!ws.isAlive) { ws.terminate(); return; }
      ws.isAlive = false;
      ws.ping();
    });
  }, 30000);

  console.log('✅ WebSocket server initialized');
}

// Broadcast to all authenticated clients
function broadcast(type, data) {
  if (!wss) return;
  const message = JSON.stringify({ type, data, ts: new Date().toISOString() });
  wss.clients.forEach(ws => {
    if (ws.readyState === 1) ws.send(message);
  });
}

module.exports = { init, broadcast };
