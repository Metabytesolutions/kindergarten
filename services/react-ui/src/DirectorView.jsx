import AlertPanel from './AlertPanel'

export default function DirectorView({ students, alerts, onAck, token, gatewayOk }) {
  const present  = students.filter(s => s.presence_state === 'PRESENT').length
  const probable = students.filter(s => s.presence_state === 'PROBABLE').length
  const missing  = students.filter(s => s.presence_state === 'MISSING').length

  return (
    <div>
      {/* Summary */}
      <div style={{ display: 'flex', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        {[
          { label: 'Present',  count: present,         color: '#44CF6C' },
          { label: 'Probable', count: probable,        color: '#FFC107' },
          { label: 'Missing',  count: missing,         color: '#DC3545' },
          { label: 'Total',    count: students.length, color: '#4ECDC4' },
        ].map(s => (
          <div key={s.label} style={{
            flex: 1, padding: '12px 24px', textAlign: 'center',
            borderRight: '1px solid rgba(255,255,255,0.06)'
          }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: s.color }}>{s.count}</div>
            <div style={{ fontSize: 11, color: '#71717A', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Alerts */}
      <div style={{ padding: '16px 24px 0' }}>
        <AlertPanel alerts={alerts} onAck={onAck} token={token} />
      </div>

      {/* Classroom overview */}
      <div style={{ padding: 24 }}>
        <div style={{ fontSize: 13, color: '#71717A', marginBottom: 16, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Classroom A — Live Overview
        </div>

        {/* Gateway status card */}
        <div style={{
          background: 'linear-gradient(135deg, #1B1B2F 0%, #16213E 100%)',
          border: `1px solid ${gatewayOk ? '#44CF6C44' : '#DC354544'}`,
          borderRadius: 12, padding: 16, marginBottom: 16,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 24 }}>📡</span>
            <div>
              <div style={{ fontWeight: 700, color: '#E4E4E7' }}>Gateway F0A882F54070</div>
              <div style={{ fontSize: 12, color: '#71717A' }}>Classroom A · 192.168.5.65</div>
            </div>
          </div>
          <span style={{
            padding: '4px 12px', borderRadius: 20, fontSize: 11, fontWeight: 700,
            background: gatewayOk ? 'rgba(68,207,108,0.15)' : 'rgba(220,53,69,0.15)',
            color: gatewayOk ? '#44CF6C' : '#DC3545'
          }}>{gatewayOk ? 'HEALTHY' : 'OFFLINE'}</span>
        </div>

        {/* Attendance summary per student */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
          {students.map(s => {
            const isPresent  = s.presence_state === 'PRESENT'
            const isProbable = s.presence_state === 'PROBABLE'
            const color      = isPresent ? '#44CF6C' : isProbable ? '#FFC107' : '#DC3545'
            const initials   = `${s.first_name[0]}${s.last_name[0]}`
            return (
              <div key={s.mac_address} style={{
                background: 'linear-gradient(135deg, #1B1B2F 0%, #16213E 100%)',
                border: `1px solid ${color}44`, borderRadius: 12, padding: 16,
                display: 'flex', alignItems: 'center', gap: 12
              }}>
                <div style={{
                  width: 40, height: 40, borderRadius: '50%',
                  background: `${color}22`, border: `2px solid ${color}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 14, fontWeight: 700, color
                }}>{initials}</div>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14, color: '#E4E4E7' }}>
                    {s.first_name} {s.last_name}
                  </div>
                  <div style={{ fontSize: 11, color, marginTop: 2, fontWeight: 700 }}>
                    {s.presence_state}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
