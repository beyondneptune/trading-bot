export function Learn() {
  const concepts = [
    {
      title: 'Paper Trading',
      body: 'You are trading with pretend money on a real-time feed. Orders go to Interactive Brokers but nothing is real — no cash changes hands. This is the safest way to practise until you are consistently profitable.',
    },
    {
      title: 'Risk per Trade (R)',
      body: 'Every trade risks 1% of your account (this is "1R"). If your account is £10,000, you risk £100 per trade. Setting a stop loss limits your loss to that amount. This keeps you in the game even on a losing streak.',
    },
    {
      title: 'Reward:Risk Ratio',
      body: 'We only take trades with a target at least 2× further from entry than the stop. If you risk £100 (1R), you aim to make £200 (2R). Even if you win only half your trades, you still profit.',
    },
    {
      title: 'Stop Loss',
      body: 'A stop loss is a price below (for buys) or above (for sells) your entry where you exit if the trade goes wrong. It turns an unlimited loss into a known, small loss. Never move your stop to give a losing trade "more room".',
    },
    {
      title: 'Market Regime',
      body: 'The system measures whether the market is calm or turbulent. In high-volatility regimes it reduces position size automatically. This means you never over-bet during scary markets.',
    },
    {
      title: 'Portfolio Heat',
      body: 'Portfolio heat is the total risk across all open positions combined. Cap is 5% of equity. If you already have 4.5% heat open, the system will not approve new trades until you are below the limit.',
    },
    {
      title: 'AI Debate',
      body: 'Two AI analysts argue about every signal — one bull, one bear. A judge then decides. This reduces bias from a single opinion. You review the judgement and make the final call: approve or reject.',
    },
    {
      title: 'GBX vs GBP',
      body: 'London Stock Exchange shares are quoted in pence (GBX), not pounds (GBP). 7,000 GBX = £70. ETFs like CSPX trade in USD or GBP. The system converts everything to GBP before sizing — but always double-check the price looks sensible.',
    },
    {
      title: 'Kill Switch',
      body: 'The red KILL SWITCH button cancels all open orders instantly. Use it if you see something wrong — incorrect symbol, wrong size, system behaving oddly. In paper mode nothing is lost, but the habit matters.',
    },
    {
      title: 'Phase System',
      body: 'The bot is built in phases. Phases 0–4 = observation and approval only. Phase 6+ = auto execution. You will never reach Phase 6 until the readiness checklist passes a 3-month paper review. You are always in control.',
    },
  ]

  return (
    <div style={{ padding: 20 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Learn</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 20 }}>
        Plain-English explanations of everything the bot does.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
        {concepts.map(c => (
          <div key={c.title} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 6, padding: 16,
          }}>
            <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 14 }}>{c.title}</div>
            <div style={{ color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.6 }}>{c.body}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
