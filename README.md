# AI Trading Analyst — Paper Account Bot

A system that watches the UK and US stock markets, generates trade
ideas using AI, and asks you to approve or reject each one before
anything happens. It runs against an Interactive Brokers **paper**
(simulated) account only.

You stay in control at all times. The AI recommends. You decide.

---

## What it does, in plain English

1. **Watches your watchlist** — checks prices and news throughout the day.
2. **Spots potential setups** — breakouts, planned entries, support dips,
   news events, positions under pressure.
3. **Runs an AI analyst** — a "bull" and a "bear" AI debate every idea.
   Neither can override the 17 hard-coded safety rules.
4. **Sends you a signal card** — you see the entry price, stop loss,
   profit target, risk in GBP, and cost as a fraction of risk (R).
5. **Waits for your approval** — nothing happens until you tap Approve
   or Reject in the dashboard or Telegram.
6. **Places a paper bracket order** — entry + stop + target, all at once.
7. **Tracks everything** — every decision you make, every P&L, the equity
   curve, and whether the AI's ideas were any good.
8. **Learns over time** — after 30 or more closed trades, the system
   analyses whether a setup has a real edge and proposes tweaks for you
   to approve.

---

## Safety rules (hard-coded, never AI-tunable)

| # | Rule |
|---|------|
| 1 | Paper account only — code refuses to run on a live account |
| 2 | You must approve every trade |
| 3 | Limit orders only — no market orders |
| 4 | Max 1% of equity at risk per trade; max 15% in one position; max 8 open positions |
| 5 | Portfolio heat cap 5% — total risk across all open positions |
| 6 | Stop loss required on every trade |
| 7 | Round-trip cost must be < 15% of 1R |
| 8 | Reward:risk must be ≥ 2:1 |
| 9 | Daily loss limit 2%; weekly drawdown 5%; peak-to-trough 10% |
| 10 | No trading during earnings blackout (5 days before/after) |
| 11 | Delayed or stale quotes rejected |
| 12 | No trading in stressed market regime (VIX-equivalent > 80th percentile) |
| 13 | Kill switch — one button cancels everything instantly |
| 14 | Reconciliation runs every 15 minutes — detects fill/position mismatches |
| 15 | Heartbeat watchdog — alerts if the bot goes silent for > 60 seconds |
| 16 | AI learning system can only propose changes; you must approve them |
| 17 | AI spend capped at £50/month |

---

## Project layout

```
trading-bot/
├── config/             Settings YAML, risk limits YAML, watchlist YAML
├── data/               Database schema (SQLite), data models, history, live feed, news
├── analyst/            AI analyst: tools, grounding, debate loop, cost tracking
├── risk/               All 17 safety checks — never touched by AI
├── monitor/            Intraday scanner: alert types, dedup, filters, detectors
├── execution/          Bracket orders, stop management, kill switch, reconciliation, heartbeat
├── learning/           R statistics, null model (Monte Carlo), shadow trades, proposals, readiness
├── api/                FastAPI service — signals, approvals, positions, equity, journal
├── ui/web/             React + TypeScript dashboard (Vite)
├── ops/                IB connection wrapper, backup script
├── tests/              311 tests — one file per phase
├── DEPLOY.md           Step-by-step setup guide
└── README.md           This file
```

---

## Quick start

See **[DEPLOY.md](DEPLOY.md)** for the full setup walkthrough.

Short version:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in your API keys
python3 -m pytest tests/ -q   # 311 tests should pass
uvicorn api.app:app --host 127.0.0.1 --port 8000 &
cd ui/web && npm install && npm run dev
```

Open http://localhost:5173.

---

## Build phases

The system was built in 8 phases. Each phase is locked until the previous
is solid. The `phase` setting in `config/settings.yaml` controls what runs.

| Phase | What was built |
|-------|---------------|
| 0 | Foundation: database (20 tables), models, config, watchlist |
| 1 | Data layer: history, live feed, news, fundamentals, backtester |
| 2 | Risk engine: cost model, position sizing, heat, regime, gates, limits |
| 3 | AI analyst: tools, grounding, bull/bear debate, cost tracking |
| 4a | Human interface: REST API, approval queue, Telegram bot |
| 4b | Dashboard: React+TS with equity chart, signal cards, journal |
| 5 | Intraday monitor: 5 alert types, dedup, filters, fast AI check |
| 6 | Paper execution: bracket orders, stops, kill switch, reconciliation |
| 7 | Learning system: R stats, Monte Carlo null model, shadow trades, readiness |
| 8 | Hardening: fault injection tests, deployment docs, backup |

---

## Technology choices (and why)

| Tech | Why |
|------|-----|
| Python 3.9 | Runs on stock macOS — no homebrew Python needed |
| SQLite WAL | Zero-dependency database; WAL mode allows concurrent reads |
| Pydantic v2 | All data structures validated at the boundary |
| FastAPI | Fast, type-safe REST API with automatic docs at /docs |
| React 18 + Vite | Fast dashboard with hot-reload during development |
| lightweight-charts | TradingView's chart library — zero cost for personal use |
| ib_async | Async wrapper around IB's TWS API |
| exchange_calendars | Authoritative market open/close times |

---

## Running tests

```bash
source .venv/bin/activate
python3 -m pytest tests/ -v     # all 311 tests
python3 -m pytest tests/test_fault_injection.py -v   # Phase 8 safety tests only
```

All tests run in mock mode — no IB connection, no API keys, no cost.

---

## Backup

```bash
bash ops/backup.sh
```

Saves the database, config YAMLs, and `.env` to `backups/<timestamp>/`.
Keeps the last 14 backups (two weeks). Run daily with cron — see the
comment at the top of `ops/backup.sh`.

---

## Glossary (for beginner traders)

**R (Risk multiple)** — If you risk £100 on a trade and make £200,
that is +2R. If you lose the full £100, that is -1R. R multiples
let you compare wins and losses fairly regardless of position size.

**Stop loss** — An automatic exit that closes the trade if price moves
against you by a set amount. Every trade in this system requires one.

**Bracket order** — Three orders placed together: the entry, the stop
loss, and the profit target. Only one of the two exits can fill.

**Portfolio heat** — Total risk across all open trades, as a percentage
of equity. If every stop fired simultaneously, how much would you lose?
The bot caps this at 5%.

**Regime** — A description of overall market conditions. In a stressed
regime (high volatility), the bot reduces or stops sizing entirely.

**Paper account** — A simulated account that looks and behaves exactly
like a real IB account but uses fake money. Ideal for testing.

**GBX vs GBP** — UK shares are quoted in pence (GBX). 100 GBX = £1 GBP.
The bot converts everything to GBP for risk calculations.

**Expectancy** — Average outcome per trade in R units:
`(win_rate × avg_win) − (loss_rate × avg_loss)`. Positive expectancy
means the strategy makes money on average.

**Kill switch** — One button that instantly cancels every open order
and blocks any new approvals until you manually clear it.

**Shadow trade** — A rejected signal that the system continues to track
silently, so you can see whether you would have been right to reject it.
