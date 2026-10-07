# Deployment Guide — AI Trading Analyst (Paper Account)

This guide takes you from a fresh Mac to a running paper-trading bot.
No prior trading or coding experience needed — every step is explained
in plain English.

---

## Prerequisites

| What | Version | Check with |
|------|---------|------------|
| macOS | 12+ | `sw_vers` |
| Python | 3.9+ | `python3 --version` |
| Node / npm | 18+ / 9+ | `node -v && npm -v` |
| Interactive Brokers account | Paper only | ibkr.com |

You do **not** need a live IB account. The bot runs entirely in mock
mode until you are ready.

---

## Step 1 — Install IB Gateway (Paper Trading)

1. Download **IB Gateway** from the IBKR website (not TWS; Gateway is
   lighter and has no GUI popups).
2. During setup choose **Paper Trading** when asked for account type.
3. After login → Configure → API → Settings:
   - Enable ActiveX and Socket Clients: **checked**
   - Socket port: **4002**
   - Read-Only API: **checked** (the bot enforces this in code too)
   - Trusted IPs: 127.0.0.1
4. Leave IB Gateway running in the background whenever the bot runs.

---

## Step 2 — Clone / copy the project

```bash
cp -r /path/to/trading-bot ~/trading-bot
cd ~/trading-bot
```

---

## Step 3 — Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
python3 -m pytest tests/ -q          # should show 311 passed
```

---

## Step 4 — Environment variables

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...        # from console.anthropic.com
TELEGRAM_BOT_TOKEN=...              # from @BotFather on Telegram
TELEGRAM_CHAT_ID=...                # your Telegram user/chat ID
DASHBOARD_SECRET=some-random-string # anything — used to sign sessions
```

Leave the IB lines as-is for paper mode.

**Never commit `.env` to git.** It is already in `.gitignore`.

---

## Step 5 — Database

```bash
python3 -c "from data.database import init_db; init_db('trading_bot.db')"
```

This creates `trading_bot.db` with all 20 tables. It is safe to run
again — all statements are `CREATE TABLE IF NOT EXISTS`.

---

## Step 6 — Configuration

Open `config/settings.yaml`:

```yaml
env: paper
mock_mode: true     # ← change to false once IB Gateway is running
phase: 6            # ← set to the current build phase
```

Open `config/risk_limits.yaml` to review the 17 hard-coded safety
rules. These are plain numbers — change them with care and only by
hand. The AI never touches this file.

---

## Step 7 — Start the API server

```bash
source .venv/bin/activate
uvicorn api.app:app --host 127.0.0.1 --port 8000
```

Test it:

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","mock":true,"phase":6}
```

---

## Step 8 — Start the React dashboard

```bash
cd ui/web
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

Pages:
- **Today** — pending signals waiting for your approval
- **Positions** — open paper positions
- **Performance** — equity curve chart
- **Journal** — log of every decision you made (approve/reject + your note)
- **Readiness** — checklist before advancing to the next phase
- **Learn** — plain-English explanations of every concept the bot uses

---

## Step 9 — First run (mock mode)

With `mock_mode: true` in settings, no IB connection is made. The bot
generates simulated quotes and signals so you can test the full
approval flow.

```bash
# In a separate terminal:
source .venv/bin/activate
python3 -c "
import asyncio
from monitor.monitor import run_one_scan
from config.settings import load_settings
from data.database import get_connection, init_db

init_db('trading_bot.db')
conn = get_connection('trading_bot.db')
asyncio.run(run_one_scan(load_settings(), conn, None, 'trading_bot.db'))
print('Scan complete — check the dashboard.')
"
```

You should see a signal card appear in the **Today** page.

---

## Step 10 — Connect to IB Gateway (when ready)

1. Start IB Gateway and log in with your paper account.
2. In `config/settings.yaml`, set `mock_mode: false`.
3. Restart the API server.
4. The bot will connect to `127.0.0.1:4002` automatically.

---

## Daily workflow

| Time | Action |
|------|--------|
| Before market open | Check **Readiness** page — all green? |
| During market hours | Review signal cards on **Today** page |
| Approve a trade | Click Approve → rate confidence (1-5) → add a note |
| Reject a trade | Click Reject |
| Positions to watch | **Positions** page; stops fire automatically |
| End of day | Check **Performance** → equity curve updated |
| Weekly | Review **Journal** → how are your approve/reject calls doing? |

---

## Kill switch

The red button in the top-right of the dashboard immediately:
1. Cancels all open orders
2. Blocks all new signal approvals
3. Logs the event to `system_events` in the database

Clear it from the dashboard or with:

```bash
curl -X POST http://127.0.0.1:8000/api/kill/clear \
     -H "X-Session-Token: <your-token>"
```

---

## Logs

```bash
tail -f logs/trading_bot.log
```

Log level is set in `config/settings.yaml` → `logging.level`.
Change to `DEBUG` when diagnosing a problem, then set back to `INFO`.

---

## Backup

Run daily — see `ops/backup.sh`:

```bash
bash ops/backup.sh
```

This copies the database and config to `backups/` with a timestamp.

---

## Advancing phases

The bot has 8 build phases. Each phase unlocks more functionality.
Never jump ahead — the Readiness page will tell you when all criteria
are met for the current phase.

To advance: edit `config/settings.yaml` → `phase: N+1`, then restart
the API server.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "IB connect failed" | IB Gateway not running or wrong port | Start Gateway, check port 4002 |
| No signals on Today page | mock_mode=false but market closed | Enable mock_mode or wait for market |
| Telegram not sending | Wrong BOT_TOKEN or CHAT_ID | Test with @BotFather |
| Dashboard shows "API offline" | uvicorn not running | Start API server |
| Tests fail | Missing dependency | `pip install -e ".[dev]"` in venv |
