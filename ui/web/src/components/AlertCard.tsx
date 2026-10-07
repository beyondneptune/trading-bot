import { useState } from 'react'
import { Signal, api } from '../api/client'

interface AlertCardProps {
  signal: Signal
  onDecision: () => void
}

const ACTION_COLOR: Record<string, string> = {
  BUY: 'var(--gain)',
  SELL: 'var(--loss)',
  NO_TRADE: 'var(--text-muted)',
}

const MOOD_OPTIONS = ['Confident', 'Cautious', 'Sceptical', 'Curious']
const CONFIDENCE_MARKS = [1, 2, 3, 4, 5]

export function AlertCard({ signal, onDecision }: AlertCardProps) {
  const [showModal, setShowModal] = useState(false)
  const [approving, setApproving] = useState(false)
  const [reason, setReason] = useState('')
  const [confidence, setConfidence] = useState(3)
  const [mood, setMood] = useState('Cautious')
  const [rejectReason, setRejectReason] = useState('')
  const [rejecting, setRejecting] = useState(false)

  const expiresAt = signal.valid_until ? new Date(signal.valid_until) : null
  const expired = expiresAt ? expiresAt < new Date() : false

  const handleApprove = async () => {
    setApproving(true)
    try {
      await api.approve(signal.id, reason, confidence, mood)
      setShowModal(false)
      onDecision()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      alert('Approve failed: ' + msg)
    } finally {
      setApproving(false)
    }
  }

  const handleReject = async () => {
    setRejecting(true)
    try {
      await api.reject(signal.id, rejectReason)
      onDecision()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      alert('Reject failed: ' + msg)
    } finally {
      setRejecting(false)
    }
  }

  const rr = signal.reward_risk != null ? signal.reward_risk.toFixed(1) : '—'
  const costR = signal.cost_r != null ? (signal.cost_r * 100).toFixed(1) : '—'
  const risk = signal.risk_gbp != null ? `£${signal.risk_gbp.toFixed(0)}` : '—'

  return (
    <>
      <div style={{
        background: 'var(--bg-card)', border: `1px solid ${expired ? 'var(--text-dim)' : 'var(--border)'}`,
        borderRadius: 6, padding: 16, opacity: expired ? 0.5 : 1,
      }}>
        {/* Top row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
          <div>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 15 }}>
              {signal.symbol}
            </span>
            <span style={{ color: 'var(--text-muted)', fontSize: 12, marginLeft: 8 }}>
              {signal.exchange}
            </span>
            <span style={{
              marginLeft: 8, fontSize: 12, fontWeight: 700,
              color: ACTION_COLOR[signal.action] ?? 'var(--text)',
            }}>
              {signal.action}
            </span>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {signal.regime ?? ''}
            </div>
            {expiresAt && (
              <div style={{ fontSize: 11, color: expired ? 'var(--danger)' : 'var(--text-muted)' }}>
                {expired ? 'EXPIRED' : `Expires ${expiresAt.toLocaleTimeString()}`}
              </div>
            )}
          </div>
        </div>

        {/* Price levels */}
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8,
          background: 'var(--bg-panel)', borderRadius: 4, padding: '8px 12px', marginBottom: 12,
          fontFamily: 'var(--font-mono)', fontSize: 12,
        }}>
          <div>
            <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>ENTRY</div>
            <div>{signal.entry_price?.toFixed(2) ?? '—'}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>STOP</div>
            <div style={{ color: 'var(--loss)' }}>{signal.stop_price?.toFixed(2) ?? '—'}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-muted)', fontSize: 10 }}>TARGET</div>
            <div style={{ color: 'var(--gain)' }}>{signal.target_price?.toFixed(2) ?? '—'}</div>
          </div>
        </div>

        {/* Stats row */}
        <div style={{
          display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-muted)', marginBottom: 14,
          fontFamily: 'var(--font-mono)',
        }}>
          <span>Risk {risk}</span>
          <span>R:R {rr}</span>
          <span>Cost {costR}%R</span>
          <span>Qty {signal.size ?? '—'}</span>
        </div>

        {/* Action buttons */}
        {!expired && (
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => setShowModal(true)}
              style={{
                flex: 1, background: 'var(--accent)', color: '#fff',
                border: 'none', borderRadius: 4, padding: '8px 0', fontWeight: 700, fontSize: 13,
              }}
            >
              APPROVE
            </button>
            <button
              onClick={() => { setRejectReason(''); handleReject() }}
              disabled={rejecting}
              style={{
                flex: 1, background: 'transparent', color: 'var(--loss)',
                border: '1px solid var(--loss)', borderRadius: 4, padding: '8px 0', fontWeight: 700, fontSize: 13,
              }}
            >
              {rejecting ? 'REJECTING…' : 'REJECT'}
            </button>
          </div>
        )}
      </div>

      {/* Approve modal */}
      {showModal && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
        }}
          onClick={(e) => { if (e.target === e.currentTarget) setShowModal(false) }}
        >
          <div style={{
            background: 'var(--bg-panel)', border: '1px solid var(--border)',
            borderRadius: 8, padding: 24, width: 400, maxWidth: '90vw',
          }}>
            <h3 style={{ marginBottom: 16, fontSize: 15 }}>
              Approve {signal.symbol} {signal.action}
            </h3>

            {/* Confidence */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>
                Your confidence (1–5)
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                {CONFIDENCE_MARKS.map(v => (
                  <button
                    key={v}
                    onClick={() => setConfidence(v)}
                    style={{
                      width: 36, height: 36, borderRadius: 4,
                      background: confidence === v ? 'var(--accent)' : 'var(--bg-card)',
                      border: '1px solid var(--border)', color: 'var(--text)', fontWeight: 700,
                    }}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>

            {/* Mood */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>Mood</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {MOOD_OPTIONS.map(m => (
                  <button
                    key={m}
                    onClick={() => setMood(m)}
                    style={{
                      padding: '4px 12px', borderRadius: 4,
                      background: mood === m ? 'var(--bg-card)' : 'transparent',
                      border: `1px solid ${mood === m ? 'var(--accent)' : 'var(--border)'}`,
                      color: mood === m ? 'var(--text)' : 'var(--text-muted)', fontSize: 12,
                    }}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>

            {/* Reason */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>
                Journal note (optional)
              </div>
              <textarea
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="Why are you taking this trade?"
                rows={3}
                style={{
                  width: '100%', background: 'var(--bg-card)',
                  border: '1px solid var(--border)', borderRadius: 4,
                  color: 'var(--text)', padding: '8px 10px', fontSize: 12, resize: 'vertical',
                  fontFamily: 'var(--font)',
                }}
              />
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={handleApprove}
                disabled={approving}
                style={{
                  flex: 1, background: 'var(--accent)', color: '#fff',
                  border: 'none', borderRadius: 4, padding: '10px 0', fontWeight: 700, fontSize: 13,
                }}
              >
                {approving ? 'APPROVING…' : 'CONFIRM APPROVE'}
              </button>
              <button
                onClick={() => setShowModal(false)}
                style={{
                  padding: '10px 16px', background: 'transparent',
                  border: '1px solid var(--border)', borderRadius: 4,
                  color: 'var(--text-muted)', fontSize: 13,
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
