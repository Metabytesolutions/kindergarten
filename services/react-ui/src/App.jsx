import { useState, useEffect, useRef } from 'react'
import Login from './Login'
import AlertPanel from './AlertPanel'
import TeacherView from './TeacherView'
import DirectorView from './DirectorView'
import GatewayManager from './GatewayManager'
import ZoneManager from './ZoneManager'
import UserManager from './UserManager'
import StudentManager from './StudentManager'

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

function StudentCard({ student }) {
  const isPresent   = student.presence_state === 'PRESENT'
  const isProbable  = student.presence_state === 'PROBABLE'
  const borderColor = isPresent ? '#44CF6C' : isProbable ? '#FFC107' : '#DC3545'
  const badgeBg     = isPresent ? 'rgba(68,207,108,0.15)' : isProbable ? 'rgba(255,193,7,0.15)' : 'rgba(220,53,69,0.15)'
  const initials    = `${student.first_name[0]}${student.last_name[0]}`
  return (
    <div style={{
      background: 'linear-gradient(135deg, #1B1B2F 0%, #16213E 100%)',
      border: `1px solid ${borderColor}`, borderRadius: 16, padding: 20,
      display: 'flex', flexDirection: 'column', gap: 12,
      boxShadow: `0 0 20px ${borderColor}22`, transition: 'all 0.3s ease'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 44, height: 44, borderRadius: '50%',
            background: `linear-gradient(135deg, ${borderColor}44, ${borderColor}22)`,
            border: `2px solid ${borderColor}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 16, fontWeight: 700, color: borderColor
          }}>{initials}</div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 16, color: '#E4E4E7' }}>
              {student.first_name} {student.last_name}
            </div>
            <div style={{ fontSize: 11, color: '#71717A', fontFamily: 'monospace' }}>
              {student.mac_address}
            </div>
          </div>
        </div>
        <div style={{
          padding: '4px 12px', borderRadius: 20, background: badgeBg,
          color: borderColor, fontSize: 11, fontWeight: 700
        }}>{student.presence_state}</div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 10, color: '#71717A', textTransform: 'uppercase' }}>Signal</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
            <SignalBars rssi={student.last_rssi || -100} />
            <span style={{ fontSize: 13, color: '#E4E4E7', fontFamily: 'monospace' }}>{student.last_rssi} dBm</span>
          </div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 10, color: '#71717A', textTransform: 'uppercase' }}>Battery</div>
          <div style={{ fontSize: 13, color: student.battery_mv ? '#44CF6C' : '#71717A', fontFamily: 'monospace', marginTop: 4 }}>
            {student.battery_mv ? `${(student.battery_mv/1000).toFixed(2)}V` : '—'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 10, color: '#71717A', textTransform: 'uppercase' }}>Last Seen</div>
          <div style={{ fontSize: 13, color: '#E4E4E7', fontFamily: 'monospace', marginTop: 4 }}>
            {student.seconds_ago < 60 ? `${student.seconds_ago}s ago` : `${Math.floor(student.seconds_ago/60)}m ago`}
          </div>
        </div>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.05)' }}>
        <div style={{
          height: '100%', borderRadius: 2, background: borderColor,
          transition: 'width 0.5s ease',
          width: isPresent ? '100%' : isProbable ? '50%' : '10%'
        }}/>
      </div>
    </div>
  )
}

export default function App() {
  const [user,       setUser]       = useState(() => JSON.parse(localStorage.getItem('prosper_user') || 'null'))
  const [token,      setToken]      = useState(() => localStorage.getItem('prosper_token') || '')
  const [students,   setStudents]   = useState([])
  const [alerts,     setAlerts]     = useState([])
  const [wsStatus,   setWsStatus]   = useState('disconnected')
  const [lastUpdate, setLastUpdate] = useState(null)
  const [itTab, setItTab] = useState('dashboard')
  const wsRef                       = useRef(null)

  function handleLogin(u, t) { setUser(u); setToken(t) }

  function handleLogout() {
    localStorage.removeItem('prosper_token')
    localStorage.removeItem('prosper_user')
    if (wsRef.current) wsRef.current.close()
    setUser(null); setToken(''); setStudents([]); setAlerts([])
  }

  async function loadInitial(tok) {
    try {
      const res  = await fetch('/api/presence/live', {
        headers: { Authorization: `Bearer ${tok}` }
      })
      if (res.status === 401) { handleLogout(); return }
      const data = await res.json()
      setStudents(data)
      setLastUpdate(new Date())
    } catch (err) {
      console.error('Initial load failed:', err)
    }
  }

  useEffect(() => {
    if (!token || !user) return
    loadInitial(token)
    const wsUrl = `ws://${window.location.host}/ws?token=${token}`
    const ws    = new WebSocket(wsUrl)
    wsRef.current = ws
    ws.onopen    = () => setWsStatus('connected')
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'PRESENCE_UPDATE') { setStudents(msg.data); setLastUpdate(new Date()) }
        if (msg.type === 'ALERT_FIRED')     { setAlerts(prev => [msg.data, ...prev.filter(a => a.id !== msg.data.id)]) }
        if (msg.type === 'ALERT_RESOLVED')  { setAlerts(prev => prev.filter(a => a.type !== msg.data.type)) }
        if (msg.type === 'GATEWAY_HEARTBEAT') { setWsStatus('connected') }
      } catch (err) { console.error('WS error:', err) }
    }
    ws.onclose = () => { setWsStatus('disconnected'); setTimeout(() => loadInitial(token), 3000) }
    ws.onerror = () => setWsStatus('error')
    return () => ws.close()
  }, [token])

  if (!user) return <Login onLogin={handleLogin} />

  const present  = students.filter(s => s.presence_state === 'PRESENT').length
  const probable = students.filter(s => s.presence_state === 'PROBABLE').length
  const missing  = students.filter(s => s.presence_state === 'MISSING').length
  const wsColor  = wsStatus === 'connected' ? '#44CF6C' : wsStatus === 'error' ? '#DC3545' : '#FFC107'

  const onAck = (id) => setAlerts(prev => prev.filter(a => a.id !== id))

  return (
    <div style={{ minHeight: '100vh', background: '#0F1117', color: '#E4E4E7', fontFamily: "'Segoe UI', system-ui, sans-serif" }}>
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}`}</style>

      <header style={{
        background: 'linear-gradient(135deg, #1B1B2F 0%, #162447 100%)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        padding: '14px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 12,
            background: 'linear-gradient(135deg, #4ECDC4, #44CF6C)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20
          }}>🏫</div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>Prosper RFID Platform</div>
            <div style={{ fontSize: 11, color: '#71717A' }}>
              Live Presence · Classroom A
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              background: wsColor,
              animation: wsStatus === 'connected' ? 'pulse 2s infinite' : 'none'
            }}/>
            <span style={{ fontSize: 12, color: '#71717A' }}>
              {wsStatus === 'connected' ? 'Live' : 'Reconnecting...'}
            </span>
            {lastUpdate && (
              <span style={{ fontSize: 11, color: '#52525B', marginLeft: 4 }}>
                {lastUpdate.toLocaleTimeString()}
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: '#71717A', borderLeft: '1px solid rgba(255,255,255,0.1)', paddingLeft: 16 }}>
            👤 {user.username} <span style={{ color: '#4ECDC4' }}>({user.role})</span>
          </div>
          <button onClick={handleLogout} style={{
            background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 8, padding: '6px 14px', color: '#71717A',
            cursor: 'pointer', fontSize: 12
          }}>Logout</button>
        </div>
      </header>

      {user.role === 'TEACHER' && (
        <TeacherView students={students} alerts={alerts} onAck={onAck} token={token} />
      )}

      {user.role === 'DIRECTOR' && (
        <DirectorView students={students} alerts={alerts} onAck={onAck} token={token} gatewayOk={wsStatus === 'connected'} />
      )}

      {user.role === 'IT' && (
        <div>
          <div style={{ display:'flex', gap:0, borderBottom:'1px solid rgba(255,255,255,0.06)', padding:'0 24px' }}>
            {[{id:'dashboard',label:'📊 Dashboard'},{id:'gateways',label:'📡 Gateways'},{id:'zones',label:'🏫 Zones'},{id:'users',label:'👥 Users'},{id:'students',label:'👶 Students'}].map(t=>(
              <div key={t.id} onClick={()=>setItTab(t.id)} style={{
                padding:'12px 20px', cursor:'pointer', fontSize:13, fontWeight:700,
                color: itTab===t.id ? '#2E86AB' : '#8899AA',
                borderBottom: itTab===t.id ? '2px solid #2E86AB' : '2px solid transparent',
                transition:'all 0.15s',
              }}>{t.label}</div>
            ))}
          </div>
          {itTab==='gateways' && <div style={{padding:24}}><GatewayManager token={token}/></div>}
          {itTab==='zones' && <div style={{padding:24}}><ZoneManager token={token}/></div>}
          {itTab==='users' && <div style={{padding:24}}><UserManager token={token}/></div>}
          {itTab==='students' && <div style={{padding:24}}><StudentManager token={token}/></div>}
          {itTab==='dashboard' && <div>
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
          <div style={{ padding: '16px 24px 0' }}>
            <AlertPanel alerts={alerts} onAck={onAck} token={token} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16, padding: 24 }}>
            {students.map(s => <StudentCard key={s.mac_address} student={s} />)}
          </div>
          </div>}
        </div>
      )}

    </div>
  )
}
