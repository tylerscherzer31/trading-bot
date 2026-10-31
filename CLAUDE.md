# CLAUDE.md — Trading Bot

Automated intraday options bot on Schwab. `SPEC.md` is the source of truth. Read the relevant section before changing behavior. Keep this file short; put details in SPEC.md.

**Current phase: 0 — Foundations** (update this line when a phase gate is passed)

## Non-negotiable rules
1. **Never place a live order** unless `LIVE_TRADING=true` and the current phase is 3 or later. Default broker is `SimBroker`.
2. **Protected paths:** `src/bot/risk/`, `src/bot/broker/schwab.py`, `.github/workflows/`. Don't edit them unless Tyler explicitly asks in this session. CI fails any PR that changes them without the `human-approved` label.
3. **Fills:** buy at the ask, sell at the bid, plus fees and slippage per SPEC §4. Never fill at the midpoint, anywhere.
4. **No lookahead:** signals use closed bars only, timestamped at bar end. Joins between options and the underlying use as-of semantics. Add a test whenever you touch data alignment.
5. **Cash account:** long calls/puts only, settled cash only. Any order path must go through the settled-cash guard.
5a. **Every live fill gets a resting broker-side stop** (SPEC §5). If it's rejected, flatten. The bot **fails closed**: no new entries on a bad token, a stale feed, or unreadable account state.
5b. **Signals are computed on SPY 1-min bars**, whichever option instrument is traded. Indexes have no volume.
5c. **Backtests run with all §5 guardrails on**, and stop fills are modeled at the next bid, gaps included.
6. **Claude/LLM code never sits on the order path.** LLM outputs are limited to `NONE | SIZE_DOWN | SIT_OUT` and proposals to `strategy.yaml`.
7. **Every backtest run, every proposal, and every holdout touch is logged in the trial ledger.** No silent reruns or cherry-picking.
8. **Secrets** (Schwab app key/secret, tokens, ThetaData, Anthropic API key) live in AWS Secrets Manager or `.env` (gitignored). Never commit them; never print them in logs.

## Stack
- Python 3.12, managed with `uv`; pin every dependency (LumiBot-style churn is common in this space).
- Schwabdev (broker/data), TA-Lib + pandas-ta-classic (**not** `pandas-ta`), py_vollib, vectorbt, pandas/polars, DuckDB, pyarrow.
- AWS CDK (Python) in `infra/`. Fargate for the market-hours engine and recorder, Lambda + EventBridge for scheduled jobs, DynamoDB for state/tokens/ledger, S3 Parquet for data.
- Datadog for metrics and alerts.

## Conventions
- Signals are pure functions: `(bars) -> Signal`. No I/O and no clock reads inside them.
- One `Broker` interface; `SimBroker` and `SchwabBroker` must be swappable without engine changes.
- Times are in `America/New_York` at the edges and UTC internally. Use the exchange calendar (`exchange_calendars` or `pandas_market_calendars`) for sessions, half days, and holidays.
- Tests: `pytest`. New logic needs tests; risk and fill logic needs property or edge-case tests (stale quotes, wide spreads, half days, expiry day).
- Parquet partitioned by `symbol/date`. Keep only near-the-money strikes to control size.

## AWS
- Deploy only with the scoped role in the project account. Never use root or admin credentials.
- `cdk diff` before every `cdk deploy`; show Tyler the diff for anything that creates or deletes stateful resources (S3, DynamoDB).
- Budget alarm must exist before other stacks are deployed.

## Commands (fill in as they're created)
- `uv run pytest`
- `uv run python -m bot.data.recorder --dry-run`
- `cd infra && cdk diff`

## When unsure
Ask. Never guess at trading rules, guardrail numbers, or anything that touches money. Guardrail values in SPEC §5 are locked (v0.3); changing them requires a human PR.
