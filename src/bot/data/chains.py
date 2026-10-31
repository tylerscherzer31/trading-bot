"""Normalize Schwab option-chain JSON into flat rows (one per contract).

Schwab's /chains response nests contracts as
  callExpDateMap / putExpDateMap -> "YYYY-MM-DD:DTE" -> "strike" -> [contract]
and uses -999 (sometimes "NaN") for unavailable greeks. Both become NaN here.

Field names follow Schwab's Market Data API docs; verify against the first live
response before relying on any column not covered by tests.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pandas as pd

COLUMNS = [
    "snapshot_ts", "underlying", "underlying_price", "expiry", "dte", "strike", "right",
    "symbol", "bid", "ask", "mark", "last", "bid_size", "ask_size", "volume",
    "open_interest", "iv", "delta", "gamma", "theta", "vega", "quote_ts",
]


def _num(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return math.nan
    return math.nan if x <= -999 or math.isinf(x) else x


def _ms_to_utc(ms: Any) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(int(ms), unit="ms", tz=UTC)
    except (TypeError, ValueError):
        return None


def normalize_chain(
    raw: dict[str, Any], snapshot_ts: datetime, strikes_each_side: int | None = None
) -> pd.DataFrame:
    underlying = str(raw.get("symbol", "")).lstrip("$")
    spot = _num(raw.get("underlyingPrice"))
    rows: list[dict[str, Any]] = []
    for map_key, right in (("callExpDateMap", "C"), ("putExpDateMap", "P")):
        for exp_key, strikes in (raw.get(map_key) or {}).items():
            expiry, _, dte = exp_key.partition(":")
            for strike_key, contracts in strikes.items():
                for c in contracts:
                    days = int(dte) if dte.isdigit() else int(_num(c.get("daysToExpiration")))
                    rows.append(
                        {
                            "snapshot_ts": pd.Timestamp(snapshot_ts).tz_convert(UTC),
                            "underlying": underlying,
                            "underlying_price": spot,
                            "expiry": expiry,
                            "dte": days,
                            "strike": float(strike_key),
                            "right": right,
                            "symbol": c.get("symbol", ""),
                            "bid": _num(c.get("bid")),
                            "ask": _num(c.get("ask")),
                            "mark": _num(c.get("mark")),
                            "last": _num(c.get("last")),
                            "bid_size": _num(c.get("bidSize")),
                            "ask_size": _num(c.get("askSize")),
                            "volume": _num(c.get("totalVolume")),
                            "open_interest": _num(c.get("openInterest")),
                            "iv": _num(c.get("volatility")),
                            "delta": _num(c.get("delta")),
                            "gamma": _num(c.get("gamma")),
                            "theta": _num(c.get("theta")),
                            "vega": _num(c.get("vega")),
                            "quote_ts": _ms_to_utc(c.get("quoteTimeInLong")),
                        }
                    )
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    if strikes_each_side is not None and not math.isnan(spot):
        keep = set()
        for (_, _), g in df.groupby(["expiry", "right"]):
            uniq = sorted(g["strike"].unique())
            if not uniq:
                continue
            atm = min(range(len(uniq)), key=lambda i: abs(uniq[i] - spot))
            keep.update(uniq[max(0, atm - strikes_each_side): atm + strikes_each_side + 1])
        df = df[df["strike"].isin(keep)]
    return df.sort_values(["expiry", "right", "strike"]).reset_index(drop=True)


def delta_band_metrics(
    chain: pd.DataFrame, delta_min: float = 0.40, delta_max: float = 0.55
) -> pd.DataFrame:
    """Per expiry/right: median spread and premium for contracts in the guardrail delta band.

    This is what decides XSP vs SPY and the Phase 3 capital (SPEC §5, §9).
    """
    if chain.empty:
        return pd.DataFrame()
    band = chain[chain["delta"].abs().between(delta_min, delta_max)].copy()
    band = band[(band["bid"] > 0) & (band["ask"] >= band["bid"])]
    if band.empty:
        return pd.DataFrame()
    band["spread"] = band["ask"] - band["bid"]
    band["mid"] = (band["ask"] + band["bid"]) / 2
    band["spread_pct_mid"] = band["spread"] / band["mid"]
    out = (
        band.groupby(["snapshot_ts", "underlying", "expiry", "dte", "right"])
        .agg(
            n=("symbol", "count"),
            median_ask=("ask", "median"),
            median_spread=("spread", "median"),
            median_spread_pct=("spread_pct_mid", "median"),
        )
        .reset_index()
    )
    out["contract_cost_usd"] = out["median_ask"] * 100
    return out
