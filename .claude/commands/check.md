---
description: Run the full local verification (lint, tests, recorder dry run, CDK synth)
---

Run these in order and report a one-line result for each. Stop at the first failure and show the error.

1. `uv run ruff check .`
2. `uv run pytest`
3. `uv run python -m bot.data.recorder --dry-run`
4. `cd infra && uv sync --group infra && npx aws-cdk@2 synth -c alert_email=check@example.com --quiet`
