from datetime import datetime, timedelta

import pytest

from bot import NY
from bot.broker.base import AccountState, OptionContract, OptionQuote, Right
from bot.risk.guardrails import Health, RiskState

NOW = datetime(2026, 11, 3, 11, 0, tzinfo=NY)  # a Tuesday session, mid-morning
CONTRACT = OptionContract("XSP", "2026-11-03", 680.0, Right.CALL)


def make_quote(bid=1.48, ask=1.52, delta=0.50, ts=NOW, contract=CONTRACT) -> OptionQuote:
    return OptionQuote(contract, bid, ask, delta, ts)


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def healthy():
    return Health(
        token_expires_at=NOW + timedelta(days=3),
        last_quote_ts=NOW,
        account_readable=True,
    )


@pytest.fixture
def account():
    return AccountState(settled_cash=1_500.0, equity=1_500.0, as_of=NOW)


@pytest.fixture
def fresh_state():
    return RiskState(week_start_equity=1_500.0, peak_equity=1_500.0)
