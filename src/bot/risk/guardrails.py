"""Guardrails (SPEC §5, locked in v0.3). PROTECTED PATH.

Nothing outside a human-approved PR may change these values. The review loop,
strategy.yaml, and every LLM output are deliberately unable to reach them.

Design rule: a 0DTE option can gap to zero through any stop, so the full
premium is treated as the worst-case loss of every trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum

from bot import NY
from bot.broker.base import AccountState, OptionQuote


class LlmMode(StrEnum):
    NONE = "NONE"  # SPEC §5 output set; NORMAL in the premarket schema maps here
    SIZE_DOWN = "SIZE_DOWN"
    SIT_OUT = "SIT_OUT"


@dataclass(frozen=True)
class RiskLimits:
    delta_min: float = 0.40
    delta_max: float = 0.55
    # (equity strictly below, max premium as fraction of settled cash)
    premium_tiers: tuple[tuple[float, float], ...] = (
        (5_000.0, 0.20),
        (20_000.0, 0.10),
        (float("inf"), 0.05),
    )
    guard_stop_pct: float = 0.40
    max_trades_per_day: int = 1
    max_trades_per_day_above_threshold: int = 2
    trades_threshold_equity: float = 2_000.0
    daily_halt_losses: int = 1
    weekly_halt_drawdown: float = 0.10
    circuit_breaker_drawdown: float = 0.30
    flat_by: time = time(15, 45)
    token_min_remaining: timedelta = timedelta(hours=1)
    max_quote_age: timedelta = timedelta(seconds=10)
    size_down_factor: float = 0.5
    contract_multiplier: int = 100


LIMITS = RiskLimits()


@dataclass(frozen=True)
class RiskState:
    """Built by the engine each decision from the trade ledger + broker account."""

    trades_today: int = 0
    losses_today: int = 0
    stopped_out_today: bool = False
    week_start_equity: float = 0.0
    peak_equity: float = 0.0
    circuit_tripped: bool = False  # latched; only a human PR/reset clears it
    has_open_position: bool = False


@dataclass(frozen=True)
class Health:
    token_expires_at: datetime | None
    last_quote_ts: datetime | None
    account_readable: bool


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    max_premium: float = 0.0


def max_premium_fraction(equity: float, limits: RiskLimits = LIMITS) -> float:
    for below, frac in limits.premium_tiers:
        if equity < below:
            return frac
    return limits.premium_tiers[-1][1]


def max_trades_today(equity: float, limits: RiskLimits = LIMITS) -> int:
    if equity > limits.trades_threshold_equity:
        return limits.max_trades_per_day_above_threshold
    return limits.max_trades_per_day


def effective_stop_price(
    entry_price: float, strategy_stop_pct: float, limits: RiskLimits = LIMITS
) -> float:
    """Strategy stops may be tighter than the guardrail stop, never wider."""
    pct = min(max(strategy_stop_pct, 0.0), limits.guard_stop_pct)
    return round(entry_price * (1 - pct), 2)


def must_flatten(now: datetime, limits: RiskLimits = LIMITS) -> bool:
    return now.astimezone(NY).time() >= limits.flat_by


def circuit_breaker_hit(state: RiskState, equity: float, limits: RiskLimits = LIMITS) -> bool:
    if state.circuit_tripped:
        return True
    if state.peak_equity <= 0:
        return False
    return (state.peak_equity - equity) / state.peak_equity >= limits.circuit_breaker_drawdown


def check_entry(
    quote: OptionQuote,
    quantity: int,
    est_fees: float,
    account: AccountState,
    state: RiskState,
    health: Health,
    now: datetime,
    llm_mode: LlmMode = LlmMode.NONE,
    llm_flag_enabled: bool = False,
    limits: RiskLimits = LIMITS,
) -> RiskDecision:
    """Every entry passes through here. Returns all failing reasons, not just the first."""
    reasons: list[str] = []

    # Fail closed (SPEC §5).
    if not health.account_readable:
        reasons.append("FAIL_CLOSED_ACCOUNT_UNREADABLE")
    token = health.token_expires_at
    if token is None or token - now < limits.token_min_remaining:
        reasons.append("FAIL_CLOSED_TOKEN")
    if health.last_quote_ts is None or now - health.last_quote_ts > limits.max_quote_age:
        reasons.append("FAIL_CLOSED_STALE_FEED")

    # Session.
    if must_flatten(now, limits):
        reasons.append("AFTER_FLAT_BY")

    # Halts.
    if circuit_breaker_hit(state, account.equity, limits):
        reasons.append("CIRCUIT_BREAKER")
    if state.week_start_equity > 0 and (
        (state.week_start_equity - account.equity) / state.week_start_equity
        >= limits.weekly_halt_drawdown
    ):
        reasons.append("WEEKLY_HALT")
    if state.losses_today >= limits.daily_halt_losses:
        reasons.append("DAILY_HALT")
    if state.stopped_out_today:
        reasons.append("NO_REENTRY_AFTER_STOP")
    if state.trades_today >= max_trades_today(account.equity, limits):
        reasons.append("MAX_TRADES_TODAY")
    if state.has_open_position:
        reasons.append("POSITION_ALREADY_OPEN")  # also blocks averaging down

    # LLM flag: can only restrict, never enlarge. Ignored until promoted (SPEC §7).
    cap_frac = max_premium_fraction(account.equity, limits)
    if llm_flag_enabled:
        if llm_mode == LlmMode.SIT_OUT:
            reasons.append("LLM_SIT_OUT")
        elif llm_mode == LlmMode.SIZE_DOWN:
            cap_frac *= limits.size_down_factor

    # Contract and size.
    if quantity < 1:
        reasons.append("BAD_QUANTITY")
    if not (limits.delta_min <= abs(quote.delta) <= limits.delta_max):
        reasons.append("DELTA_OUT_OF_BAND")

    max_premium = cap_frac * account.settled_cash
    cost = quote.ask * quantity * limits.contract_multiplier + est_fees
    if cost > max_premium:
        # Never move further OTM to fit: the caller must skip the trade.
        reasons.append("PREMIUM_CAP")
    if cost > account.settled_cash:
        reasons.append("SETTLED_CASH")

    return RiskDecision(allowed=not reasons, reasons=tuple(reasons), max_premium=max_premium)
