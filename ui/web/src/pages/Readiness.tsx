import { useEffect, useState } from 'react'

interface ReadinessItem {
  name: string
  passed: boolean
  value: string
  requirement: string
}

interface ReadinessData {
  items: ReadinessItem[]
  overall: boolean
  summary: string
  blockers: string[]
  checked_at: string
}

export function Readiness() {
  const [data, setData] = useState<ReadinessData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/readiness')
      .then(r => r.ok ? r.json() : Promise.resolve(null))
      .then(setData)
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200 }}>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Loading…</span>
      </div>
    )
  }

  if (!data) {
    return (
      <div style={{ padding: 20, color: 'var(--text-muted)' }}>
        Readiness endpoint not available yet (Phase 7).
      </div>
    )
  }

  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600 }}>Phase Readiness</h2>
        <span style={{
          padding: '4px 14px', borderRadius: 20, fontSize: 12, fontWeight: 700,
          background: data.overall ? 'var(--gain)' : 'var(--danger)',
          color: '#fff',
        }}>
          {data.overall ? 'READY' : 'NOT READY'}
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{data.summary}</span>
      </div>

      {data.blockers.length > 0 && (
        <div style={{
          background: '#2d1b1b', border: '1px solid var(--danger)',
          borderRadius: 6, padding: '10px 16px', marginBottom: 20,
          fontSize: 13,
        }}>
          <div style={{ color: 'var(--danger)', fontWeight: 700, marginBottom: 6 }}>Blockers</div>
          {data.blockers.map(b => (
            <div key={b} style={{ color: 'var(--text-muted)', marginLeft: 8 }}>— {b}</div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {data.items.map(item => (
          <div key={item.name} style={{
            background: 'var(--bg-card)', border: `1px solid ${item.passed ? 'var(--border)' : 'var(--danger)'}`,
            borderRadius: 6, padding: '12px 16px',
            display: 'flex', alignItems: 'center', gap: 16,
          }}>
            <span style={{
              fontSize: 16, minWidth: 20, textAlign: 'center',
              color: item.passed ? 'var(--gain)' : 'var(--danger)',
            }}>
              {item.passed ? '✓' : '✗'}
            </span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{item.name}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                Requirement: {item.requirement}
              </div>
            </div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 13,
              color: item.passed ? 'var(--gain)' : 'var(--loss)',
              fontWeight: 600,
            }}>
              {item.value}
            </div>
          </div>
        ))}
      </div>

      {data.checked_at && (
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-dim)' }}>
          Last checked: {new Date(data.checked_at).toLocaleString()}
        </div>
      )}
    </div>
  )
}
