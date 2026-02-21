import { useState, useEffect } from 'react'

export default function BatchWorkflow({ token, onClose }) {
  const [session,  setSession]  = useState(null)
  const [students, setStudents] = useState([])
  const [tags,     setTags]     = useState([])
  const [loading,  setLoading]  = useState(false)
  const [message,  setMessage]  = useState(null)
  const [selected, setSelected] = useState({ student: null, tag: null })

  const headers = { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }

  async function loadData() {
    try {
      const [sessRes, stuRes, tagRes] = await Promise.all([
        fetch('/api/sessions/active',              { headers }),
        fetch('/api/sessions/unassigned/students', { headers }),
        fetch('/api/sessions/unactivated/tags',    { headers }),
      ])
      setSession(await sessRes.json())
      setStudents(await stuRes.json())
      setTags(await tagRes.json())
    } catch (err) { console.error(err) }
  }

  useEffect(() => {
    loadData()
    const iv = setInterval(loadData, 3000)
    return () => clearInterval(iv)
  }, [])

  async function startSession() {
    setLoading(true)
    const res  = await fetch('/api/sessions/start', { method: 'POST', headers, body: JSON.stringify({}) })
    const data = await res.json()
    setSession(data)
    setLoading(false)
    setMessage({ type: 'success', text: 'Session started!' })
    loadData()
  }

  async function closeSession() {
    if (!session) return
    await fetch('/api/sessions/' + session.id + '/close', { method: 'POST', headers })
    setSession(null)
    setMessage({ type: 'success', text: 'Session closed' })
    loadData()
  }

  async function activateTag() {
    if (!selected.student || !selected.tag) {
      setMessage({ type: 'error', text: 'Select both a student and a tag first' })
      return
    }
    setLoading(true)
    const res  = await fetch('/api/sessions/activate', {
      method: 'POST', headers,
      body: JSON.stringify({ mac_address: selected.tag, student_id: selected.student, batch_id: session?.id })
    })
    const data = await res.json()
    if (data.success) {
      setMessage({ type: 'success', text: 'Tag activated for ' + data.student.first_name + ' ' + data.student.last_name })
      setSelected({ student: null, tag: null })
      loadData()
    } else {
      setMessage({ type: 'error', text: data.error })
    }
    setLoading(false)
  }

  const card = {
    background: '#1B1B2F',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16, padding: 20, marginBottom: 16
  }

  const msgColor = message?.type === 'success' ? '#44CF6C' : '#DC3545'
  const msgBg    = message?.type === 'success' ? 'rgba(68,207,108,0.15)' : 'rgba(220,53,69,0.15)'

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.8)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div style={{ background: '#0F1117', borderRadius: 20, width: '100%', maxWidth: 800, maxHeight: '90vh', overflow: 'auto', border: '1px solid rgba(255,255,255,0.1)' }}>

        <div style={{ padding: '20px 24px', borderBottom: '1px solid rgba(255,255,255,0.06)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18, color: '#E4E4E7' }}>Card Activation Workflow</div>
            <div style={{ fontSize: 12, color: '#71717A', marginTop: 2 }}>Link BLE tags to students</div>
          </div>
          <button onClick={onClose} style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '6px 14px', color: '#71717A', cursor: 'pointer' }}>Close</button>
        </div>

        <div style={{ padding: 24 }}>
          {message && (
            <div style={{ padding: '10px 16px', borderRadius: 8, marginBottom: 16, background: msgBg, color: msgColor, fontSize: 13 }}>
              {message.text}
            </div>
          )}

          <div style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontWeight: 700, color: '#E4E4E7', marginBottom: 4 }}>Session Status</div>
                <div style={{ fontSize: 13, color: session ? '#44CF6C' : '#71717A' }}>
                  {session ? 'Active since ' + new Date(session.started_at).toLocaleTimeString() : 'No active session'}
                </div>
              </div>
              {!session
                ? <button onClick={startSession} disabled={loading} style={{ background: 'linear-gradient(135deg, #4ECDC4, #44CF6C)', border: 'none', borderRadius: 8, padding: '10px 20px', color: '#0F1117', fontWeight: 700, cursor: 'pointer' }}>Start Session</button>
                : <button onClick={closeSession} style={{ background: 'rgba(220,53,69,0.2)', border: '1px solid rgba(220,53,69,0.3)', borderRadius: 8, padding: '10px 20px', color: '#DC3545', cursor: 'pointer', fontWeight: 700 }}>Close Session</button>
              }
            </div>
          </div>

          {session && (
            <div style={card}>
              <div style={{ fontWeight: 700, color: '#E4E4E7', marginBottom: 16 }}>Activate a Tag</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

                <div>
                  <div style={{ fontSize: 11, color: '#71717A', textTransform: 'uppercase', marginBottom: 8 }}>
                    Unassigned Students ({students.length})
                  </div>
                  {students.length === 0
                    ? <div style={{ color: '#44CF6C', fontSize: 13 }}>All students assigned</div>
                    : students.map(s => (
                      <div key={s.id} onClick={() => setSelected(p => ({ ...p, student: s.id }))} style={{
                        padding: '10px 14px', borderRadius: 8, marginBottom: 8, cursor: 'pointer',
                        background: selected.student === s.id ? 'rgba(78,205,196,0.2)' : 'rgba(255,255,255,0.04)',
                        border: '1px solid ' + (selected.student === s.id ? '#4ECDC4' : 'rgba(255,255,255,0.08)'),
                        color: '#E4E4E7', fontSize: 14, fontWeight: selected.student === s.id ? 700 : 400
                      }}>
                        {s.first_name} {s.last_name}
                      </div>
                    ))
                  }
                </div>

                <div>
                  <div style={{ fontSize: 11, color: '#71717A', textTransform: 'uppercase', marginBottom: 8 }}>
                    Detected Tags ({tags.length})
                  </div>
                  {tags.length === 0
                    ? <div style={{ color: '#71717A', fontSize: 13 }}>No unactivated tags in range</div>
                    : tags.map(t => (
                      <div key={t.mac_address} onClick={() => setSelected(p => ({ ...p, tag: t.mac_address }))} style={{
                        padding: '10px 14px', borderRadius: 8, marginBottom: 8, cursor: 'pointer',
                        background: selected.tag === t.mac_address ? 'rgba(78,205,196,0.2)' : 'rgba(255,255,255,0.04)',
                        border: '1px solid ' + (selected.tag === t.mac_address ? '#4ECDC4' : 'rgba(255,255,255,0.08)'),
                      }}>
                        <div style={{ color: '#E4E4E7', fontFamily: 'monospace', fontSize: 13, fontWeight: 700 }}>{t.mac_address}</div>
                        <div style={{ color: '#71717A', fontSize: 11, marginTop: 2 }}>RSSI: {t.last_rssi} dBm · {t.seconds_ago}s ago</div>
                      </div>
                    ))
                  }
                </div>
              </div>

              <button onClick={activateTag} disabled={loading || !selected.student || !selected.tag} style={{
                marginTop: 16, width: '100%', padding: '12px',
                background: selected.student && selected.tag ? 'linear-gradient(135deg, #4ECDC4, #44CF6C)' : 'rgba(255,255,255,0.05)',
                border: 'none', borderRadius: 10,
                color: selected.student && selected.tag ? '#0F1117' : '#52525B',
                fontWeight: 700, fontSize: 15,
                cursor: selected.student && selected.tag ? 'pointer' : 'not-allowed'
              }}>
                {loading ? 'Activating...' : 'Link Tag to Student'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
