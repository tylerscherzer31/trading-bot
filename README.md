# trading-bot

Disciplined intraday options bot on Schwab. **`SPEC.md` is the source of truth;
`CLAUDE.md` is the rulebook for Claude Code.** Current phase: **0 — Foundations**.

## What's here (Phase 0 scaffold)

| Path | Status |
|---|---|
| `src/bot/risk/guardrails.py` | **Done.** All SPEC v0.3 guardrails, fail-closed, LLM-can-only-restrict. Protected |
| `src/bot/broker/sim.py` | **Done.** Bid/ask fills, fees, slippage windows, stale/wide rejects, gap-through stops, T+1 settlement |
| `src/bot/broker/schwab.py` | Stub. Refuses unless `LIVE_TRADING=true` and phase ≥ 3. Protected |
| `src/bot/engine/loop.py` | Entry path: signal → risk → fill → broker-side stop (flatten if rejected) |
| `src/bot/signals/` | C0 control done; H1 interface only (Phase 1) |
| `src/bot/ledger/` | Append-only, hash-chained trial ledger (local JSONL) |
| `src/bot/llm/schemas.py` | Closed schemas: flag modes, single-key proposals that can't touch guardrails |
| `src/bot/data/recorder.py` | Dry-run plan + S3 key layout; live loop waits on Schwab app approval |
| `infra/` | CDK app, `BudgetStack` only |
| `.github/workflows/ci.yml` | Lint + tests + protected-path check |

## Setup

```bash
# prerequisites: uv (https://docs.astral.sh/uv/), Node 20+, AWS CLI
uv sync                       # Python 3.12 + pinned deps from uv.lock
uv run pytest                 # 30 tests
uv run python -m bot.data.recorder --dry-run
cp .env.example .env          # fill in when the Schwab app is approved
```

## Deploy the budget alarm (first AWS step)

```bash
cd infra
uv sync --group infra
npx aws-cdk@2 bootstrap -c alert_email=YOU@example.com   # once per account/region
npx aws-cdk@2 diff      -c alert_email=YOU@example.com
npx aws-cdk@2 deploy BudgetStack -c alert_email=YOU@example.com
```

## GitHub settings (make the protected-path check mean something)

1. Branch protection on `main`: require PRs, require the `test` and `protected-paths` checks.
2. Create a label named `human-approved`. Only you apply it.
3. Don't give automation tokens permission to add labels.

## Phase 0 checklist
- [x] Repo, CI, protected-path check, branch protection, Claude Code settings
- [x] Token service core: expiry health, T-24h alert, Saturday re-auth slot, client factory (`src/bot/auth/`)
- [x] Recorder loop: chain normalization, delta-band spread/premium metrics, Parquet sink, session runner
- [x] CDK `DataStack`: private versioned bucket + tokens/state/ledger tables (synth only)
- [x] H1 noise-area signal (Phase 1 work, pulled forward; needs validation vs the paper)
- [ ] **Tyler:** deploy `BudgetStack` (needs AWS account + scoped role)
- [ ] **Waiting on Schwab:** create app → first login → token metadata into DynamoDB/Secrets Manager
- [ ] Recorder: SPY 1-min bars + index quotes, S3 sink, first live run; `RecorderStack` (Fargate, market hours)
- [ ] Deploy `DataStack` after reviewing `cdk diff` together
- [ ] ThetaData backfill once the recorder is running
