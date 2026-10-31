"""Engine wiring: signal -> risk -> broker (SPEC §6).

One code path for backtest, paper and live; only the broker changes. This is
the Phase 0 skeleton: the entry decision and post-fill protection are real,
the full event loop lands in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.broker.base import Broker, OptionQuote, OrderRequest, Position, Side
from bot.risk.guardrails import (
    Health,
    LlmMode,
    RiskDecision,
    RiskState,
    check_entry,
    effective_stop_price,
)
from bot.signals.base import Direction, Signal


@dataclass(frozen=True)
class EntryResult:
    decision: RiskDecision
    position: Position | None
    note: str = ""


def try_enter(
    signal: Signal,
    quote: OptionQuote,
    broker: Broker,
    state: RiskState,
    health: Health,
    now: datetime,
    strategy_stop_pct: float,
    take_profit_pct: float,
    est_fees: float,
    llm_mode: LlmMode = LlmMode.NONE,
    llm_flag_enabled: bool = False,
) -> EntryResult:
    if signal.direction == Direction.FLAT:
        return EntryResult(RiskDecision(False, ("NO_SIGNAL",)), None)

    decision = check_entry(
        quote, 1, est_fees, broker.account(), state, health, now, llm_mode, llm_flag_enabled
    )
    if not decision.allowed:
        return EntryResult(decision, None)

    fill = broker.submit(OrderRequest(quote.contract, Side.BUY_TO_OPEN, 1), quote)
    if fill is None:
        return EntryResult(decision, None, "broker rejected or no fill")

    position = Position(
        contract=fill.contract,
        quantity=fill.quantity,
        entry_price=fill.price,
        entry_fees=fill.fees,
        opened_at=fill.ts,
        stop_price=effective_stop_price(fill.price, strategy_stop_pct),
        take_profit_price=round(fill.price * (1 + take_profit_pct), 2),
    )
    if not broker.place_stop(position):
        # SPEC §5: rejected broker-side stop -> flatten immediately.
        broker.submit(OrderRequest(position.contract, Side.SELL_TO_CLOSE, position.quantity), quote)
        return EntryResult(decision, None, "stop rejected; flattened")
    return EntryResult(decision, position)
