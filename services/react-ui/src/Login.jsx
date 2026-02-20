import { useState } from 'react'

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const res  = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      })
      const data = await res.json()
      if (!res.ok) { setError(data.error || 'Login failed'); return; }
      localStorage.setItem('prosper_token', data.token)
      localStorage.setItem('prosper_user',  JSON.stringify(data.user))
      onLogin(data.user, data.token)
    } catch {
      setError('Cannot connect to server')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', background: '#0F1117',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: "'Segoe UI', system-ui, sans-serif"
    }}>
      <div style={{
        background: 'linear-gradient(135deg, #1B1B2F 0%, #16213E 100%)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 20, padding: 40, width: 380,
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)'
      }}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            width: 64, height: 64, borderRadius: 18, margin: '0 auto 16px',
            background: 'linear-gradient(135deg, #4ECDC4, #44CF6C)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 32
          }}>🏫</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#E4E4E7' }}>Prosper RFID</div>
          <div style={{ fontSize: 13, color: '#71717A', marginTop: 4 }}>Safety & Operations Platform</div>
        </div>

        {/* Form */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, color: '#71717A', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Username
            </label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="Enter username"
              style={{
                width: '100%', marginTop: 6, padding: '10px 14px',
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 10, color: '#E4E4E7', fontSize: 14,
                outline: 'none', boxSizing: 'border-box'
              }}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#71717A', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSubmit(e)}
              placeholder="Enter password"
              style={{
                width: '100%', marginTop: 6, padding: '10px 14px',
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 10, color: '#E4E4E7', fontSize: 14,
                outline: 'none', boxSizing: 'border-box'
              }}
            />
          </div>

          {error && (
            <div style={{
              padding: '10px 14px', borderRadius: 8,
              background: 'rgba(220,53,69,0.15)',
              border: '1px solid rgba(220,53,69,0.3)',
              color: '#DC3545', fontSize: 13
            }}>{error}</div>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading}
            style={{
              padding: '12px', borderRadius: 10, border: 'none',
              background: loading ? '#27272A' : 'linear-gradient(135deg, #4ECDC4, #44CF6C)',
              color: loading ? '#71717A' : '#0F1117',
              fontSize: 15, fontWeight: 700, cursor: loading ? 'not-allowed' : 'pointer',
              marginTop: 8, transition: 'all 0.2s'
            }}
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </div>

        <div style={{ textAlign: 'center', marginTop: 24, fontSize: 12, color: '#52525B' }}>
          Default: admin / Admin1234!
        </div>
      </div>
    </div>
  )
}
