/**
 * API client — reads session token from meta tag injected by the server.
 * All state-changing requests include X-Session-Token.
 */

const SESSION_TOKEN = (
  document.querySelector('meta[name="session-token"]') as HTMLMetaElement | null
)?.content ?? ''

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.method && options.method !== 'GET'
      ? { 'X-Session-Token': SESSION_TOKEN }
      : {}),
    ...(options.headers as Record<string, string> | undefined),
  }
  const res = await fetch(path, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  status: () => request<{ mode: string; phase: number; mock_mode: boolean }>('/api/status'),
  signals: () => request<{ signals: Signal[]; mode: string }>('/api/signals'),
  positions: () => request<{ positions: Position[] }>('/api/positions'),
  equity: () => request<{ snapshots: EquitySnapshot[] }>('/api/equity'),
  performance: () => request<Performance>('/api/performance'),
  activity: () => request<ActivityResponse>('/api/activity'),

  approve: (signal_id: number, reason?: string, confidence?: number, mood?: string) =>
    request<{ status: string; order_ref: string }>('/api/approve', {
      method: 'POST',
      body: JSON.stringify({ signal_id, reason, confidence, mood }),
    }),

  reject: (signal_id: number, reason?: string) =>
    request<{ status: string }>('/api/reject', {
      method: 'POST',
      body: JSON.stringify({ signal_id, reason }),
    }),

  kill: () =>
    request<{ status: string }>('/api/kill', {
      method: 'POST',
      body: JSON.stringify({ confirm: true }),
    }),
}

// Types
export interface Signal {
  id: number
  symbol: string
  exchange: string
  signal_type: string
  action: string
  entry_price: number | null
  stop_price: number | null
  target_price: number | null
  size: number | null
  risk_gbp: number | null
  reward_risk: number | null
  cost_r: number | null
  regime: string | null
  regime_multiplier: number | null
  valid_until: string | null
  created_at: string
}

export interface Position {
  id: number
  symbol: string
  exchange: string
  quantity: number
  avg_cost: number
  currency: string
  stop_price: number | null
  target_price: number | null
  opened_at: string
}

export interface EquitySnapshot {
  id: number
  equity_gbp: number
  cash_gbp: number
  open_positions: number
  portfolio_heat: number
  snapped_at: string
}

export interface Performance {
  mode: string
  total_ai_spend_usd: number
  total_trades: number
}

export interface ActivityEvent {
  time: string
  type: string
  text: string
}

export interface ActivityResponse {
  events: ActivityEvent[]
  score: Record<string, number | boolean>
  mode: string
}
