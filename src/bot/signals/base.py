"""Signal interface. Signals are pure functions of closed SPY bars (CLAUDE.md conventions).

No I/O and no clock reads. Bars are timestamped at bar END; a signal at time T
may only use bars whose end timestamp is <= T.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd


class Direction(StrEnum):
    LONG_CALL = "LONG_CALL"
    LONG_PUT = "LONG_PUT"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Signal:
    direction: Direction
    ts: pd.Timestamp  # decision time (end of the last bar used)
    reason: str = ""


def assert_closed_bars(bars: pd.DataFrame, decision_ts: pd.Timestamp) -> None:
    """Guard against lookahead: every bar must have ended at or before decision_ts."""
    if len(bars) and bars.index.max() > decision_ts:
        raise ValueError("lookahead: bars extend past decision time")
