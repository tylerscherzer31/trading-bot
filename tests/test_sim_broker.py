from datetime import datetime, timedelta

from bot import NY
from bot.broker.base import OrderRequest, Position, Side
from bot.broker.sim import FillModel, SimBroker
from tests.conftest import CONTRACT, NOW, make_quote

MODEL = FillModel(index_fee_per_contract=0.0)  # isolate commission in these tests


def broker(cash=1_500.0) -> SimBroker:
    b = SimBroker(settled_cash=cash, model=MODEL)
    b.set_clock(NOW)
    return b


def test_buys_at_ask_sells_at_bid_never_mid():
    b = broker()
    buy = b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), make_quote(1.40, 1.50))
    assert buy.price == 1.50
    b.place_stop(Position(CONTRACT, 1, buy.price, buy.fees, NOW, stop_price=0.90))
    sell = b.submit(OrderRequest(CONTRACT, Side.SELL_TO_CLOSE, 1), make_quote(1.60, 1.70))
    assert sell.price == 1.60


def test_round_trip_cost_includes_spread_and_fees():
    b = broker()
    q = make_quote(1.45, 1.55)
    buy = b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), q)
    b.place_stop(Position(CONTRACT, 1, buy.price, buy.fees, NOW, stop_price=0.5))
    b.submit(OrderRequest(CONTRACT, Side.SELL_TO_CLOSE, 1), q)  # flat market
    b.set_clock(datetime(2026, 11, 4, 9, 0, tzinfo=NY))  # proceeds settle T+1
    assert round(1_500 - b.account().settled_cash, 2) == 10.0 + 1.30  # spread + 2x $0.65


def test_rejects_stale_and_wide_quotes():
    b = broker()
    stale = make_quote(ts=NOW - timedelta(seconds=30))
    wide = make_quote(bid=1.00, ask=1.60)
    assert b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), stale) is None
    assert b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), wide) is None


def test_slippage_near_open_and_close():
    b = SimBroker(settled_cash=1_500, model=MODEL)
    early = datetime(2026, 11, 3, 9, 35, tzinfo=NY)
    b.set_clock(early)
    fill = b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), make_quote(ts=early))
    assert fill.price == 1.53  # ask 1.52 + 1 tick


def test_stop_fills_at_gapped_bid_not_stop_price():
    b = broker()
    buy = b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), make_quote(1.48, 1.52))
    b.place_stop(Position(CONTRACT, 1, buy.price, buy.fees, NOW, stop_price=0.91))
    later = NOW + timedelta(minutes=5)
    fill = b.on_quote(make_quote(0.60, 0.64, ts=later))  # gap straight through 0.91
    assert fill.reason == "stop" and fill.price == 0.60


def test_proceeds_unsettled_until_next_session():
    b = broker()
    q = make_quote()
    buy = b.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), q)
    b.place_stop(Position(CONTRACT, 1, buy.price, buy.fees, NOW, stop_price=0.5))
    b.submit(OrderRequest(CONTRACT, Side.SELL_TO_CLOSE, 1), q)
    same_day = b.account()
    assert same_day.settled_cash < same_day.equity  # sale proceeds pending
    # Friday sale settles Monday, not Saturday.
    fri = SimBroker(settled_cash=1_500, model=MODEL)
    fri_now = datetime(2026, 11, 6, 11, 0, tzinfo=NY)
    fri.set_clock(fri_now)
    fq = make_quote(ts=fri_now)
    b2 = fri.submit(OrderRequest(CONTRACT, Side.BUY_TO_OPEN, 1), fq)
    fri.place_stop(Position(CONTRACT, 1, b2.price, b2.fees, fri_now, stop_price=0.5))
    fri.submit(OrderRequest(CONTRACT, Side.SELL_TO_CLOSE, 1), fq)
    fri.set_clock(datetime(2026, 11, 7, 12, 0, tzinfo=NY))
    assert fri.account().settled_cash < 1_400
    fri.set_clock(datetime(2026, 11, 9, 9, 0, tzinfo=NY))
    assert fri.account().settled_cash > 1_490
