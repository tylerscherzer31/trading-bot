#!/usr/bin/env python3
"""CDK app. Budget alarm deploys first (CLAUDE.md: it must exist before other stacks).

    cd infra
    cdk synth -c alert_email=you@example.com
    cdk diff  -c alert_email=you@example.com
    cdk deploy BudgetStack -c alert_email=you@example.com
"""

import aws_cdk as cdk
from stacks.budget import BudgetStack

app = cdk.App()

email = app.node.try_get_context("alert_email")
if not email:
    raise SystemExit("Pass -c alert_email=you@example.com")
limit = float(app.node.try_get_context("monthly_budget_usd") or 50)

BudgetStack(app, "BudgetStack", alert_email=email, monthly_limit_usd=limit)

# Next stacks (added one at a time, each after `cdk diff` review):
#   DataStack     - S3 bucket for parquet, DynamoDB tables (tokens, state, ledger)
#   RecorderStack - Fargate service on a market-hours schedule
#   JobsStack     - EventBridge + Lambda for premarket / daily grading / weekly review

app.synth()
