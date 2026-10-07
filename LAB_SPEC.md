# LAB_SPEC.md: strategy lab on Alpaca paper trading

The safety rules in `CLAUDE.md` take priority over anything in this file.

## 0. Goal and principles

Run several published, rules-based strategies side by side on an Alpaca paper account, each in its own slice, to find out which (if any) deserve real money. I would eventually invest about £100,000, but only in strategies that pass the tests below.

1. **History first, then paper trading.** The backtest asks whether a strategy works. Paper trading asks whether our live version behaves like its backtest.
2. **Same code everywhere.** The backtester and the live lab call the same strategy code.
3. **Rules are frozen and written down before any result is seen.** No tuning.
4. **Honest comparison.** Every strategy is compared, after costs, with buying and holding the S&P 500 (SPY).
5. **Monthly returns are shown but decide nothing.** One good month proves nothing. The decision rules are in sections 5 and 8.
6. **Plain English everywhere.** I'm a beginner and want to learn from this.

## 1. What changes from the IB version

- Stop the IB bot (`run_bot.py`) and switch off its trading path with a flag. Keep the IB code and the IB account: I am not deleting them yet, and IB's longer price history may be useful (section 4). Ask me before removing any IB code.
- Use Alpaca paper only, for US stocks and ETFs only, through a broker interface so the lab doesn't depend on Alpaca directly.
- Reuse what works: the database, risk module, API, dashboard, Telegram, logging and tests. Retire what no longer applies (LSE units, stamp duty, IB contract handling) by switching it off with flags, not by deleting it.
- `PHASE10_TASKS.md` is folded in. Its Tasks 1, 4 and 6 are part of Phase L0. Its Task 7 becomes Phase L4. Its IB-specific Tasks 2, 3 and 5 are replaced by Phase L1.

## 2. The strategies (frozen rules)

Each strategy runs in its own slice. "Month-end" means the last trading day of the month. Signals use prices adjusted for splits and dividends. Unless a strategy says otherwise, trades are placed in the first five minutes after the next market open.

### S1 Trend following across asset classes
- **Source:** Mebane Faber, "A Quantitative Approach to Tactical Asset Allocation" (2007).
- **Idea:** hold each asset class only while it is in an uptrend.
- **Funds:** SPY (US stocks), EFA (international stocks), IEF (US Treasury bonds), VNQ (US real estate), DBC (commodities) and GLD (gold). Faber used the first five; gold is added as a sixth by my choice and fixed now.
- **Rule:** at month-end, each fund whose price is above its 10-month simple moving average of month-end prices gets 1/6 of the slice. When a fund is below its average, its 1/6 is held in BIL (1-3 month Treasury bills) instead.

### S2 Dual momentum
- **Source:** Gary Antonacci, "Dual Momentum Investing" (2014), Global Equities Momentum.
- **Idea:** own the stronger of US and international stocks, but only while stocks beat Treasury bills. Otherwise own bonds.
- **Funds:** SPY, EFA (standing in for the ACWI ex-US index Antonacci used), BIL and AGG (US aggregate bonds).
- **Rule:** at month-end, compare 12-month total returns. If SPY beats BIL, hold whichever of SPY and EFA has the higher 12-month return. Otherwise hold AGG. The whole slice is always in one fund.

### S3 Sector momentum
- **Source:** Mebane Faber, "Relative Strength Strategies for Investing" (2010).
- **Idea:** hold the US sectors that have risen most over the past year.
- **Funds:** the nine original Select Sector SPDR funds, all trading since 1998: XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV and XLY.
- **Rule:** at month-end, rank the funds by 12-month total return and hold the top three, one third of the slice each.
- **Why sector funds, not single stocks:** a test on today's biggest companies flatters itself, because they are on the list precisely because they went up (survivorship bias). Sector funds don't have that problem.

### S4 Short-term mean reversion (RSI(2))
- **Source:** Larry Connors and Cesar Alvarez, "Short Term Trading Strategies That Work" (2008).
- **Idea:** in a rising market, buy sharp short-term dips and sell on the bounce.
- **Fund:** SPY.
- **Entry:** after each close, if SPY is above its 200-day simple moving average and its 2-day RSI is below 10, buy with the whole slice in the first five minutes after the next open. Use whole shares, with a limit at most 0.5% above the latest price.
- **Exit:** after a close above the 5-day simple moving average, cancel the protective stop and sell at the next open.
- **Deviations from the original,** for safety and practicality: trades happen at the next open instead of the close, and each entry carries a protective stop 3×ATR(14) below the entry price, held at Alpaca as an OTO order. Report how often the stop fired.

### S5 Breakout and dip-to-support (the existing bot, daily version)
- **Source:** this bot's own rules. They are not published, so treat them as unproven.
- **Universe:** the US stocks in the current watchlist (about 30).
- **Rule:** use the existing detectors and `build_signal` on daily closes: the entry trigger as now, a stop at 2×ATR(14) and a target at 2R. Size each trade with the original per-trade rules applied to the slice (safety rule 5).
- **Entry:** a bracket order in the first five minutes after the open, in whole shares, with a limit at most 0.5% above the latest price, time-in-force DAY. Skip the trade if the price has moved more than 1×ATR(14) from the signal close.
- **Uptrend check:** tell me whether dip-to-support requires an uptrend, and run it as it is either way.
- **Survivorship bias:** this applies here (today's large companies). Label the results accordingly.

### S6 Covered calls (buy-write)
- **Source:** the method of the Cboe S&P 500 BuyWrite Index (BXM): own the index and sell a one-month at-the-money call against it every month.
- **Idea:** collect option premium in exchange for giving up gains above the strike.
- **Mechanics:** hold 100 shares per contract and sell one call per 100 shares. On the monthly expiry day (the third Friday), at about 11:00 New York time, buy back the expiring call if it still has value, then sell the next month's call with the strike nearest the current price. Use limit orders at the mid-price, moving towards the market price up to 3 times, 10 minutes apart. Never leave a call uncovered.
- **Underlying:** SPY, if 100 shares fit inside the slice. If they don't, stop and ask me to choose between a bigger slice for this strategy, the most liquid cheaper index fund with active options (show me candidates with their option spreads and open interest), or skipping covered calls for now.
- **Expiry and assignment:** check how Alpaca paper handles these, and report.

### S7 Buy and hold (benchmark)
- SPY, bought on day one and held. Every other strategy must beat it to pass.

For context only (not a candidate, and no paper slice), the backtest report also shows a 60/40 mix of SPY and AGG rebalanced monthly.

## 3. Slices and execution

- **Slices:** equal slices of the paper account for the strategies I choose after the backtest (section 5), plus the benchmark. Keep the default paper balance of $100,000, which is close to the amount I'd really use.
- **Attribution:** every order carries its slice in `client_order_id`. The database keeps each slice's cash, positions, fees and dividends. The slice positions must add up to Alpaca's positions (safety rule 10). A slice can never sell more than it holds, even if the account holds more of that symbol.
- **Allocation strategies (S1, S2, S3, S7):** fractional (notional) market orders, time-in-force DAY, placed in the first five minutes after the open. Sells go before buys.
- **Swing strategies (S4, S5):** whole shares, using OTO orders (S4) or bracket orders (S5), with the stop held at Alpaca.
- **Covered calls (S6):** as described in S6.
- **Preview before each monthly rebalance:** the evening before, send me the planned trades on Telegram and the dashboard, with a one-line reason for each, for example "SPY is above its 10-month average, so the trend slice keeps it". The trades go ahead unless I press Pause.
- **Dividends:** Alpaca paper does not credit dividends. Record dividends and fund distributions from Alpaca's corporate-actions data in a dividends ledger, and add them to each slice's reported return. Show returns both with and without them.
- **Paper realism:** Alpaca paper fills against the best market price, but it does not simulate market impact, slippage from delays or regulatory fees. Apply the backtest's cost model to reported paper results, and show "as filled" and "after costs" side by side.
- **Calendar:** use the NYSE calendar, including holidays and half days. UK and US clocks change on different dates (25 October and 1 November 2026), so test the week in between.

## 4. Data

- **History:** use Alpaca daily bars, adjusted for splits and dividends for signals and backtests, and unadjusted for order prices. Alpaca has no data before 2016, so report each symbol's first available date.
- **Longer history:** the monthly strategies need about a year of history before their first signal, so with Alpaca data alone the test starts around 2017. Ask me whether to add longer history from IB (I have kept that account) so the test includes 2008. If I say yes, use it for the ETFs only, check it against Alpaca where the two overlap, and record the source of every bar.
- **Feeds:** the free plan gives real-time prices from IEX only. Use the SIP feed for history and IEX for real time, and record the feed on every bar and quote. Alpaca's docs say the free plan only restricts the latest 15 minutes of history; confirm this works with my keys.
- **Limits:** stream real-time quotes only for held symbols, because the free plan allows 30 streamed symbols. Use REST latest-quote calls for order checks, and stay under 200 requests a minute.
- **Quality checks:** look for missing days, zero or negative prices, one-day moves over 20% without a corporate action, and stale timestamps. Any of these blocks trading in that symbol until resolved, and is reported to me.
- **Options data (S6 only):** option chain snapshots (bid, ask, open interest) for the chosen underlying.

## 5. Backtest and pass rules (decided now, before any results)

- **Same code:** run the S1 to S7 strategy code through the backtester. Do not re-implement it separately.
- **Fills:**
  - Allocation and RSI(2) trades fill at the next open plus slippage.
  - Bracket trades enter at the open if within the 1×ATR limit, otherwise they are skipped.
  - If the stop and target are both inside one day's range, assume the stop hit first.
  - A gap through the stop fills at the open.
- **Costs:** $0 commission. Slippage of 0.05% per side for ETFs and 0.10% per side for single stocks. SEC and FINRA fees on sales, from their current schedules (cite them). For options (S6): Alpaca's current per-contract fees plus half the bid-ask spread.
- **S6 history:** Alpaca's options history is short, so use the published BXM index values as a proxy for the backtest and label the result "proxy". If BXM data can't be obtained, ask me.
- **Report,** for each strategy and for an equal mix of all passing strategies:
  - returns and risk: CAGR, annual volatility, Sharpe ratio (with BIL as the risk-free rate), Sortino ratio, maximum drawdown and how long it lasted, worst month and worst 12 months
  - behaviour: share of positive months, turnover, cost drag, time invested, beta to SPY and the correlation between slices
  - detail: a monthly return table, and trade statistics in R for S4 and S5

  Compare everything with SPY, the 60/40 mix and cash.
- **Statistics:** block bootstrap (by month) confidence intervals for the difference in CAGR and in Sharpe ratio against SPY, with a Holm correction across the six candidates. Say plainly how large a difference roughly nine years of data can and can't detect.
- **Robustness (reported, never used to choose):** move the start date 12 months either way, compare the first and second halves, and move each parameter 20% either way. If a strategy only works at its exact settings, say so.
- **Pass rule:** a candidate passes if, after costs, over the full test period, either:
  - (a) its Sharpe ratio is at least SPY's and its maximum drawdown is no worse than SPY's, or
  - (b) its maximum drawdown is at most two-thirds of SPY's and its CAGR is no more than 2 percentage points a year below SPY's.

  Flag any strategy that passes only because of one half of the period. These thresholds are fixed now, and changing them after seeing results makes the test invalid.
- **Verdict:** pass or fail for each strategy, in plain English, with the main reason. Passing means "not worse than the index, with less risk". It is not proof of a lasting edge.
- **Choice:** after the report, I choose which strategies get a paper slice. The default recommendation is the passing ones only.

## 6. Paper lab operations

- **Scheduler:**
  - monthly strategies after the month-end close
  - daily strategies after every close
  - covered-call rolls on expiry Fridays
  - reconciliation every 5 minutes during market hours
  - a heartbeat, with an alert if the process stops
- **Tracking:** each month, re-run the backtest over the paper period and compare it with each slice's actual paper result. Explain the differences (fills, timing, costs, dividends). Treat a gap of more than 1% in a quarter that can't be explained as a bug to investigate.
- **Breakers and kill switch:** as in `CLAUDE.md` rules 8 and 9.
- **Dry run first:** run a full week in mock mode on recorded data before the first paper trade.

## 7. Dashboard and Telegram

- **Header:** a PAPER · ALPACA badge that can't be hidden, connection status, the data feed (IEX real time), market open or closed in UK time, the kill switch and the hard stop.
- **Lab page (new, and the main page):**
  - one card per slice: strategy name, a one-line plain-English idea, the source, status (backtest pass or fail, running, paused), value, return since start, drawdown against its backtest's worst, positions and the next action date
  - one chart of all slices against SPY
  - a monthly table with the note "single months decide nothing"
  - reconciliation and breaker status
- **Strategy pages:** the rules, the backtest results, the paper results against the backtest over the same dates, and a plain-English explanation of the strategy.
- **Telegram:** a daily summary, monthly rebalance previews with a Pause button, fills, breakers, reconciliation problems and a weekly summary.

## 8. Decision rules for real money (documentation only, no code in this version)

Write these into the README:

- **Eligibility:** a strategy is eligible only if it passed the backtest rule and has run on paper for at least 3 months (6 preferred), with paper results tracking its own backtest, no safety breaches, and drawdowns within its tested range.
- **Starting size:** start with at most 25% of the intended amount, spread across the eligible strategies, never all in one. Increase only after 6 months of live results that track paper.
- **Before using Alpaca for real money:** it holds dollars only (so there are conversion costs), it is covered by US SIPC protection rather than the UK's FSCS, and it can't be an ISA, so gains are taxed normally. Compare this with a global index fund in an ISA.
- **Going live** needs a separate, deliberate change to both the code and `CLAUDE.md`.

## 9. Phases (stop after each one)

- **L0 Safety and plumbing:** stop the IB bot and switch it off with a flag, move to Python 3.11 only, use one database per mode with a startup guard, allow no mock data outside mock mode, and replace the dashboard token with a random one plus Origin and Host checks. Write tests for each.
- **L1 Alpaca paper connection:**
  - the broker interface and an Alpaca paper adapter
  - the paper-account checks, and setting and verifying the account configuration (safety rule 2)
  - the trade-update stream into the database, and reconciliation
  - the kill switch, hard stop and Close everything
  - tests with a fake broker
  - finish with one supervised test, only when I say go: buy 1 share of SPY with a stop, confirm it shows correctly in the database and dashboard, then close it
- **L2 Data:** history with a coverage report, feeds, quality checks, corporate actions and option chain snapshots. Ask me about IB history before 2016.
- **L3 Strategies:** a shared strategy interface and S1 to S7 exactly as written. Each gets unit tests on small hand-made price series that check every rule (for example, a fund just above versus just below its 10-month average).
- **L4 Backtest:** the full report from section 5, with verdicts. Show it to me, and I choose the paper slices.
- **L5 Paper lab:** slices and attribution, the scheduler, previews, the dividends ledger, cost reporting, breakers, the Lab page and Telegram. Do a dry-run week in mock mode, then start paper trading.
- **L6 Monthly review:** the tracking report, the comparison of each slice with SPY, and the README section on decision rules.
