import AlertPanel from './AlertPanel'

function SignalBars({ rssi }) {
  const strength = rssi >= -50 ? 4 : rssi >= -65 ? 3 : rssi >= -75 ? 2 : 1
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 20 }}>
      {[1,2,3,4].map(b => (
        <div key={b} style={{
          width: 6, height: 4 + b * 4, borderRadius: 2,
          background: b <= strength
            ? strength >= 3 ? '#44CF6C' : strength === 2 ? '#FFC107' : '#DC3545'
            : 'rgba(255,255,255,0.1)'
        }}/>
      ))}
    </div>
  )
}

export default function TeacherView({ students, alerts, onAck, token }) {
  const present  = students.filter(s => s.presence_state === 'PRESENT').length
  const missing  = students.filter(s => s.presence_state === 'MISSING').length

  return (
    <div>
      {/* Summary */}
      <div style={{ display: 'flex', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        {[
          { label: 'Present',  count: present,         color: '#44CF6C' },
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

      {/* Student list — simple table for teacher */}
      <div style={{ padding: 24 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              {['Student', 'Status', 'Signal', 'Battery', 'Last Seen'].map(h => (
                <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, color: '#71717A', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {students.map(s => {
              const isPresent  = s.presence_state === 'PRESENT'
              const isProbable = s.presence_state === 'PROBABLE'
              const color      = isPresent ? '#44CF6C' : isProbable ? '#FFC107' : '#DC3545'
              return (
                <tr key={s.mac_address} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={{ padding: '12px', color: '#E4E4E7', fontWeight: 600 }}>
                    {s.first_name} {s.last_name}
                  </td>
                  <td style={{ padding: '12px' }}>
                    <span style={{
                      padding: '3px 10px', borderRadius: 20,
                      background: `${color}22`, color, fontSize: 11, fontWeight: 700
                    }}>{s.presence_state}</span>
                  </td>
                  <td style={{ padding: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <SignalBars rssi={s.last_rssi || -100} />
                      <span style={{ fontSize: 12, color: '#71717A', fontFamily: 'monospace' }}>{s.last_rssi} dBm</span>
                    </div>
                  </td>
                  <td style={{ padding: '12px', color: s.battery_mv ? '#44CF6C' : '#71717A', fontFamily: 'monospace', fontSize: 13 }}>
                    {s.battery_mv ? `${(s.battery_mv/1000).toFixed(2)}V` : '—'}
                  </td>
                  <td style={{ padding: '12px', color: '#71717A', fontFamily: 'monospace', fontSize: 12 }}>
                    {s.seconds_ago < 60 ? `${s.seconds_ago}s ago` : `${Math.floor(s.seconds_ago/60)}m ago`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
