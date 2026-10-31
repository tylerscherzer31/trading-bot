"""C0: random-direction control (SPEC §3).

Same entry times, sizing and exits as the hypothesis under test; only the
direction is a coin flip. Deterministic per (seed, decision time) so runs
reproduce, and run across many seeds to get a distribution.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from bot.signals.base import Direction, Signal, assert_closed_bars


def c0_signal(bars: pd.DataFrame, decision_ts: pd.Timestamp, seed: int) -> Signal:
    assert_closed_bars(bars, decision_ts)
    digest = hashlib.sha256(f"{seed}:{decision_ts.isoformat()}".encode()).digest()
    direction = Direction.LONG_CALL if digest[0] % 2 == 0 else Direction.LONG_PUT
    return Signal(direction, decision_ts, reason=f"C0 seed={seed}")
