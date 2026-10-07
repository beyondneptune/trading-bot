import { useEffect, useState } from 'react'

interface Approval {
  id: number
  signal_id: number
  decision: string
  interface: string
  reason: string | null
  confidence: number | null
  mood: string | null
  decided_at: string
}

export function Journal() {
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/journal')
      .then(r => r.ok ? r.json() : Promise.resolve({ approvals: [] }))
      .then(d => setApprovals(d.approvals ?? []))
      .finally(() => setLoading(false))
  }, [])

  const DECISION_COLOR: Record<string, string> = {
    APPROVED: 'var(--gain)',
    REJECTED: 'var(--loss)',
    EXPIRED: 'var(--text-muted)',
  }

  return (
    <div style={{ padding: 20 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Decision Journal</h2>

      {loading ? (
        <div style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Loading…</div>
      ) : approvals.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '60px 20px',
          color: 'var(--text-muted)', fontFamily: 'var(--font-mono)',
        }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>—</div>
          <div>No decisions yet</div>
          <div style={{ fontSize: 11, marginTop: 4 }}>
            Your approve/reject decisions will appear here with notes.
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {approvals.map(a => (
            <div key={a.id} style={{
              background: 'var(--bg-card)', border: '1px solid var(--border)',
              borderRadius: 6, padding: '12px 16px',
              display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
            }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{
                    fontWeight: 700, fontSize: 13,
                    color: DECISION_COLOR[a.decision] ?? 'var(--text)',
                  }}>
                    {a.decision}
                  </span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                    Signal #{a.signal_id}
                  </span>
                  {a.mood && (
                    <span style={{
                      fontSize: 11, padding: '1px 8px', borderRadius: 10,
                      background: 'var(--bg-panel)', color: 'var(--text-muted)',
                    }}>
                      {a.mood}
                    </span>
                  )}
                </div>
                {a.reason && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic' }}>
                    "{a.reason}"
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                {a.confidence != null && <div>Confidence: {a.confidence}/5</div>}
                <div>{new Date(a.decided_at).toLocaleString()}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
