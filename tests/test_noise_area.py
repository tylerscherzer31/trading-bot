import math

import numpy as np
import pandas as pd
import pytest

from bot.signals.base import Direction
from bot.signals.noise_area import (
    is_check_mark,
    noise_area_signal,
    noise_bounds,
    session_vwap,
    trailing_stop,
)

TZ = "America/New_York"


def day_bars(date: str, open_: float, path) -> pd.DataFrame:
    """390 one-minute bars, timestamped at bar END (09:31..16:00). path(i) -> close."""
    idx = pd.date_range(f"{date} 09:31", periods=390, freq="1min", tz=TZ)
    closes = np.array([path(i) for i in range(390)], dtype=float)
    opens = np.concatenate([[open_], closes[:-1]])
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) + 0.01,
         "low": np.minimum(opens, closes) - 0.01, "close": closes, "volume": 1000.0},
        index=idx,
    )


def history(n_days=14, wiggle=0.002, start="2026-10-12"):
    """n_days of sessions where price wanders ±wiggle around a 600 open."""
    days = pd.bdate_range(start, periods=n_days)
    return pd.concat(
        [day_bars(str(d.date()), 600.0, lambda i: 600 * (1 + wiggle * math.sin(i / 20)))
         for d in days]
    )


def test_bounds_match_hand_calc_and_use_prev_close():
    hist = history()
    prev_close = hist["close"].iloc[-1]
    today = day_bars("2026-10-30", 603.0, lambda i: 603.0)
    bars = pd.concat([hist, today])
    ts = pd.Timestamp("2026-10-30 10:00", tz=TZ)
    b = noise_bounds(bars.loc[:ts], ts, lookback_days=14, band_multiplier=1.0)
    # sigma = mean |close@10:00 / open - 1| over 14 identical days
    expected_sigma = abs(600 * (1 + 0.002 * math.sin(29 / 20)) / 600 - 1)
    assert b.sigma == pytest.approx(expected_sigma)
    assert b.upper == pytest.approx(max(603.0, prev_close) * (1 + expected_sigma))
    assert b.lower == pytest.approx(min(603.0, prev_close) * (1 - expected_sigma))


def test_breakout_long_breakdown_short_inside_flat():
    hist = history()
    ts = pd.Timestamp("2026-10-30 10:30", tz=TZ)
    up = pd.concat([hist, day_bars("2026-10-30", 600, lambda i: 600 + 0.05 * i)])
    down = pd.concat([hist, day_bars("2026-10-30", 600, lambda i: 600 - 0.05 * i)])
    flat = pd.concat([hist, day_bars("2026-10-30", 600, lambda i: 600.0)])
    assert noise_area_signal(up.loc[:ts], ts, 14, 1.0).direction == Direction.LONG_CALL
    assert noise_area_signal(down.loc[:ts], ts, 14, 1.0).direction == Direction.LONG_PUT
    assert noise_area_signal(flat.loc[:ts], ts, 14, 1.0).direction == Direction.FLAT


def test_insufficient_history_is_flat():
    bars = pd.concat([history(n_days=5), day_bars("2026-10-30", 600, lambda i: 650)])
    ts = pd.Timestamp("2026-10-30 10:00", tz=TZ)
    assert noise_area_signal(bars.loc[:ts], ts, 14, 1.0).direction == Direction.FLAT


def test_refuses_future_bars():
    bars = pd.concat([history(), day_bars("2026-10-30", 600, lambda i: 600)])
    with pytest.raises(ValueError):
        noise_area_signal(bars, pd.Timestamp("2026-10-30 10:00", tz=TZ), 14, 1.0)


def test_vwap_and_trailing_stop():
    hist = history()
    ts = pd.Timestamp("2026-10-30 11:00", tz=TZ)
    up = pd.concat([hist, day_bars("2026-10-30", 600, lambda i: 600 + 0.05 * i)]).loc[:ts]
    vwap = session_vwap(up, ts)
    b = noise_bounds(up, ts, 14, 1.0)
    stop_long = trailing_stop(up, ts, Direction.LONG_CALL, 14, 1.0)
    assert stop_long == pytest.approx(max(b.upper, vwap))
    assert stop_long < up["close"].iloc[-1]  # trend still intact
    stop_short = trailing_stop(up, ts, Direction.LONG_PUT, 14, 1.0)
    assert stop_short == pytest.approx(min(b.lower, vwap))


def test_check_marks():
    assert is_check_mark(pd.Timestamp("2026-10-30 10:00", tz=TZ))
    assert is_check_mark(pd.Timestamp("2026-10-30 14:30", tz=TZ))
    assert not is_check_mark(pd.Timestamp("2026-10-30 10:15", tz=TZ))
