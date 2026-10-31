"""H1: noise-area intraday momentum (Zarattini, Aziz & Barbon, 2024), on SPY 1-min bars.

For day t and time-of-day τ:
    σ(t, τ) = mean over the previous `lookback_days` sessions d of |Close(d, τ) / Open(d) − 1|
    Upper(t, τ) = max(Open(t), Close(t−1)) × (1 + m·σ)
    Lower(t, τ) = min(Open(t), Close(t−1)) × (1 − m·σ)

Decisions only on check marks (every 30 min). Price above Upper → long (calls),
below Lower → short (puts), otherwise flat. While in a trade the trailing stop is
max(Upper, VWAP) for longs and min(Lower, VWAP) for shorts.

Bars: DataFrame indexed by tz-aware bar END timestamps, columns open/high/low/close/volume.
Pure functions: no I/O, no clock reads; only bars ending at or before decision_ts are used.

Status: implemented from the paper's description; Phase 1 must reproduce the paper's SPY
results before this signal is run on options (SPEC §9).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bot import NY
from bot.signals.base import Direction, Signal, assert_closed_bars


@dataclass(frozen=True)
class NoiseBounds:
    upper: float
    lower: float
    sigma: float
    day_open: float
    prev_close: float


def _sessions(bars: pd.DataFrame) -> dict:
    local = bars.index.tz_convert(NY)
    return {d: bars[local.date == d] for d in sorted(set(local.date))}


def _tod(ts: pd.Timestamp) -> tuple[int, int]:
    t = ts.tz_convert(NY)
    return t.hour, t.minute


def noise_bounds(
    bars: pd.DataFrame, decision_ts: pd.Timestamp, lookback_days: int, band_multiplier: float
) -> NoiseBounds | None:
    """Bounds at decision_ts, or None if history is insufficient."""
    assert_closed_bars(bars, decision_ts)
    days = _sessions(bars)
    today = decision_ts.tz_convert(NY).date()
    if today not in days:
        return None
    prior = [d for d in days if d < today]
    if len(prior) < lookback_days:
        return None
    hist = prior[-lookback_days:]

    tod = _tod(decision_ts)
    moves = []
    for d in hist:
        day = days[d]
        at = day[[_tod(ts) == tod for ts in day.index]]
        if at.empty:
            return None  # e.g. τ after a half-day close: don't guess
        moves.append(abs(at["close"].iloc[-1] / day["open"].iloc[0] - 1))
    sigma = sum(moves) / len(moves)

    today_bars = days[today]
    day_open = float(today_bars["open"].iloc[0])
    prev_close = float(days[prior[-1]]["close"].iloc[-1])
    width = band_multiplier * sigma
    return NoiseBounds(
        upper=max(day_open, prev_close) * (1 + width),
        lower=min(day_open, prev_close) * (1 - width),
        sigma=sigma,
        day_open=day_open,
        prev_close=prev_close,
    )


def session_vwap(bars: pd.DataFrame, decision_ts: pd.Timestamp) -> float:
    assert_closed_bars(bars, decision_ts)
    local = bars.index.tz_convert(NY)
    today = bars[local.date == decision_ts.tz_convert(NY).date()]
    typical = (today["high"] + today["low"] + today["close"]) / 3
    vol = today["volume"].sum()
    return float((typical * today["volume"]).sum() / vol) if vol > 0 else float("nan")


def noise_area_signal(
    bars: pd.DataFrame, decision_ts: pd.Timestamp, lookback_days: int, band_multiplier: float
) -> Signal:
    b = noise_bounds(bars, decision_ts, lookback_days, band_multiplier)
    if b is None:
        return Signal(Direction.FLAT, decision_ts, "H1 insufficient history")
    price = float(bars.loc[:decision_ts, "close"].iloc[-1])
    if price > b.upper:
        return Signal(Direction.LONG_CALL, decision_ts, f"H1 {price:.2f} > upper {b.upper:.2f}")
    if price < b.lower:
        return Signal(Direction.LONG_PUT, decision_ts, f"H1 {price:.2f} < lower {b.lower:.2f}")
    return Signal(Direction.FLAT, decision_ts, "H1 inside noise area")


def trailing_stop(
    bars: pd.DataFrame,
    decision_ts: pd.Timestamp,
    direction: Direction,
    lookback_days: int,
    band_multiplier: float,
) -> float | None:
    """Underlying-price stop for an open H1 position (exit when crossed at a check mark)."""
    b = noise_bounds(bars, decision_ts, lookback_days, band_multiplier)
    if b is None or direction == Direction.FLAT:
        return None
    vwap = session_vwap(bars, decision_ts)
    if direction == Direction.LONG_CALL:
        return max(b.upper, vwap)
    return min(b.lower, vwap)


def is_check_mark(ts: pd.Timestamp, every_minutes: int = 30) -> bool:
    t = ts.tz_convert(NY)
    return t.second == 0 and (t.hour * 60 + t.minute) % every_minutes == 0
