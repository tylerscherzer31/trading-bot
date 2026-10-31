"""Monthly cost budget with email alerts at 50% / 80% actual and 100% forecast."""

from aws_cdk import Stack
from aws_cdk import aws_budgets as budgets
from constructs import Construct


class BudgetStack(Stack):
    def __init__(
        self, scope: Construct, cid: str, *, alert_email: str, monthly_limit_usd: float, **kw
    ) -> None:
        super().__init__(scope, cid, **kw)

        def alert(threshold: float, kind: str):
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    comparison_operator="GREATER_THAN",
                    notification_type=kind,
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[
                    budgets.CfnBudget.SubscriberProperty(
                        address=alert_email, subscription_type="EMAIL"
                    )
                ],
            )

        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="trading-bot-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=monthly_limit_usd, unit="USD"
                ),
            ),
            notifications_with_subscribers=[
                alert(50, "ACTUAL"),
                alert(80, "ACTUAL"),
                alert(100, "FORECASTED"),
            ],
        )
