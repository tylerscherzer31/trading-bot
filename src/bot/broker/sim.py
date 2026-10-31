"""SimBroker: pessimistic fills from recorded/live Schwab chains (SPEC §4).

- Buy at the ask, sell at the bid. Never the midpoint.
- Commission + index fee per contract each way.
- +1 tick slippage near the open, near the close, and on fast moves.
- No fill on a stale quote or a spread wider than max_spread_pct of mid.
- Resting stops fill at the first bid at/below the stop, gaps included.
- Sale proceeds settle T+1 (cash account), so they aren't spendable same day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import exchange_calendars as xcals
import pandas as pd

from bot import NY
from bot.broker.base import AccountState, Fill, OptionQuote, OrderRequest, Position, Side


@dataclass(frozen=True)
class FillModel:
    commission_per_contract: float = 0.65
    # Pessimistic placeholder until Schwab confirms XSP index fees (SPEC §11).
    index_fee_per_contract: float = 0.50
    max_quote_age: timedelta = timedelta(seconds=10)
    max_spread_pct: float = 0.25
    open_slip_window: timedelta = timedelta(minutes=15)
    close_slip_start: time = time(15, 30)

    def tick(self, price: float) -> float:
        return 0.01 if price < 3.0 else 0.05

    def fees(self, quantity: int) -> float:
        return round((self.commission_per_contract + self.index_fee_per_contract) * quantity, 2)

    def slip_ticks(self, now: datetime, fast_move: bool) -> int:
        t = now.astimezone(NY)
        open_dt = t.replace(hour=9, minute=30, second=0, microsecond=0)
        near_open = open_dt <= t < open_dt + self.open_slip_window
        near_close = t.time() >= self.close_slip_start
        return 1 if (near_open or near_close or fast_move) else 0


_XNYS = xcals.get_calendar("XNYS")


def next_session(d: date) -> date:
    return _XNYS.next_session(pd.Timestamp(d)).date()


@dataclass
class SimBroker:
    settled_cash: float
    model: FillModel = field(default_factory=FillModel)
    now: datetime | None = None
    _pending: list[tuple[date, float]] = field(default_factory=list)  # (settle date, amount)
    _position: Position | None = None
    _last_bid: float | None = None
    fills: list[Fill] = field(default_factory=list)

    # --- clock -----------------------------------------------------------
    def set_clock(self, now: datetime) -> None:
        self.now = now
        today = now.astimezone(NY).date()
        still: list[tuple[date, float]] = []
        for settle, amt in self._pending:
            if settle <= today:
                self.settled_cash += amt
            else:
                still.append((settle, amt))
        self._pending = still

    # --- Broker interface ------------------------------------------------
    def account(self) -> AccountState:
        assert self.now is not None, "call set_clock first"
        unsettled = sum(a for _, a in self._pending)
        mark = 0.0
        if self._position and self._last_bid is not None:
            p = self._position
            mark = self._last_bid * p.quantity * p.contract.multiplier
        return AccountState(
            settled_cash=round(self.settled_cash, 2),
            equity=round(self.settled_cash + unsettled + mark, 2),
            as_of=self.now,
        )

    def open_position(self) -> Position | None:
        return self._position

    def submit(
        self, order: OrderRequest, quote: OptionQuote, fast_move: bool = False
    ) -> Fill | None:
        assert self.now is not None, "call set_clock first"
        if self.now - quote.ts > self.model.max_quote_age:
            return None
        if quote.spread_pct_of_mid > self.model.max_spread_pct:
            return None
        slip = self.model.slip_ticks(self.now, fast_move)
        fees = self.model.fees(order.quantity)

        if order.side == Side.BUY_TO_OPEN:
            price = round(quote.ask + slip * self.model.tick(quote.ask), 2)
            cost = price * order.quantity * order.contract.multiplier + fees
            if cost > self.settled_cash:
                return None  # settled-cash guard also lives in risk/; belt and braces
            self.settled_cash -= cost
            fill = Fill(order.contract, order.side, order.quantity, price, fees, self.now, "entry")
            # The broker holds the position from the moment of fill; place_stop() attaches
            # the protective stop. Until then stop_price=0 means "unprotected".
            self._position = Position(
                order.contract, order.quantity, price, fees, self.now, stop_price=0.0
            )
        else:
            if self._position is None:
                return None
            price = max(round(quote.bid - slip * self.model.tick(quote.bid), 2), 0.0)
            fill = self._close(price, fees, "exit")
        self.fills.append(fill)
        self._last_bid = quote.bid
        return fill

    def place_stop(self, position: Position) -> bool:
        self._position = position
        return True

    # --- simulation hooks -------------------------------------------------
    def on_quote(self, quote: OptionQuote) -> Fill | None:
        """Feed every quote for the held contract. Triggers resting stop / take-profit."""
        if self.now is None or quote.ts > self.now:
            self.set_clock(quote.ts)
        p = self._position
        if p is None or quote.contract != p.contract:
            return None
        self._last_bid = quote.bid
        fees = self.model.fees(p.quantity)
        if quote.bid <= p.stop_price:
            # Stop becomes a market sell: first available bid, gaps included.
            fill = self._close(quote.bid, fees, "stop")
            self.fills.append(fill)
            return fill
        if p.take_profit_price is not None and quote.bid >= p.take_profit_price:
            fill = self._close(p.take_profit_price, fees, "take_profit")
            self.fills.append(fill)
            return fill
        return None

    def _close(self, price: float, fees: float, reason: str) -> Fill:
        assert self._position is not None and self.now is not None
        p = self._position
        proceeds = price * p.quantity * p.contract.multiplier - fees
        settle = next_session(self.now.astimezone(NY).date())
        self._pending.append((settle, proceeds))
        self._position = None
        return Fill(p.contract, Side.SELL_TO_CLOSE, p.quantity, price, fees, self.now, reason)
