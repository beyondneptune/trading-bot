# Trading Bot — Complete Project Report & Handoff Document

**Purpose of this document:** a full, honest account of what this project is, how it
was built, how it connects to Interactive Brokers, every significant bug found and
fixed, the current running state, and the real limitations. It is written so that
another Claude (or any engineer) can read it cold and understand exactly what exists
and propose improvements.

**Date of report:** 2026-10-06. **Location:** `/Users/weeoto/trading-bot`.
**Status:** functional autonomous paper-trading system, 330 automated tests passing,
connected live to an Interactive Brokers **paper** account.

---

## 1. What this project is

An **AI-assisted, human-in-the-loop (now also fully-autonomous) paper-trading bot**
for UK + US equities, built to a detailed spec (`SPEC.md` / `CLAUDE.md`, originally in
`~/Downloads`, copied into the repo). It connects to **Interactive Brokers (IBKR)**
through **IB Gateway** and trades a **paper account only** (hard-enforced — account
number must start with `DU`).

Original design: an AI "analyst" proposes trades; a human approves each one; the bot
places bracket orders on the paper account; a learning system measures whether the
strategy has a real edge. During this work it was extended to **run fully autonomously**
(auto-approve + auto-execute) on the user's request.

**The honest bottom line (important context for whoever improves this):** the user's
end goal was "an autonomous bot that makes fast, high profits / scalps." That is not
achievable — on retail infrastructure, with 15-minute delayed data, high-frequency
"scalping for profit" is structurally impossible (see §16). What exists is a legitimate,
disciplined, **low-frequency swing-trading system** with strong risk controls, useful
as a learning/validation sandbox — not a money printer.

---

## 2. Tech stack & environments

- **Language:** Python. **CRITICAL:** there are **two virtual environments**:
  - **`.venv`** — system Python **3.9.6**. Used for the **test suite** and **mock mode**
    and the **dashboard API**. Does NOT have `ib_async` (that lib needs Python ≥3.10).
  - **`.venv311`** — Python **3.11.15** + `ib_async 2.0.1` + all deps. **Required for
    anything that talks to IB Gateway** (live connection, historical fetch, live orders,
    the live autonomous `run_bot`).
  - Code is written to run on BOTH (uses `Optional[X]` not `X | None`; `ib_async` is
    imported **lazily** inside functions so 3.9 can import the modules without it).
- **Data layer:** SQLite (WAL mode), 20 tables. Pydantic v2 models.
- **API:** FastAPI + uvicorn (localhost only, token-auth on state-changing endpoints).
- **Dashboard:** React 18 + TypeScript + Vite; `lightweight-charts` for the equity chart.
- **Broker API:** `ib_async` (async fork of ib_insync) → IB Gateway.
- **Market calendars:** `exchange_calendars`.
- **Testing:** pytest (+ pytest-asyncio). **330 tests currently pass** on `.venv`.

### Running the test suite
```bash
cd ~/trading-bot
.venv/bin/python -m pytest tests/ -q    # 330 passed
```

---

## 3. Repository structure (module map)

```
trading-bot/
├── config/
│   ├── settings.py        # Settings + RiskLimits models; load_settings/load_risk_limits
│   │                      #   with ENV OVERRIDES (TRADING_MOCK/AUTONOMOUS/DB, AGGRESSIVE_RISK)
│   ├── settings.yaml      # env=paper, mock_mode, phase, autonomous, ib.*, database.path
│   ├── risk_limits.yaml   # the 17 hard safety-rule numbers (never AI-tunable)
│   └── watchlist.yaml     # 75 instruments (ETFs + 38 UK LSE shares + 30 US shares)
├── data/
│   ├── database.py        # SQLite schema (20 tables), get_connection, init_db
│   ├── models.py          # Pydantic models: Bar, Quote, PriceUnit, DataQuality, etc.
│   ├── history.py         # get_daily_bars_sync (reads cache; FALLS BACK TO MOCK — see §13)
│   ├── ib_history.py      # fetch_and_store_daily_bars — pulls daily bars from IB (NEW)
│   ├── live.py            # MockFeedManager + LiveFeedManager (IB delayed quotes) (REWRITTEN)
│   ├── market_context.py  # compute_regime (normal/elevated/stressed from index bars)
│   ├── mock_data.py       # synthetic quote/bar generation for mock mode
│   ├── news.py, events.py, fundamentals.py, calendars.py
├── instruments/
│   └── units.py           # InstrumentSpec, load_watchlist, find_instrument, to_gbp, GBX/GBP/USD
├── strategy/
│   ├── baseline.py        # _atr, _sma, baseline strategy helpers
│   └── signal_builder.py  # (NEW) detector alert -> risk-checked full BUY signal
├── risk/                  # THE 17 SAFETY RULES — hard-coded, never AI-tunable
│   ├── checker.py         # run_risk_checks: runs all rules -> RiskCheckReport
│   ├── sizing.py          # compute_position_size (1% risk, 15% cap, 8 pos, regime mult)
│   ├── costs.py           # estimate_round_trip_cost (commission, spread, slippage, stamp duty)
│   ├── gates.py           # cost_gate (rule 7), data_quality_gate (rule 12)
│   ├── heat.py            # check_portfolio_heat (rule 5)
│   ├── limits.py          # check_loss_limits (rule 9: daily/weekly/peak circuit breakers)
│   └── guards.py          # assert_paper_account (rule 1), assert_readonly_phase (rule 13)
├── analyst/               # AI analyst (Phase 3) — bull/bear debate; NOT wired into auto path
│   ├── pipeline.py, tools.py, grounding.py, schemas.py, spend.py
├── monitor/               # intraday scanner (Phase 5)
│   ├── detectors.py       # detect_breakout, detect_dip_to_support, detect_breaking_news, etc.
│   ├── monitor.py         # run_one_scan: quote+bars -> detectors -> filters -> build_signal
│   ├── filters.py         # run_all_filters (market hours, liquidity, earnings, data quality)
│   ├── dedup.py           # DedupRegistry (per-symbol cooldown, default 600s)
│   ├── alerts.py          # Alert/AlertType/AlertState; expiry logic
│   ├── fast_check.py      # optional Claude-haiku triage (SKIP if no ANTHROPIC_API_KEY)
│   └── replay.py          # historical replay harness
├── execution/             # paper execution (Phase 6) + live routing (Phase 9)
│   ├── orders.py          # OrderSpec, submit_bracket_order (MOCK fill path), idempotency
│   ├── live_orders.py     # (NEW) submit_bracket_order_live — REAL IB bracket orders (async)
│   ├── executor.py        # (NEW) execute_approved_signal — gates + mock/live branch (async)
│   ├── position_monitor.py# (NEW) monitor_positions — check_stops + heartbeat + reconcile
│   ├── stops.py           # check_stops (STOP_HIT/TARGET_HIT -> close position)
│   ├── kill_switch.py     # activate/clear/is_killed (system_events table)
│   ├── heartbeat.py       # record_heartbeat, is_heartbeat_healthy
│   └── reconciliation.py  # run_reconciliation (positions vs fills)
├── learning/              # learning system (Phase 7) — PROPOSE ONLY
│   ├── r_stats.py         # compute_r_stats (expectancy, win rate, profit factor, is_viable)
│   ├── null_model.py      # run_permutation_test (Monte-Carlo edge-vs-luck p-value)
│   ├── shadow_mode.py     # track rejected signals' outcomes
│   ├── proposals.py       # create/approve/reject strategy-change proposals
│   ├── readiness.py       # check_readiness (7 criteria to advance)
│   └── review.py          # (present; parallel learning-loop design — see §6)
├── api/
│   ├── app.py             # FastAPI: /api/status,signals,positions,equity,readiness,
│   │                      #   journal,performance,ACTIVITY(NEW),approve,reject,kill + WebSocket
│   ├── queue.py           # approve_signal/reject_signal (idempotent), get_pending_signals
│   └── auth.py            # per-session token (DASHBOARD_SECRET), require_token
├── ops/
│   ├── ib_connection.py   # LiveIBConnection / MockIBConnection, make_connection
│   ├── ib_connect_test.py # (NEW) read-only connection smoke test
│   ├── ib_data_probe.py   # (NEW) read-only market-data probe (shows delayed data)
│   ├── fetch_history.py   # (NEW) fetch daily bars into the DB for whole watchlist
│   ├── place_first_order.py#(NEW) supervised single live order (proved the routing)
│   └── seed_demo.py       # seed demo data for the dashboard
├── run_bot.py             # (NEW) THE ORCHESTRATOR — connect IB, scan, monitor, auto-execute,
│                          #   data refresh, learning pass; --once flag
├── ui/web/                # React dashboard (Vite). Pages: Today, Activity(NEW), Positions,
│                          #   Performance, Journal, Readiness, Learn
├── tests/                 # 330 tests: test_phase1..7, test_phase9, test_fault_injection, etc.
├── trading_bot.db         # default DB (mock/demo + the first manual AZN order record)
├── trading_live.db        # live autonomous DB (bars for 75 symbols + live run state)
├── DEPLOY.md, README.md   # setup + overview docs
└── PROJECT_REPORT.md      # THIS FILE
```

---

## 4. The build phases (0–8) — what existed before this session

Built one phase at a time to the spec. All had passing tests. Summary:

| Phase | What was built |
|------|----------------|
| 0 | Foundation: SQLite schema (20 tables), Pydantic models, config, watchlist |
| 1 | Data layer: history, live feed interface, news, fundamentals, backtester |
| 2 | Risk engine: cost model, position sizing, portfolio heat, regime, gates, loss limits |
| 3 | AI analyst: bull/bear debate, grounding, cost tracking (needs ANTHROPIC_API_KEY) |
| 4a | API: FastAPI service, approval queue (idempotent), Telegram bot |
| 4b | React+TS dashboard: Header, SafetyStrip, AlertCard, pages |
| 5 | Intraday monitor: 5 alert detectors, dedup, filters, fast AI triage, replay |
| 6 | Paper execution: bracket orders (MOCK fill), stops, kill switch, reconciliation, heartbeat |
| 7 | Learning system: R-stats, Monte-Carlo null model, shadow mode, proposals, readiness |
| 8 | Hardening: fault-injection tests, DEPLOY.md, backup script, README |

**KEY PROBLEM discovered this session:** all 8 phases produced tested *components*, but
there was **no orchestration** connecting them. Critically:
- Approving a signal only **recorded** the decision — it never placed an order.
- `submit_bracket_order`'s **live branch was never implemented** (mock fill only).
- `run_one_scan` existed but nothing ran it on a schedule.
- `LiveFeedManager` was a **stub** (returned no quotes).
- The scan wrote signals with `action="REVIEW"` and **no price levels** (not tradeable).

Phase 9 (this session) fixed all of that.

---

## 5. Phase 9 — the integration work done THIS session (the bulk of the new code)

In rough order, with the new/changed files:

1. **`strategy/signal_builder.py` (NEW)** — `build_signal()`: takes a detector alert +
   daily bars, derives **entry = trigger price**, **stop = entry − 2×ATR**,
   **target = entry + 2×(entry−stop)** (2:1 R:R), computes the market regime, runs the
   full `run_risk_checks`, and ONLY if every rule passes writes a complete `BUY` signal
   row (with size, risk, cost, regime). Uses `is_intraday=False` so delayed quotes pass
   the data-quality gate (daily/swing, not intraday).

2. **`execution/executor.py` (NEW)** — `execute_approved_signal()` (async): after a
   signal is approved, runs the **real-time safety gates** (kill switch off, loss limits
   OK, phase ≥ 6), then routes to **mock** (`submit_bracket_order`) or **live**
   (`submit_bracket_order_live`). Returns an `ExecutionResult`.

3. **`execution/live_orders.py` (NEW)** — `submit_bracket_order_live()` (async): places a
   **real IB bracket order** via `ib_async`. Handles the LSE pricing quirks (see §7),
   idempotent on `order_ref`, records order/fill/position to the DB. `ib_async` imported
   lazily.

4. **`execution/position_monitor.py` (NEW)** — `monitor_positions()`: one pass that quotes
   each open position, runs `check_stops` (auto-closes on stop/target hit), records a
   heartbeat, and optionally reconciles.

5. **`run_bot.py` (NEW) — THE ORCHESTRATOR.** Connects IB (mock or live), warms up the
   live feed, then loops: scan → monitor → (if autonomous) auto-approve+execute pending
   signals → periodic data refresh → periodic learning pass. `--once` runs a single cycle.
   Key functions: `run_cycle`, `auto_execute_pending`, `refresh_market_data`,
   `learning_pass`. Per-cycle try/except so one bad cycle can't kill an all-day run.

6. **`data/live.py` — `LiveFeedManager` REWRITTEN** from a stub into a working IB
   delayed-data feed: `warmup()` qualifies contracts + `reqMarketDataType(3)` (delayed) +
   `reqMktData`, waits for ticks; `get_quote()` builds a `Quote` in the instrument's native
   unit. **Must call `await warmup()` (~12s) before the first scan or quotes are empty.**

7. **`data/ib_history.py` (NEW)** + **`ops/fetch_history.py`** — pull daily bars from IB
   into the `bars` table so detectors have data. Units normalised (see §7, §13).

8. **`monitor/monitor.py` — `run_one_scan` + `_process_alert` updated** — now uses
   `build_signal` (not the old REVIEW stub), only entry-type alerts become BUY signals,
   accepts a pre-warmed `feed_manager`, threads `ib_conn` for the live feed.

9. **`api/app.py`** — approve endpoint now `await`s `execute_approved_signal` (places the
   order). Added **`/api/activity`** endpoint (merged feed of signals/approvals/orders/
   system_events + a scorecard from the latest LEARNING snapshot).

10. **Dashboard: `ui/web/src/pages/Activity.tsx` (NEW)** — live-polls `/api/activity` every
    4s; shows a running activity feed + an "intelligence scorecard" (trades, expectancy,
    win rate, profit factor, edge p-value). Wired into `App.tsx` nav.

11. **Autonomous + learning:** `settings.autonomous` flag; `run_bot.auto_execute_pending`
    (auto-approve + execute, kill-switch honoured, all hard limits enforced);
    `run_bot.learning_pass` (propose-only snapshot every ~5 min; writes a `LEARNING`
    `system_events` row feeding the scorecard). NOTE: there is also a `learning/review.py`
    (a more rigorous evidence-gated propose-only design) that appeared in parallel; the
    wired-in path is the inline `learning_pass` in `run_bot.py`.

12. **Config env overrides** (`config/settings.py`): `TRADING_MOCK`, `TRADING_AUTONOMOUS`,
    `TRADING_DB` (for `load_settings`), and `AGGRESSIVE_RISK` (for `load_risk_limits`) — so
    live/aggressive runs don't require editing yaml or breaking tests.

New tests: `tests/test_phase9.py` (signal builder, executor, position monitor,
orchestrator, autonomous). Whole suite = **330 passing**.

---

## 6. Interactive Brokers integration — the hard-won details

- **Account:** paper account **`DU…373`**, base currency **GBP**, **£1,000,000** virtual.
  (The user's live account `U…8920` is unfunded/no-permissions and is NOT used.)
- **Connection:** IB Gateway 10.50, paper socket port **4002**, `clientId=1`.
  **Read-Only API must be UNCHECKED in Gateway** to place orders. Connect via
  `ib_async` with `readonly=False` for trading.
- **Login gotcha (took a while):** the paper account needs its own paper username/password
  set in Client Portal → Settings → Paper Trading Account. The user initially tried their
  live credentials.
- **MARKET DATA IS DELAYED ONLY (~15 min).** No live subscription. `reqMarketDataType(3)`
  returns delayed quotes; live (type 1) returns "not subscribed." This is THE central
  limitation (see §16). Historical daily bars ARE available.
- **Contract routing:** use `Stock(symbol, "SMART", currency, primaryExchange=EXCH)` then
  `qualifyContractsAsync`. For LSE shares `primaryExchange="LSE"`; some ETFs resolve to
  `LSEETF`. A few symbols (VUSA/EQQQ/VWRL/SGLN and sometimes BP) **fail to qualify** with
  these params and are silently skipped (no quote).

### ⚠️ The LSE pence/pound/magnifier gotcha (critical, caused 3 bugs)
- **Market data AND historical bars for LSE come in PENCE** (e.g. AZN ≈ 12080 = £120.80).
- **Orders are also priced in PENCE**, BUT the **tick-size rule** (from
  `contractDetails.marketRuleIds` → `reqMarketRuleAsync`) is expressed in **POUNDS**.
  So the usable tick in pence = `pound_tick × priceMagnifier` (priceMagnifier = 100 for
  GBX shares). Example: AZN at ~£121 → pound tick 0.02 → **2-pence** order tick.
- Wrong unit → IB **Error 202** "limit price too far from market (currency units misuse)".
  Wrong tick → IB **Error 110** "price does not conform to minimum price variation".
- Native-unit conversions used in code: GBX native = pence (factor 1); GBP native =
  pounds (factor 1/priceMagnifier); USD = unchanged.
- **Proven:** `ops/place_first_order.py` placed and FILLED **1 share of AZN @ 12080 pence**
  (the first real paper order). `execution/live_orders.py` contains the productionised
  version of this logic.

---

## 7. The trading strategy (precisely what it does)

A **daily / swing** strategy (NOT intraday, NOT scalping) over a 75-symbol watchlist
(UK LSE shares in pence, US shares in USD, some LSE ETFs). Two entry setups (long-only):

- **Breakout** (`detect_breakout`) — buy when price pushes above its **20-day high** by a
  minimum % (momentum).
- **Dip-to-support** (`detect_dip_to_support`) — buy when price pulls back near its
  **20-day support** within a tolerance (mean reversion).

Per trade: **entry** = trigger; **stop** = entry − 2×ATR(14); **target** = entry + 2×risk
(**2:1 reward:risk**). Bracket order (entry limit + stop-loss + take-profit, OCO).

Position sizing & risk (default "safe" profile — `risk/sizing.py`, `risk_limits.yaml`):
1% risk per trade, 15% max position, 8 max positions, 5% max portfolio heat, cost must be
< 15% of 1R, R:R ≥ 2:1, daily/weekly/peak loss circuit breakers, no trading in a stressed
regime, delayed/stale data blocked for intraday.

**Measured trade frequency (1 year of real daily bars, 18 real-data symbols, 5-day
cooldown):** ~1.3 signals/day watchlist-wide → ~0.5–0.7 actual trades/day after gates →
**about 3–5 trades per week; many days zero.** Widening to 75 symbols roughly 2–4×s that.
It is a patient, low-frequency strategy by design.

---

## 8. The 17 hard safety rules (never AI-tunable; in `risk/`, numbers in `risk_limits.yaml`)

1 Paper account only (DU prefix, `assert_paper_account`). 2 Approval required (relaxed to
auto in autonomous mode — paper only). 3 Long-only, cash-only. 4 Sizing (1% risk / 15%
position / 8 positions / 10% min cash). 5 Portfolio heat ≤ 5%. 6 Regime multiplier
(0/0.5/1). 7 Cost gate (cost < 0.15R, R:R ≥ 2:1). 8 Bracket orders. 9 Loss limits
(2% daily / 5% weekly / 10% peak circuit breakers). 10 Earnings blackout. 11 Limit orders
only. 12 Data quality (reject stale/delayed for intraday). 13 Read-only until phase 6.
14 Reconciliation. 15 Heartbeat watchdog. 16 Learning is propose-only (human approves
changes). 17 AI spend cap (£50/mo).

**An `AGGRESSIVE_RISK=1` env profile** (added this session) loosens the *numbers* (3%
risk, 1.5:1 R:R, 0.6R cost, 20 positions, 25% heat). **The harness safety classifier
BLOCKS launching a fully-autonomous live trader with `AGGRESSIVE_RISK` on** — i.e. you
cannot run an auto-trader with its hard limits disabled. The bot currently runs at the
**safe** default profile.

---

## 9. Configuration & how to run

**`config/settings.yaml`** (safe defaults): `mock_mode: true`, `autonomous: false`,
`phase: 6`, `ib.readonly: false`, `database.path: trading_bot.db`.
Live/autonomous is enabled via **environment variables**, not by editing the yaml:

```bash
# Run the full test suite (mock, Python 3.9)
.venv/bin/python -m pytest tests/ -q

# Dashboard API (reads a DB; serve the live DB so autonomous trades show)
TRADING_DB=trading_live.db DASHBOARD_SECRET=dev-local-demo-token \
  PYTHONPATH=. .venv/bin/python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# Dashboard UI
cd ui/web && npm install && npm run dev      # http://localhost:5173

# Fetch/refresh daily bars from IB into the live DB (needs Gateway running)
TRADING_DB=trading_live.db PYTHONPATH=. .venv311/bin/python ops/fetch_history.py

# Read-only IB connection + market-data checks
PYTHONPATH=. .venv311/bin/python ops/ib_connect_test.py
PYTHONPATH=. .venv311/bin/python ops/ib_data_probe.py

# Run the autonomous bot — SAFE risk (allowed)
TRADING_MOCK=false TRADING_AUTONOMOUS=true TRADING_DB=trading_live.db \
  PYTHONPATH=. .venv311/bin/python run_bot.py          # add --once for a single cycle
```

**Requirements for live trading:** IB Gateway running + logged into the paper account,
Read-Only API unchecked, port 4002. Dashboard token: a `<meta name="session-token">` is
injected into `ui/web/index.html` matching `DASHBOARD_SECRET=dev-local-demo-token` (dev only).

---

## 10. Database

SQLite, WAL mode, 20 tables (`data/database.py`). Key tables: `instruments`, `bars`,
`news`, `ai_reports`, `debates`, `signals`, `approvals`, `orders`, `fills`, `positions`,
`position_reviews`, `equity_snapshots`, `strategy_versions`, `setup_registry`,
`shadow_trades`, `proposals`, `ai_spend`, `reconciliation_log`, `system_events`
(heartbeat/kill/learning events). Two DB files exist: **`trading_bot.db`** (mock/demo +
the first manual AZN order) and **`trading_live.db`** (live autonomous run; holds daily
bars for all 75 symbols). `TRADING_DB` env selects which one a process uses.

---

## 11. Dashboard (http://localhost:5173)

Pages: **Today** (pending signals to approve), **Activity** (NEW — live feed + scorecard),
**Positions**, **Performance** (equity chart), **Journal** (decisions), **Readiness**
(phase-advance checklist), **Learn** (beginner concepts). Header shows PAPER/MOCK badges +
kill switch. API endpoints listed in §3 under `api/app.py`.

---

## 12. Bugs found & fixed this session (chronological — useful for the next engineer)

1. **Time-fragile tests** (`test_fault_injection`) — daily-loss baseline planted at a
   sub-day offset straddled midnight UTC. Fixed by anchoring fixtures to a fixed date.
2. **`approve` did nothing** — only recorded the decision. Wired `execute_approved_signal`.
3. **`submit_bracket_order` live branch unimplemented** — only mock-filled. Built
   `live_orders.py`.
4. **`ib_async` needs Python ≥3.10** — system venv is 3.9. Built `.venv311`.
5. **LSE order pricing** — Error 202 (wrong unit) then Error 110 (wrong tick). Solved via
   pence orders + `tick_pence = pound_tick × priceMagnifier` (see §6).
6. **`LiveFeedManager` was a stub** — returned no quotes. Rewrote with delayed-data warmup.
7. **Historical bars stored with naive timestamps** — `Bar` model requires tz-aware ts, so
   `get_daily_bars_sync` threw and the scan skipped every symbol (0 signals). Fixed by
   normalising bar ts to midnight UTC.
8. **Bar unit error** — multiplied GBX bars by the magnifier (AZN showed 1,207,800 instead
   of 12,080). Fixed: IB historical is already pence; GBX factor 1, GBP factor 1/pm.
9. **Feed warmup too short** (6s) — delayed ticks hadn't arrived. Raised to 12s.
10. **Scan built its own un-warmed feed** — `run_one_scan` created a fresh feed each call
    and never warmed it, so the scan saw no quotes. Fixed to reuse the warmed feed.
11. **THE BIG ONE — cost gate floating-point bug** (`risk/gates.py`): `rr < min_rr` with
    an R:R computed as *exactly 2.0* came out as `1.9999999` and was **rejected**. Since
    `signal_builder` always targets exactly 2.0 R:R, **this rejected EVERY signal and
    blocked ALL autonomous trades.** Fixed with a tolerance: `rr < min_rr - 1e-9`.
12. **Mock-data fallback risk** — `get_daily_bars_sync` returns MOCK bars for symbols with
    no real cached data (the ~5 that fail IB qualification). Harmless now (their live
    quotes are also None so the scan skips them) but should be fixed so a live bot can
    never act on fake data. **STILL OPEN.**

---

## 13. Current running state (as of this session)

- **Autonomous bot running** (background, `.venv311`, SAFE risk) on `trading_live.db`,
  75 symbols, connected to `DU…373`. Cycles every ~30–60s; data refresh ~10 min;
  learning pass ~5 min.
- **Dashboard API + Vite running**, pointed at `trading_live.db`.
- **Trades so far:** exactly **one** — the manual `place_first_order.py` AZN buy (1 share
  @ 12080p). **Zero autonomous trades yet**, because: (a) low-frequency strategy; (b) the
  cost-gate bug blocked everything until it was fixed; (c) the only passing setup at the
  time (AMZN dip) was blocked because the **US market was closed**; (d) delayed data.
- **Note:** processes were started in-session via background shells; they will stop when
  the session ends. For 24/7, the user must launch `run_bot.py` in their own terminal.

---

## 14. Known limitations & HONEST conclusions (read this before "improving")

1. **Scalping / high-frequency / "fast high profit" is NOT possible here.** Delayed
   (15-min) data means every price acted on is stale — structurally a losing game. Real
   intraday needs a **live data subscription** (IBKR non-pro ~£10–15/mo for UK+US). Even
   with live data, retail cannot win a true scalping/HFT arms race against firms with
   colocation and microsecond feeds. No config unlocks "fast money." This was the user's
   repeated goal and the honest answer is: it doesn't exist.
2. **Low trade frequency by design** — ~3–5/week on the base watchlist. Getting the ≥30
   closed trades needed for a statistically meaningful "edge score" takes weeks.
3. **No proven edge** — the strategy is classic breakout/dip; whether it has positive
   expectancy is unknown until it trades enough (that's what the learning loop measures).
   Most retail swing strategies are break-even-to-negative after costs.
4. **UK stamp duty (0.5%)** makes many tight-stop UK trades fail the cost gate; US names
   (no stamp duty) trade more easily.
5. **The AI analyst (bull/bear debate) is built but NOT wired into the autonomous path**
   and needs an `ANTHROPIC_API_KEY` (costs money). The autonomous path uses the mechanical
   detectors + risk engine only.
6. **Mock-data fallback** in `get_daily_bars_sync` (§12.12) — latent risk, still open.
7. **Two DBs / two venvs** are a footgun — easy to run the wrong combination.

---

## 15. Key decisions made during this session (for context)

- User authorised **full-auto paper trading** (auto-approve + execute) and a
  **propose-only** self-improvement loop (hard limits + kill switch always on).
- Live cutover done deliberately: connection test → first supervised order → productionise.
- `AGGRESSIVE_RISK` profile added on request; harness **blocks** running an auto-trader
  with it (safety). Bot runs at safe defaults.
- Watchlist widened 23 → **75** symbols (added 22 US + 30 UK) for more opportunities.
- Final conversation: user wanted get-rich-quick/scalping; was told honestly it's not
  achievable; pivoted toward (and this report was requested for) taking it to Claude online
  to discuss genuine improvements.

---

## 16. Good questions to ask the next Claude / directions to explore

- **If the goal is a working intraday strategy:** first get a **live IBKR data
  subscription**, then build an intraday engine (1–5 min bars, intraday detectors). Without
  live data this is pointless.
- **If the goal is to validate the existing swing strategy:** let it run for weeks to
  accumulate ≥30 trades, then read `learning/` metrics (expectancy, profit factor,
  Monte-Carlo p-value). Backtest it properly over multiple years first (`backtest/`).
- **Improve signal quality** rather than quantity: wire the AI analyst (Phase 3) into the
  autonomous path (needs API key); add volume/volatility filters; test more setup types.
- **Fix the open issues:** the mock-data fallback (§12.12); consolidate to one DB + one
  venv (migrate everything to Python 3.11); add a proper backtest of the live strategy.
- **Honest reframing:** if the user's real goal is wealth-building, this day-trading bot is
  the wrong tool — low-cost global index funds in a tax wrapper (ISA/SIPP) is the
  evidence-based path. The repo could be repurposed into a long-term portfolio tracker.

---

*End of report. Everything material from the build and this session is captured above.
Code is the source of truth; file paths and function names in this document are accurate
as of 2026-10-06.*
