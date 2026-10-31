"""Broker interface and shared order/quote types (SPEC §6).

SimBroker and SchwabBroker implement the same interface so the engine never
knows which one it's talking to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Right(StrEnum):
    CALL = "C"
    PUT = "P"


class Side(StrEnum):
    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"


@dataclass(frozen=True)
class OptionContract:
    underlying: str  # "XSP" or "SPY"
    expiry: str  # YYYY-MM-DD
    strike: float
    right: Right

    @property
    def multiplier(self) -> int:
        return 100


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    bid: float
    ask: float
    delta: float
    ts: datetime  # quote timestamp (tz-aware)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct_of_mid(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else float("inf")


@dataclass(frozen=True)
class OrderRequest:
    contract: OptionContract
    side: Side
    quantity: int


@dataclass(frozen=True)
class Fill:
    contract: OptionContract
    side: Side
    quantity: int
    price: float  # per-share option price
    fees: float  # total $ for the fill
    ts: datetime
    reason: str = ""

    @property
    def gross(self) -> float:
        return self.price * self.quantity * self.contract.multiplier


@dataclass
class Position:
    contract: OptionContract
    quantity: int
    entry_price: float
    entry_fees: float
    opened_at: datetime
    stop_price: float  # resting broker-side stop (SPEC §5)
    take_profit_price: float | None = None
    meta: dict = field(default_factory=dict)

    @property
    def cost(self) -> float:
        return self.entry_price * self.quantity * self.contract.multiplier + self.entry_fees


@dataclass(frozen=True)
class AccountState:
    """Read from the broker, never from local bookkeeping (settled-cash guard)."""

    settled_cash: float
    equity: float
    as_of: datetime


class Broker(Protocol):
    def account(self) -> AccountState: ...

    def submit(self, order: OrderRequest, quote: OptionQuote) -> Fill | None: ...

    def place_stop(self, position: Position) -> bool:
        """Rest a stop at the broker. Returns False if rejected (caller must flatten)."""
        ...

    def open_position(self) -> Position | None: ...
