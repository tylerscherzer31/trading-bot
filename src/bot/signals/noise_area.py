"""H1: noise-area intraday momentum (Zarattini/Aziz/Barbon). Built in Phase 1.

Interface fixed now so the engine and tests can be written against it.
Implementation must use closed SPY 1-min bars only and be validated against
the paper's published SPY results before it's run on options.
"""

from __future__ import annotations

import pandas as pd

from bot.signals.base import Signal


def noise_area_signal(
    bars: pd.DataFrame, decision_ts: pd.Timestamp, lookback_days: int, band_multiplier: float
) -> Signal:
    raise NotImplementedError("H1 is implemented in Phase 1 (SPEC §9)")
