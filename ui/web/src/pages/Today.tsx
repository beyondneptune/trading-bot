import { useEffect, useState, useCallback } from 'react'
import { api, Signal, EquitySnapshot } from '../api/client'
import { AlertCard } from '../components/AlertCard'
import { SafetyStrip } from '../components/SafetyStrip'
import { useWebSocket } from '../hooks/useWebSocket'

export function Today() {
  const [signals, setSignals] = useState<Signal[]>([])
  const [snapshots, setSnapshots] = useState<EquitySnapshot[]>([])
  const [regime, setRegime] = useState<string | null>(null)
  const [regimeMultiplier, setRegimeMult] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [sigRes, eqRes] = await Promise.all([
        api.signals(),
        api.equity(),
      ])
      setSignals(sigRes.signals)
      setSnapshots(eqRes.snapshots)

      // Extract regime from signals if available
      const sig = sigRes.signals[0]
      if (sig?.regime) {
        setRegime(sig.regime)
        setRegimeMult(sig.regime_multiplier ?? null)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Refresh on WebSocket push
  useWebSocket(useCallback((msg) => {
    if (msg.type === 'approval' || msg.type === 'new_signal') {
      refresh()
    }
  }, [refresh]))

  const pending = signals.filter(s => {
    if (s.valid_until) return new Date(s.valid_until) > new Date()
    return true
  })

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200 }}>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Loading…</span>
      </div>
    )
  }

  return (
    <div>
      <SafetyStrip snapshots={snapshots} regime={regime} regimeMultiplier={regimeMultiplier} />

      <div style={{ padding: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600 }}>
            Pending Signals
            {pending.length > 0 && (
              <span style={{
                marginLeft: 8, background: 'var(--warning)', color: '#000',
                fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
              }}>
                {pending.length}
              </span>
            )}
          </h2>
          <button
            onClick={refresh}
            style={{
              background: 'transparent', border: '1px solid var(--border)',
              color: 'var(--text-muted)', borderRadius: 4, padding: '4px 12px', fontSize: 12,
            }}
          >
            Refresh
          </button>
        </div>

        {pending.length === 0 ? (
          <div style={{
            textAlign: 'center', padding: '60px 20px',
            color: 'var(--text-muted)', fontFamily: 'var(--font-mono)',
          }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>—</div>
            <div>No pending signals</div>
            <div style={{ fontSize: 11, marginTop: 4 }}>
              The AI analyst will post signals here when opportunities appear.
            </div>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12 }}>
            {pending.map(s => (
              <AlertCard key={s.id} signal={s} onDecision={refresh} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
