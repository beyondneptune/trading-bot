import { useEffect, useRef, useState } from 'react'
import { createChart, ColorType, LineStyle } from 'lightweight-charts'
import { api, EquitySnapshot, Performance as PerfData } from '../api/client'

export function Performance() {
  const chartRef = useRef<HTMLDivElement>(null)
  const [snapshots, setSnapshots] = useState<EquitySnapshot[]>([])
  const [perf, setPerf] = useState<PerfData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.equity(), api.performance()])
      .then(([eq, p]) => {
        setSnapshots(eq.snapshots.slice().reverse()) // oldest first for chart
        setPerf(p)
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!chartRef.current || snapshots.length === 0) return

    const chart = createChart(chartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#1c2128' },
        textColor: '#7d8590',
      },
      grid: {
        vertLines: { color: '#30363d', style: LineStyle.Dotted },
        horzLines: { color: '#30363d', style: LineStyle.Dotted },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
      width: chartRef.current.clientWidth,
      height: 300,
    })

    const series = chart.addLineSeries({
      color: '#3fb950',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    const data = snapshots.map(s => ({
      time: Math.floor(new Date(s.snapped_at).getTime() / 1000) as unknown as string,
      value: s.equity_gbp,
    }))

    series.setData(data as Parameters<typeof series.setData>[0])
    chart.timeScale().fitContent()

    const resizeObserver = new ResizeObserver(() => {
      if (chartRef.current) {
        chart.applyOptions({ width: chartRef.current.clientWidth })
      }
    })
    resizeObserver.observe(chartRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [snapshots])

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200 }}>
        <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>Loading…</span>
      </div>
    )
  }

  return (
    <div style={{ padding: 20 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Performance</h2>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
        {[
          { label: 'Total Trades', value: String(perf?.total_trades ?? 0) },
          { label: 'AI Spend', value: `$${(perf?.total_ai_spend_usd ?? 0).toFixed(4)}` },
          { label: 'Mode', value: perf?.mode ?? 'PAPER' },
          { label: 'Snapshots', value: String(snapshots.length) },
        ].map(({ label, value }) => (
          <div key={label} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 6, padding: '12px 16px', minWidth: 120,
          }}>
            <div style={{ color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
              {label}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 18 }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Equity chart */}
      <div style={{
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderRadius: 6, overflow: 'hidden',
      }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontSize: 13, fontWeight: 600 }}>
          Equity Curve (GBP)
        </div>
        {snapshots.length === 0 ? (
          <div style={{ padding: '60px 0', textAlign: 'center', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            No equity snapshots yet
          </div>
        ) : (
          <div ref={chartRef} />
        )}
      </div>
    </div>
  )
}
