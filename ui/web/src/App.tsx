import { useEffect, useState } from 'react'
import { Header } from './components/Header'
import { Today } from './pages/Today'
import { Activity } from './pages/Activity'
import { Positions } from './pages/Positions'
import { Performance } from './pages/Performance'
import { Journal } from './pages/Journal'
import { Learn } from './pages/Learn'
import { Readiness } from './pages/Readiness'
import { api } from './api/client'

type Page = 'today' | 'activity' | 'positions' | 'performance' | 'journal' | 'learn' | 'readiness'

const NAV: { id: Page; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'activity', label: 'Activity' },
  { id: 'positions', label: 'Positions' },
  { id: 'performance', label: 'Performance' },
  { id: 'journal', label: 'Journal' },
  { id: 'readiness', label: 'Readiness' },
  { id: 'learn', label: 'Learn' },
]

export default function App() {
  const [page, setPage] = useState<Page>('today')
  const [mockMode, setMockMode] = useState(true)
  const [phase, setPhase] = useState(0)
  const [killed, setKilled] = useState(false)

  useEffect(() => {
    api.status()
      .then(s => { setMockMode(s.mock_mode); setPhase(s.phase) })
      .catch(() => {})
  }, [])

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Header mockMode={mockMode} phase={phase} onKill={() => setKilled(true)} />

      {/* Kill switch banner */}
      {killed && (
        <div style={{
          background: 'var(--danger)', color: '#fff',
          textAlign: 'center', padding: '8px 0', fontWeight: 700, fontSize: 13, letterSpacing: 0.5,
        }}>
          KILL SWITCH ACTIVATED — All open orders cancelled
        </div>
      )}

      {/* Navigation */}
      <nav style={{
        display: 'flex', gap: 0,
        background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)',
        overflowX: 'auto',
      }}>
        {NAV.map(n => (
          <button
            key={n.id}
            onClick={() => setPage(n.id)}
            style={{
              background: 'transparent', border: 'none',
              borderBottom: `2px solid ${page === n.id ? 'var(--accent)' : 'transparent'}`,
              color: page === n.id ? 'var(--text)' : 'var(--text-muted)',
              padding: '10px 18px', fontSize: 13, fontWeight: page === n.id ? 600 : 400,
              cursor: 'pointer', whiteSpace: 'nowrap', transition: 'color 0.15s',
            }}
          >
            {n.label}
          </button>
        ))}
      </nav>

      {/* Page content */}
      <main style={{ flex: 1 }}>
        {page === 'today' && <Today />}
        {page === 'activity' && <Activity />}
        {page === 'positions' && <Positions />}
        {page === 'performance' && <Performance />}
        {page === 'journal' && <Journal />}
        {page === 'readiness' && <Readiness />}
        {page === 'learn' && <Learn />}
      </main>
    </div>
  )
}
