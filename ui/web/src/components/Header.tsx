import { useState } from 'react'
import { api } from '../api/client'

interface HeaderProps {
  mockMode: boolean
  phase: number
  onKill: () => void
}

export function Header({ mockMode, phase, onKill }: HeaderProps) {
  const [killing, setKilling] = useState(false)
  const [killed, setKilled] = useState(false)

  const handleKill = async () => {
    if (!window.confirm('KILL SWITCH: cancel all open orders and pause the system?')) return
    setKilling(true)
    try {
      await api.kill()
      setKilled(true)
      onKill()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      alert('Kill switch failed: ' + msg)
    } finally {
      setKilling(false)
    }
  }

  return (
    <header style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '10px 20px', background: 'var(--bg-panel)',
      borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, zIndex: 100,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 16 }}>
          Trading Bot
        </span>
        <span style={{
          background: 'var(--paper-amber)', color: '#000', fontSize: 11,
          fontWeight: 700, padding: '2px 8px', borderRadius: 3, letterSpacing: 1,
        }}>
          PAPER
        </span>
        {mockMode && (
          <span style={{
            background: '#30363d', color: 'var(--text-muted)', fontSize: 11,
            fontWeight: 600, padding: '2px 8px', borderRadius: 3,
          }}>
            MOCK
          </span>
        )}
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          Phase {phase}
        </span>
      </div>

      <button
        onClick={handleKill}
        disabled={killing || killed}
        style={{
          background: killed ? 'var(--text-dim)' : killing ? 'var(--warning)' : 'var(--danger)',
          color: '#fff', border: 'none', borderRadius: 4, padding: '6px 16px',
          fontWeight: 700, fontSize: 12, letterSpacing: 0.5, opacity: killing ? 0.7 : 1,
          transition: 'background 0.2s',
        }}
      >
        {killed ? 'KILLED' : killing ? 'KILLING…' : 'KILL SWITCH'}
      </button>
    </header>
  )
}
