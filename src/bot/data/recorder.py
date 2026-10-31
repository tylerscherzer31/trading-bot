"""Recorder (SPEC §6): snapshots chains + bars every minute during market hours.

Runs from day one, before any strategy work. `--dry-run` prints the plan and
API budget without touching the network; the live path needs the Schwab app
key (pending developer approval).

Usage:
    uv run python -m bot.data.recorder --dry-run
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

import exchange_calendars as xcals
import pandas as pd

from bot import NY

SCHWAB_RATE_LIMIT_PER_MIN = 120


@dataclass(frozen=True)
class RecorderPlan:
    chain_symbols: tuple[str, ...] = ("$XSP", "SPY")
    max_dte: int = 2
    strikes_each_side: int = 15
    quote_symbols: tuple[str, ...] = ("$SPX", "$XSP", "$VIX", "$VIX1D", "SPY")
    bar_symbols: tuple[str, ...] = ("SPY",)
    cadence_seconds: int = 60

    def requests_per_minute(self) -> int:
        # one chain call per symbol (date-ranged), one batched quote call, one bars call per symbol
        return len(self.chain_symbols) + 1 + len(self.bar_symbols)


def session_bounds(day: pd.Timestamp) -> tuple[datetime, datetime] | None:
    cal = xcals.get_calendar("XNYS")
    if not cal.is_session(day):
        return None
    return (
        cal.session_open(day).to_pydatetime().astimezone(NY),
        cal.session_close(day).to_pydatetime().astimezone(NY),
    )


def s3_key(kind: str, symbol: str, ts: datetime) -> str:
    """Parquet partitioned by symbol/date (CLAUDE.md conventions)."""
    d = ts.astimezone(NY).date().isoformat()
    clean = symbol.lstrip("$")
    return str(PurePosixPath(kind, f"symbol={clean}", f"date={d}", f"{ts:%H%M%S}.parquet"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today NY)")
    args = ap.parse_args(argv)

    plan = RecorderPlan()
    day = pd.Timestamp(args.date) if args.date else pd.Timestamp(datetime.now(NY).date())
    bounds = session_bounds(day)
    rpm = plan.requests_per_minute()

    print(f"Recorder plan for {day.date()}")
    if bounds is None:
        print("  Not a trading session (weekend/holiday). Nothing to record.")
    else:
        o, c = bounds
        minutes = int((c - o).total_seconds() // 60)
        print(f"  Session {o:%H:%M}–{c:%H:%M} ET ({minutes} snapshots)")
    print(f"  Chains: {', '.join(plan.chain_symbols)} | 0–{plan.max_dte} DTE | "
          f"±{plan.strikes_each_side} strikes")
    print(f"  Quotes: {', '.join(plan.quote_symbols)} | Bars: {', '.join(plan.bar_symbols)} 1-min")
    print(f"  API budget: {rpm} req/min of {SCHWAB_RATE_LIMIT_PER_MIN} "
          f"({rpm / SCHWAB_RATE_LIMIT_PER_MIN:.0%})")
    print(f"  Example key: {s3_key('chains', '$XSP', datetime.now(NY))}")

    if args.dry_run:
        return 0
    if not os.getenv("SCHWAB_APP_KEY"):
        print("SCHWAB_APP_KEY not set: live recording needs the approved Schwab app.")
        return 2
    raise NotImplementedError("Live recording loop is the next Phase 0 task")


if __name__ == "__main__":
    raise SystemExit(main())
