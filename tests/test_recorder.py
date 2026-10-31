import math
from datetime import datetime, timedelta

import pandas as pd

from bot import NY
from bot.data.chains import delta_band_metrics, normalize_chain
from bot.data.recorder import Recorder, RecorderPlan
from bot.data.sink import LocalParquetSink

SNAP = datetime(2026, 11, 3, 11, 0, tzinfo=NY)
QUOTE_MS = int(SNAP.timestamp() * 1000)


def contract(sym, strike, right, delta, bid, ask):
    return {
        "putCall": "CALL" if right == "C" else "PUT", "symbol": sym, "bid": bid, "ask": ask,
        "mark": (bid + ask) / 2, "last": bid, "bidSize": 10, "askSize": 12, "totalVolume": 500,
        "openInterest": 1000, "volatility": 14.2, "delta": delta, "gamma": 0.05,
        "theta": -0.4, "vega": 0.02, "strikePrice": strike, "quoteTimeInLong": QUOTE_MS,
    }


def fake_chain(symbol="$XSP", spot=680.12):
    def side(right):
        out = {}
        for exp in ("2026-11-03:0", "2026-11-04:1", "2026-11-10:7"):
            strikes = {}
            for k in range(660, 701):
                moneyness = (spot - k) if right == "C" else (k - spot)
                d = max(0.02, min(0.98, 0.5 + moneyness / 20))
                d = d if right == "C" else -d
                prem = max(0.05, 1.5 + moneyness * 0.5)
                strikes[f"{k:.1f}"] = [contract(f"XSP {k}{right}", k, right, d, prem, prem + 0.04)]
            out[exp] = strikes
        return out

    calls, puts = side("C"), side("P")
    calls["2026-11-03:0"]["700.0"][0]["delta"] = -999.0  # Schwab "unavailable"
    puts["2026-11-03:0"]["660.0"][0]["delta"] = "NaN"
    return {"symbol": symbol, "underlyingPrice": spot, "callExpDateMap": calls,
            "putExpDateMap": puts}


def test_normalize_flattens_and_cleans_sentinels():
    df = normalize_chain(fake_chain(), SNAP)
    assert len(df) == 41 * 3 * 2
    assert set(df["right"]) == {"C", "P"} and df["underlying"].iloc[0] == "XSP"
    bad_call = df[(df.expiry == "2026-11-03") & (df.right == "C") & (df.strike == 700)]
    bad_put = df[(df.expiry == "2026-11-03") & (df.right == "P") & (df.strike == 660)]
    assert math.isnan(bad_call["delta"].iloc[0]) and math.isnan(bad_put["delta"].iloc[0])
    assert df["quote_ts"].iloc[0] == pd.Timestamp(SNAP).tz_convert("UTC")
    assert str(df["snapshot_ts"].dt.tz) == "UTC"


def test_strike_window_around_spot():
    df = normalize_chain(fake_chain(), SNAP, strikes_each_side=5)
    one = df[(df.expiry == "2026-11-03") & (df.right == "C")]
    assert sorted(one["strike"]) == [float(k) for k in range(675, 686)]  # ATM 680 ±5


def test_delta_band_metrics_only_uses_guardrail_band():
    m = delta_band_metrics(normalize_chain(fake_chain(), SNAP))
    assert not m.empty
    assert (m["median_spread"].round(4) == 0.04).all()
    assert (m["contract_cost_usd"] > 0).all()


class FakeMD:
    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def get_chain(self, symbol, from_date, to_date, strike_count):
        self.calls.append((symbol, from_date, to_date, strike_count))
        if symbol in self.fail_on:
            raise RuntimeError("Schwab 500")
        return fake_chain(symbol, 680.12 if symbol == "$XSP" else 6.8012 * 100)


def test_run_once_writes_partitioned_parquet_and_filters_dte(tmp_path):
    sink = LocalParquetSink(tmp_path)
    rec = Recorder(FakeMD(), sink, RecorderPlan(chain_symbols=("$XSP",), strikes_each_side=10))
    keys = rec.run_once(SNAP)
    assert "chains/symbol=XSP/date=2026-11-03/110000.parquet" in keys
    assert "delta_band/symbol=XSP/date=2026-11-03/110000.parquet" in keys
    back = pd.read_parquet(tmp_path / "chains/symbol=XSP/date=2026-11-03/110000.parquet")
    assert back["dte"].max() <= 2  # the 7DTE expiry was dropped


def test_one_symbol_failing_does_not_stop_others(tmp_path):
    rec = Recorder(FakeMD(fail_on={"$XSP"}), LocalParquetSink(tmp_path))
    keys = rec.run_once(SNAP)
    assert rec.errors == 1
    assert any(k.startswith("chains/symbol=SPY/") for k in keys)


class FakeClock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


def test_run_session_full_day_and_half_day(tmp_path):
    class CountingRecorder(Recorder):
        def run_once(self, now):
            self.seen = getattr(self, "seen", []) + [now]
            return []

    clock = FakeClock(datetime(2026, 11, 3, 8, 0, tzinfo=NY))
    rec = CountingRecorder(FakeMD(), LocalParquetSink(tmp_path))
    n = rec.run_session(pd.Timestamp("2026-11-03"), clock=clock, sleep=clock.sleep)
    assert n == 390 and rec.seen[0].astimezone(NY).strftime("%H:%M") == "09:30"

    # Day after Thanksgiving 2026 closes at 13:00 ET.
    clock = FakeClock(datetime(2026, 11, 27, 9, 0, tzinfo=NY))
    rec = CountingRecorder(FakeMD(), LocalParquetSink(tmp_path))
    assert rec.run_session(pd.Timestamp("2026-11-27"), clock=clock, sleep=clock.sleep) == 210

    assert rec.run_session(pd.Timestamp("2026-11-07")) == 0  # Saturday


def test_run_session_skips_missed_slots_instead_of_bursting(tmp_path):
    class SlowRecorder(Recorder):
        def run_once(self, now):
            clock.t += timedelta(seconds=150)  # a snapshot that takes 2.5 minutes
            return []

    clock = FakeClock(datetime(2026, 11, 3, 15, 50, tzinfo=NY))
    rec = SlowRecorder(FakeMD(), LocalParquetSink(tmp_path))
    n = rec.run_session(pd.Timestamp("2026-11-03"), clock=clock, sleep=clock.sleep)
    assert n == 4  # 15:50, 15:52:30, 15:55, 15:57:30 -> no catch-up burst
