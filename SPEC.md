# Trading Bot — Spec v0.3
> Source of truth for strategy, risk, architecture, and phases. Changes go through a PR with a dated changelog entry at the bottom.
> Values marked are waiting on Tyler's sign-off. Don't build against them as final.

## 1. Goal
An automated intraday options bot on Schwab that:
1. **Enforces discipline.** It can't revenge-trade, average down, oversize, or override its own stops.
2. **Measures honestly.** Bid/ask fills, every trial logged, holdout testing.
3. **Finds and keeps an edge only when the data proves one.** "No edge yet" is an acceptable answer.

## 2. Account and instrument constraints
| Item | Value |
|---|---|
| Broker | Schwab, Trader API (Individual) |
| Account | **Cash, dedicated to the bot** (no manual trading in it) |
| Allowed | Buy calls and buy puts (single leg) |
| Not allowed | Spreads, short options, margin |
| Cash rule | Spend **settled cash only**. Options settle T+1. Zero good-faith violations |
| Instrument candidates | **XSP** (1/10 SPX, cash-settled, European, Section 1256) and **SPY** (much tighter spreads, physically settled). The pick is made from measured costs at the Phase 1 gate (§9), not up front. Flat-by-15:45 removes SPY's exercise risk |
| Signal source | **SPY 1-min bars** for every hypothesis, whichever instrument is traded. Indexes carry no volume, so volume and VWAP inputs can't come from $SPX/$XSP. Join to the option chain with as-of semantics |
| Excluded in v1 | TSLA and single stocks |
| Expirations | 0DTE and 1DTE (both tested). **1DTE is also closed the same day** (see §5 flat-by rule); it's tested for lower gamma/theta, not overnight holds |

## 3. Strategy hypotheses (pre-registered)
Every hypothesis runs through the same entry/exit framework and the same costs. **No hypothesis is "the strategy" until it passes the Phase 1 gate.**

| ID | Signal (on SPY bars) | Expression |
|---|---|---|
| H1 | **Noise-area intraday momentum** (Zarattini/Aziz/Barbon): price breaks outside the volatility-scaled band around the open, checked on 30-min marks | Long call/put, delta band per §5 |
| H2 | **Tyler's baseline confluence**: 15m bias + 5m confirm + 1m trigger (TTM Squeeze, GoNoGo approximation, volume) | Same |
| H3 | H1 or H2 filtered by the event calendar (skip FOMC/CPI/NFP days or the hour around them) | Same |
| C0 | **Random-direction control**: same entry times, sizing, and exits, coin-flip direction. Run with many seeds and report the distribution, not one draw | Same |

Shared rules (all hypotheses):
- No entries in the first 15 minutes after the open or after 14:30 ET for 0DTE.
- Exits: the hypothesis's own exit (e.g., H1's noise-boundary/VWAP trail or signal flip), premium take-profit, premium stop, and a hard time exit. All are parameters in `strategy.yaml`. Strategy stops may be **tighter** than the §5 guardrail stop, never wider.
- One open position at a time.
- **Backtests run with every §5 guardrail switched on**, so backtest, paper, and live results are comparable.

A hypothesis "wins" only if it beats the **C0 distribution** (above its 95th percentile on net P&L) and stays net positive under pessimistic fills on the untouched holdout (§8).

## 4. Fills and costs (backtest and SimBroker)
- Buy at the **ask**, sell at the **bid**. Never use the midpoint.
- Commission is $0.65 per contract each way, plus regulatory/index fees (verify Schwab's XSP fees).
- Extra slippage of +1 tick near the open, near the close, and on fast moves (configurable).
- No fill if the quote is stale (older than N seconds) or the spread exceeds X% of the mid.
- Signals use **closed bars only**, timestamped at bar end, to prevent lookahead.
- Stop fills are modeled as a market sell at the **next bid** after the trigger, including gaps through the stop. Don't assume a fill at the stop price.

## 5. Guardrails (protected code, `risk/`)
They live in protected code, not `strategy.yaml`, and nothing can change them except a human PR.

**Design rule:** a 0DTE option can gap to zero through any stop, so **the full premium is the worst-case loss of every trade.** Every number below is sized so a single total loss can't trip the circuit breaker on its own.

| Guardrail | Value |
|---|---|
| Strike selection | Delta **0.40–0.55** only. If one contract in that band costs more than the premium cap, **no trade**. Never move further OTM to fit the budget |
| Max premium per trade | **20% of settled cash**; steps down to 10%, then 5% as equity passes $5k and $20k |
| Guardrail premium stop | −40% of premium paid. Strategy stops may be tighter |
| Broker-side stop | Right after every fill, a **resting stop order at Schwab** at the guardrail stop level. If Schwab rejects it, flatten immediately. This protects the position if the token, network, or container dies |
| Max trades per day | 1 (2 once equity is over $2k) |
| Daily halt | After 1 losing trade |
| Weekly halt | Equity down **10% week-to-date**. Replaces "3 losing trades": at a 40% win rate, three losses in five trades happens in about 68% of weeks |
| Circuit breaker | −30% from equity peak → bot disabled until a human review and PR |
| Averaging down / adding to losers | Never |
| Re-entry after a stop | Not the same day |
| Flat by | 15:45 ET, every position, every day (0DTE and 1DTE) |
| Settled-cash guard | Order rejected if cost > settled cash available (checked from Schwab's balances, not local state) |
| Fail closed | No new entries if the token is invalid or expiring within 1h, the quote feed is stale, or the account state can't be read. Existing positions stay protected by the broker-side stop |
| Claude outputs | `NONE`, `SIZE_DOWN`, `SIT_OUT` only. **No path to open or enlarge a trade** |
| Human controls | Kill switch (flatten and disable). **No manual-entry button** |

**Capital implication:** at a 20% cap, a contract costing $P needs about 5 × $P of settled cash. The recorder will measure real 0.40–0.55 delta premiums for XSP and SPY. Phase 3 is funded to whatever that implies, likely around $1–2k, rather than to an arbitrary amount.

## 6. Architecture
```
            ┌──────────── EventBridge schedules ────────────┐
            │                                               │
  08:30 ET  ▼ premarket-lambda (Claude)       Sat AM   ▼ review-lambda (Claude)
  regime flag → DynamoDB (locked at open)      weekly review → proposal → trial ledger
                                                           │
                                                           ▼
                                               backtest gate (Batch/Fargate job)

  Market hours (Fargate, always-on during session):
  Schwabdev stream + chain poller ──► recorder ──► S3 Parquet (chains, 1-min bars)
                 │
                 ▼
  signal engine (pure functions, closed SPY bars) ──► risk/ (protected) ──► Broker interface
                                                                        ├─ SimBroker  (bid/ask from live chains)
                                                                        └─ SchwabBroker (live; gated by env flag)
  All events → Datadog (fills vs theoretical, slippage, halts, token expiry T-24h alert)
```

Components:
- **Recorder** runs from day one, even before strategy work. Every minute during market hours it snapshots XSP and SPY chains (0–2 DTE, ±N strikes around the money), SPY 1-min bars, and $SPX/$XSP/$VIX/$VIX1D quotes to S3, respecting Schwab's API rate limits (~120 requests/min). It also logs **bid/ask spread and premium at 0.40–0.55 delta** per instrument, which feeds the XSP-vs-SPY decision and the capital sizing in §5.
- **Token service** keeps Schwabdev tokens in DynamoDB or Secrets Manager. It alerts 24h before refresh-token expiry and links to a re-auth page. The re-auth is a fixed weekly calendar slot (weekend), not left to the alert. An expired token triggers fail-closed (§5).
- **Engine** is one event loop. The same code runs in backtest, paper, and live, with only the broker swapped.
- **Strategy config** lives in `strategy.yaml`, git-versioned. It's the only thing the review loop can propose changes to.
- **Trial ledger** is append-only (DynamoDB plus an S3 export). It logs every proposal (including ones never tested), every backtest run with params and data window, and every holdout touch.
- **IaC** is AWS CDK (Python), deployed under a scoped IAM role in a dedicated account/sub-account with a budget alarm.

## 7. Claude's role (off the order path)
| Job | When | Output |
|---|---|---|
| Pre-market read | 08:30 ET | `{mode: NORMAL / SIZE_DOWN / SIT_OUT, lean, reasons}` from futures, overnight news, VIX/VIX1D, economic calendar. Bull/bear/risk structure borrowed from TradingAgents. Cached with prompt and model version, locked at the open. **Logged only until the flag is promoted (see Evaluation)** |
| Daily trade grading | 16:30 ET | Grades each trade against the rules (did the bot do what the spec says?). Flags bugs and data issues. **No parameter proposals** |
| Weekly review | Saturday | Looks at the week's trades plus the cumulative record and may propose **one** `strategy.yaml` parameter change, stated as a hypothesis before any test runs |
| Never | — | Places orders, edits `risk/`, edits broker code, changes guardrails |

Why weekly: at ≤1 trade a day, a daily proposal reacts mostly to noise, and every proposal adds to the trial count that Deflated Sharpe has to pay for.

Evaluation:
- **One paper book.** The rules-only book is the counterfactual; the flag is logged beside it, not acted on.
- Each flag is scored against the actual outcome: Brier score on `lean`, and P&L avoided/forgone on `SIZE_DOWN`/`SIT_OUT` days versus the rules-only result.
- The flag is promoted to acting on paper only if, after **≥60 trading days and ≥10 non-NORMAL days**, it shows net avoided losses. Otherwise it's cut.
- Never backtest flags on dates before the model's training cutoff.

## 8. Validation gate (how any change ships)
1. The change is proposed as a single hypothesis and logged in the trial ledger.
2. **Walk-forward backtest**:
   - Pessimistic fills (§4), all guardrails on (§3).
   - Must beat the incumbent and the C0 distribution.
   - Report Deflated Sharpe / PBO using the ledger's **total** trial count (proposals included).
3. **Holdout check:** the most recent N months, which the review loop never sees.
   - Only candidates that already passed step 2 may touch it. Every touch is logged.
   - Holdout budget: after **5 touches** the holdout is retired and replaced with newer recorder data the loop hasn't seen.
4. **Shadow paper:** at least 20 trading days next to the incumbent.
5. Human approves the PR. Deploy on the weekend only, never mid-week or intraday.

## 9. Phases
| Phase | Work | Exit criteria |
|---|---|---|
| **0. Foundations** | Repo + CI, CDK baseline, Schwabdev auth + token service, **recorder live**, ThetaData backfill (XSP/SPX/SPXW/SPY options, near-money, 1-min bid/ask; SPY bars; $SPX index) → S3, Databento spot-check | Recorder running daily; backfill matches spot-check on a sample of days |
| **1. Backtest** | Engine + SimBroker, indicators (TTM Squeeze validated vs thinkorswim), H1–H3 vs C0 on both XSP and SPY | A hypothesis beats C0, net positive on the holdout under pessimistic fills. **Instrument chosen** (XSP vs SPY) on measured net cost from backtest + ≥4 weeks of recorder spreads. If none passes, stop and rethink. Don't push ahead |
| **2. Paper** | Engine on live Schwab data + SimBroker; one book + logged flag; daily grading + weekly review on | ≥60 paper trades; net result inside the backtest's bootstrapped range (not below its 10th percentile); zero guardrail breaches; zero simulated good-faith violations. 60–100 trades can't prove an edge on their own. They confirm live behavior matches the backtest |
| **3. Tiny live** | Fund per §5 capital implication. `SchwabBroker` enabled, 1 contract, kill switch only | ≥40 live trades; live fills within tolerance of SimBroker on the same quotes; broker-side stop placed on 100% of fills; result still inside the backtest range |
| **4. Scale** | Add capital; per-trade risk steps down per §5 | Drawdown stays inside the circuit breaker; edge persists |

## 10. Repo layout (target)
```
trading-bot/
  CLAUDE.md
  SPEC.md
  strategy.yaml              # only file the review loop may propose changes to
  src/bot/
    data/        # recorder, ThetaData loader, parquet io
    signals/     # pure functions on closed bars (H1, H2, filters, C0)
    engine/      # event loop
    broker/      # base.py, sim.py, schwab.py  (schwab.py protected)
    risk/        # guardrails (protected)
    llm/         # premarket, daily grading, weekly review jobs, prompts, schemas
    ledger/      # trial ledger
  backtests/     # notebooks/scripts; results written to S3 + ledger
  infra/         # CDK app
  tests/
  .github/workflows/  # CI incl. protected-path check
```

## 11. Open questions
- Schwab: is the cash account approved for long options (which level), and is XSP tradable?
- Schwab XSP index-option fees on top of $0.65.
- Schwab API: are **stop orders accepted on XSP and SPY options** (needed for the broker-side stop)? If not, what's the fallback?
- ThetaData: does the options subscription cover XSP, and is the $SPX index feed a separate subscription?
- Which expiration (0DTE vs 1DTE) and which instrument (XSP vs SPY) survive Phase 1.
- Final guardrail values (§5, every item).
- Whether to buy Cboe DataShop history (2012+) if ThetaData's depth isn't enough. Daily SPX/XSP expirations only exist since 2022, so 0DTE history is about four years whatever the vendor.

## Changelog
- 2026-10-30 v0.1: Initial spec. Cash account, long options only, XSP primary.
- 2026-10-30 v0.2: Review pass.
  - XSP vs SPY decided from measured costs at Phase 1. Signals on SPY bars (indexes have no volume). 1DTE closed the same day.
  - Guardrails resized so one total loss can't trip the breaker: 20% premium cap, delta band with no-trade-if-unaffordable, −30% breaker. Weekly halt is now drawdown-based. Broker-side resting stop and fail-closed added.
  - C0 run as a distribution. Backtests run with guardrails on. Stop fills modeled at the next bid.
  - Claude: daily grading, weekly proposals. One paper book with the flag logged and promoted only on evidence.
  - Ledger counts proposals. Holdout has a touch budget.
  - Phase 2/3 gates check consistency with the backtest instead of claiming significance from 100 trades.
- 2026-10-30 v0.3: Tyler approved all v0.2 proposed values (guardrails §5, holdout budget §8). Locked.
