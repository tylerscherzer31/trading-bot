"""Recorder (SPEC §6): snapshots chains every minute during market hours.

Runs from day one, before any strategy work. Clock, sleeper, market-data
client and sink are injected, so the loop is tested without Schwab or AWS.

Usage:
    uv run python -m bot.data.recorder --dry-run          # plan + API budget only
    uv run python -m bot.data.recorder --out data_local   # live (needs Schwab app)
"""

from __future__ import annotations

import argparse
import logging
import os
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import exchange_calendars as xcals
import pandas as pd

from bot import NY
from bot.data.chains import delta_band_metrics, normalize_chain
from bot.data.recorder_keys import s3_key
from bot.data.sink import LocalParquetSink, Sink

__all__ = ["RecorderPlan", "Recorder", "main", "s3_key", "session_bounds"]

log = logging.getLogger("bot.recorder")
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


class MarketData(Protocol):
    def get_chain(
        self, symbol: str, from_date: str, to_date: str, strike_count: int
    ) -> dict[str, Any]: ...


class SchwabdevMarketData:
    """Adapter over schwabdev.Client. Method/param names per schwabdev docs; verify on first run."""

    def __init__(self, client: Any) -> None:
        self._c = client

    def get_chain(
        self, symbol: str, from_date: str, to_date: str, strike_count: int
    ) -> dict[str, Any]:
        resp = self._c.option_chains(
            symbol, strikeCount=strike_count * 2 + 1, fromDate=from_date, toDate=to_date
        )
        resp.raise_for_status()
        return resp.json()


def session_bounds(day: pd.Timestamp) -> tuple[datetime, datetime] | None:
    cal = xcals.get_calendar("XNYS")
    if not cal.is_session(day):
        return None
    return (
        cal.session_open(day).to_pydatetime().astimezone(NY),
        cal.session_close(day).to_pydatetime().astimezone(NY),  # half days handled
    )


@dataclass
class Recorder:
    md: MarketData
    sink: Sink
    plan: RecorderPlan = field(default_factory=RecorderPlan)
    errors: int = 0

    def run_once(self, now: datetime) -> list[str]:
        """One snapshot of every chain symbol. A failing symbol never stops the others."""
        written: list[str] = []
        today = now.astimezone(NY).date()
        to = today + timedelta(days=self.plan.max_dte + 4)  # cover weekends/holidays
        for sym in self.plan.chain_symbols:
            try:
                raw = self.md.get_chain(
                    sym, today.isoformat(), to.isoformat(), self.plan.strikes_each_side
                )
                chain = normalize_chain(raw, now, self.plan.strikes_each_side)
                chain = chain[chain["dte"] <= self.plan.max_dte]
                for kind, df in (("chains", chain), ("delta_band", delta_band_metrics(chain))):
                    key = self.sink.write(df, kind, sym, now)
                    if key:
                        written.append(key)
            except Exception:  # noqa: BLE001 - recorder must keep running
                self.errors += 1
                log.exception("snapshot failed for %s", sym)
        return written

    def run_session(
        self,
        day: pd.Timestamp,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = _time.sleep,
    ) -> int:
        """Snapshot every cadence from open to close. Returns snapshots taken."""
        bounds = session_bounds(day)
        if bounds is None:
            log.info("%s is not a session; nothing to record", day.date())
            return 0
        open_, close = bounds
        step = timedelta(seconds=self.plan.cadence_seconds)
        taken = 0
        nxt = open_
        while True:
            now = clock()
            if now >= close:
                break
            if now < nxt:
                sleep((nxt - now).total_seconds())
                continue
            self.run_once(now)
            taken += 1
            while nxt <= now:
                nxt += step  # skip missed slots instead of bursting to catch up
        return taken


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today NY)")
    ap.add_argument("--out", default="data_local", help="local root for parquet output")
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

    from bot.auth.schwab_client import credentials_from_env, make_client

    logging.basicConfig(level=logging.INFO)
    md = SchwabdevMarketData(make_client(credentials_from_env()))
    rec = Recorder(md, LocalParquetSink(Path(args.out)), plan)
    n = rec.run_session(day)
    print(f"Recorded {n} snapshots with {rec.errors} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
