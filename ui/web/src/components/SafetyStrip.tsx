import { EquitySnapshot } from '../api/client'

interface SafetyStripProps {
  snapshots: EquitySnapshot[]
  regime: string | null
  regimeMultiplier: number | null
}

function pct(value: number, max: number) {
  return Math.min(100, Math.max(0, (value / max) * 100))
}

export function SafetyStrip({ snapshots, regime, regimeMultiplier }: SafetyStripProps) {
  const latest = snapshots[0]

  const startOfDay = snapshots.find(s => {
    const d = new Date(s.snapped_at)
    const now = new Date()
    return d.getDate() !== now.getDate() || d.getMonth() !== now.getMonth()
  })

  const startEquity = startOfDay?.equity_gbp ?? latest?.equity_gbp ?? 0
  const currentEquity = latest?.equity_gbp ?? 0
  const dailyPnl = currentEquity - startEquity
  const dailyPnlPct = startEquity > 0 ? (dailyPnl / startEquity) * 100 : 0

  const heat = latest?.portfolio_heat ?? 0
  const heatPct = pct(heat * 100, 5)

  const regimeColor = () => {
    if (!regime) return 'var(--text-muted)'
    if (regime.includes('HIGH') || regime.includes('RISK_OFF')) return 'var(--loss)'
    if (regime.includes('LOW') || regime.includes('BULL')) return 'var(--gain)'
    return 'var(--warning)'
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 24,
      padding: '8px 20px', background: 'var(--bg-panel)',
      borderBottom: '1px solid var(--border)',
      fontFamily: 'var(--font-mono)', fontSize: 12,
    }}>
      {/* Daily P&L */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Daily P&L
        </span>
        <span style={{ color: dailyPnl >= 0 ? 'var(--gain)' : 'var(--loss)', fontWeight: 700 }}>
          {dailyPnl >= 0 ? '+' : ''}{dailyPnlPct.toFixed(2)}%
          <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: 6 }}>
            £{Math.abs(dailyPnl).toFixed(0)}
          </span>
        </span>
      </div>

      <div style={{ width: 1, height: 28, background: 'var(--border)' }} />

      {/* Portfolio heat */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Portfolio Heat {(heat * 100).toFixed(1)}%/5%
        </span>
        <div style={{ width: 120, height: 6, background: 'var(--bg-card)', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{
            width: `${heatPct}%`, height: '100%', borderRadius: 3,
            background: heatPct > 80 ? 'var(--danger)' : heatPct > 60 ? 'var(--warning)' : 'var(--accent)',
            transition: 'width 0.5s',
          }} />
        </div>
      </div>

      <div style={{ width: 1, height: 28, background: 'var(--border)' }} />

      {/* Regime */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Regime
        </span>
        <span style={{ color: regimeColor(), fontWeight: 600 }}>
          {regime ?? '—'}
          {regimeMultiplier != null && (
            <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: 6 }}>
              ×{regimeMultiplier.toFixed(2)}
            </span>
          )}
        </span>
      </div>

      <div style={{ width: 1, height: 28, background: 'var(--border)' }} />

      {/* Equity */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Equity
        </span>
        <span style={{ fontWeight: 600 }}>
          £{currentEquity.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      </div>
    </div>
  )
}
