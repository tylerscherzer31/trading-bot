"""Stateful resources: Parquet bucket + DynamoDB tables (SPEC §6).

Everything here is RETAIN: deleting the stack never deletes recorded market
data, token metadata, bot state, or the trial ledger. Show `cdk diff` to Tyler
before deploying (CLAUDE.md: stateful resources).
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw) -> None:
        super().__init__(scope, cid, **kw)

        self.bucket = s3.Bucket(
            self,
            "MarketData",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                # Old snapshots are read rarely (backtests); keep them, pay less.
                s3.LifecycleRule(
                    id="chains-to-ia",
                    prefix="chains/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    id="expire-old-versions",
                    noncurrent_version_expiration=Duration.days(30),
                ),
            ],
        )

        def table(name: str, sort_key: str | None = None) -> ddb.Table:
            return ddb.Table(
                self,
                name,
                partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                sort_key=(
                    ddb.Attribute(name=sort_key, type=ddb.AttributeType.NUMBER)
                    if sort_key
                    else None
                ),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True
                ),
                encryption=ddb.TableEncryption.AWS_MANAGED,
                removal_policy=RemovalPolicy.RETAIN,
            )

        # Token *metadata* (issue/expiry times). Token strings go to Secrets Manager.
        self.tokens = table("Tokens")
        # Bot state: halts, peak equity, latched circuit breaker, daily counters.
        self.state = table("State")
        # Append-only trial ledger: pk = stream name, seq = monotonic sequence number.
        self.ledger = table("Ledger", sort_key="seq")

        for name, value in (
            ("BucketName", self.bucket.bucket_name),
            ("TokensTable", self.tokens.table_name),
            ("StateTable", self.state.table_name),
            ("LedgerTable", self.ledger.table_name),
        ):
            CfnOutput(self, name, value=value)
