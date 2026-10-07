import { useEffect, useState } from 'react'
import { api, ActivityEvent } from '../api/client'

const TYPE_COLOR: Record<string, string> = {
  ORDER: 'var(--accent)',
  SIGNAL: 'var(--text)',
  APPROVED: 'var(--gain)',
  REJECTED: 'var(--loss)',
  LEARNING: '#b794f6',
  HEARTBEAT: 'var(--text-dim)',
  KILL_SWITCH: 'var(--danger)',
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString()
  } catch {
    return iso
  }
}

function ScoreCard({ score }: { score: Record<string, number | boolean> }) {
  const has = score && Object.keys(score).length > 0
  const n = (score.n_trades as number) ?? 0
  const cell = (label: string, value: string, color?: string) => (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 14px' }}>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, fontFamily: 'var(--font-mono)', color: color ?? 'var(--text)' }}>{value}</div>
    </div>
  )
  return (
    <div style={{ marginBottom: 20 }}>
      <h3 style={{ fontSize: 14, marginBottom: 10 }}>Intelligence scorecard</h3>
      {!has || n === 0 ? (
        <div style={{ color: 'var(--text-muted)', fontSize: 13, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6, padding: 14 }}>
          No closed trades yet — a real score needs ≥30 completed trades. Metrics appear here as the bot trades.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
          {cell('Closed trades', String(n))}
          {cell('Expectancy (R)', String(score.expectancy_r ?? '—'),
            (score.expectancy_r as number) > 0 ? 'var(--gain)' : 'var(--loss)')}
          {cell('Win rate', score.win_rate != null ? `${Math.round((score.win_rate as number) * 100)}%` : '—')}
          {cell('Profit factor', String(score.profit_factor ?? '—'))}
          {cell('Edge (p-value)', score.edge_p_value != null ? String(score.edge_p_value) : `need ${30 - n} more`)}
          {cell('Viable?', score.viable ? 'YES' : 'not yet', score.viable ? 'var(--gain)' : 'var(--text-muted)')}
        </div>
      )}
    </div>
  )
}

export function Activity() {
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [score, setScore] = useState<Record<string, number | boolean>>({})
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      api.activity()
        .then(r => { if (alive) { setEvents(r.events); setScore(r.score); setErr(null) } })
        .catch(e => { if (alive) setErr(String(e)) })
    }
    load()
    const t = setInterval(load, 4000)  // live refresh every 4s
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <div style={{ padding: 20, maxWidth: 900, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <h2 style={{ fontSize: 16 }}>Bot Activity</h2>
        <span style={{ fontSize: 11, color: 'var(--gain)' }}>● live (updates every 4s)</span>
      </div>

      <ScoreCard score={score} />

      {err && <div style={{ color: 'var(--loss)', fontSize: 12, marginBottom: 10 }}>API: {err}</div>}

      <h3 style={{ fontSize: 14, marginBottom: 10 }}>What the bot is doing</h3>
      {events.length === 0 ? (
        <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          No activity yet. The bot scans every ~minute and logs what it finds here.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {events.map((e, i) => (
            <div key={i} style={{
              display: 'flex', gap: 12, alignItems: 'baseline',
              padding: '6px 10px', borderBottom: '1px solid var(--border)',
              fontFamily: 'var(--font-mono)', fontSize: 12,
            }}>
              <span style={{ color: 'var(--text-dim)', minWidth: 70 }}>{fmtTime(e.time)}</span>
              <span style={{ color: TYPE_COLOR[e.type] ?? 'var(--text-muted)', minWidth: 90, fontWeight: 600 }}>{e.type}</span>
              <span style={{ color: 'var(--text)' }}>{e.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
