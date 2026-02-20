const SEVERITY_COLOR = { CRITICAL: '#DC3545', WARNING: '#FFC107', INFO: '#4ECDC4' }
const TYPE_ICON = { TAG_MISSING: '🚨', GATEWAY_OFFLINE: '📡', EXIT_ATTEMPT: '🚪' }

export default function AlertPanel({ alerts, onAck, token }) {
  if (!alerts || alerts.length === 0) return null
  async function handleAck(id) {
    await fetch(`/api/alerts/${id}/ack`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}` }
    })
    onAck(id)
  }
  return (
    <div style={{ padding: '0 24px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 12, color: '#71717A', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
        🚨 Active Alerts ({alerts.length})
      </div>
      {alerts.map(alert => (
        <div key={alert.id} style={{
          background: `${SEVERITY_COLOR[alert.severity]}11`,
          border: `1px solid ${SEVERITY_COLOR[alert.severity]}44`,
          borderLeft: `4px solid ${SEVERITY_COLOR[alert.severity]}`,
          borderRadius: 10, padding: '12px 16px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 20 }}>{TYPE_ICON[alert.alert_type || alert.type] || '⚠️'}</span>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14, color: SEVERITY_COLOR[alert.severity] }}>
                {alert.title}
              </div>
              <div style={{ fontSize: 12, color: '#71717A', marginTop: 2 }}>
                {alert.description} · {new Date(alert.created_at).toLocaleTimeString()}
              </div>
            </div>
          </div>
          <button onClick={() => handleAck(alert.id)} style={{
            background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 8, padding: '6px 14px', color: '#E4E4E7',
            cursor: 'pointer', fontSize: 12, whiteSpace: 'nowrap'
          }}>Acknowledge</button>
        </div>
      ))}
    </div>
  )
}
