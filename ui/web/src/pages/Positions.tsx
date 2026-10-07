import { useEffect, useState } from 'react'
import { api, Position } from '../api/client'

export function Positions() {
  const [positions, setPositions] = useState<Position[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.positions()
      .then(r => setPositions(r.positions))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div style={{ padding: 20 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>
        Open Positions
        <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: 13, marginLeft: 8 }}>
          {positions.length} open
        </span>
      </h2>

      {loading ? (
        <div style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Loading…</div>
      ) : positions.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '60px 20px',
          color: 'var(--text-muted)', fontFamily: 'var(--font-mono)',
        }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>—</div>
          <div>No open positions</div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['Symbol', 'Exchange', 'Qty', 'Avg Cost', 'Stop', 'Target', 'Currency', 'Opened'].map(h => (
                  <th key={h} style={{
                    textAlign: 'left', padding: '6px 12px',
                    color: 'var(--text-muted)', fontWeight: 600, fontSize: 11,
                    textTransform: 'uppercase', letterSpacing: 0.5,
                  }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.id} style={{ borderBottom: '1px solid var(--bg-card)' }}>
                  <td style={{ padding: '10px 12px', fontWeight: 700 }}>{p.symbol}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-muted)' }}>{p.exchange}</td>
                  <td style={{ padding: '10px 12px' }}>{p.quantity}</td>
                  <td style={{ padding: '10px 12px' }}>{p.avg_cost.toFixed(2)}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--loss)' }}>
                    {p.stop_price?.toFixed(2) ?? '—'}
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--gain)' }}>
                    {p.target_price?.toFixed(2) ?? '—'}
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-muted)' }}>{p.currency}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-muted)' }}>
                    {new Date(p.opened_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
