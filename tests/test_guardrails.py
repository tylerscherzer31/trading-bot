from dataclasses import replace
from datetime import datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from bot import NY
from bot.broker.base import AccountState
from bot.risk.guardrails import (
    LIMITS,
    Health,
    LlmMode,
    RiskState,
    check_entry,
    effective_stop_price,
    max_premium_fraction,
    must_flatten,
)
from tests.conftest import NOW, make_quote


def decide(quote, account, state, health, now=NOW, **kw):
    return check_entry(quote, 1, 1.15, account, state, health, now, **kw)


def test_clean_entry_allowed(account, fresh_state, healthy):
    d = decide(make_quote(), account, fresh_state, healthy)
    assert d.allowed, d.reasons
    assert d.max_premium == 300.0  # 20% of $1,500


def test_locked_values_match_spec_v03():
    assert (LIMITS.delta_min, LIMITS.delta_max) == (0.40, 0.55)
    assert LIMITS.guard_stop_pct == 0.40
    assert LIMITS.weekly_halt_drawdown == 0.10
    assert LIMITS.circuit_breaker_drawdown == 0.30
    assert LIMITS.flat_by.strftime("%H:%M") == "15:45"
    assert max_premium_fraction(1_000) == 0.20
    assert max_premium_fraction(5_000) == 0.10
    assert max_premium_fraction(20_000) == 0.05


def test_unaffordable_contract_is_skipped_not_resized(fresh_state, healthy):
    small = AccountState(settled_cash=500.0, equity=500.0, as_of=NOW)  # cap $100
    d = decide(make_quote(bid=1.48, ask=1.52), small, fresh_state, healthy)
    assert not d.allowed and "PREMIUM_CAP" in d.reasons


def test_delta_band(account, fresh_state, healthy):
    for bad in (0.25, 0.70):
        d = decide(make_quote(delta=bad), account, fresh_state, healthy)
        assert "DELTA_OUT_OF_BAND" in d.reasons
    assert decide(make_quote(delta=-0.45), account, fresh_state, healthy).allowed  # puts


def test_settled_cash_guard(fresh_state, healthy):
    acct = AccountState(settled_cash=100.0, equity=5_000.0, as_of=NOW)
    assert "SETTLED_CASH" in decide(make_quote(), acct, fresh_state, healthy).reasons


def test_fail_closed(account, fresh_state):
    stale = Health(NOW + timedelta(days=3), NOW - timedelta(minutes=2), True)
    expiring = Health(NOW + timedelta(minutes=30), NOW, True)
    unreadable = Health(NOW + timedelta(days=3), NOW, False)
    assert "FAIL_CLOSED_STALE_FEED" in decide(make_quote(), account, fresh_state, stale).reasons
    assert "FAIL_CLOSED_TOKEN" in decide(make_quote(), account, fresh_state, expiring).reasons
    assert "FAIL_CLOSED_ACCOUNT_UNREADABLE" in decide(
        make_quote(), account, fresh_state, unreadable
    ).reasons


def test_halts(account, fresh_state, healthy):
    q = make_quote()
    assert "DAILY_HALT" in decide(q, account, replace(fresh_state, losses_today=1), healthy).reasons
    assert "MAX_TRADES_TODAY" in decide(
        q, account, replace(fresh_state, trades_today=1), healthy
    ).reasons
    assert "NO_REENTRY_AFTER_STOP" in decide(
        q, account, replace(fresh_state, stopped_out_today=True), healthy
    ).reasons
    assert "POSITION_ALREADY_OPEN" in decide(
        q, account, replace(fresh_state, has_open_position=True), healthy
    ).reasons


def test_weekly_halt_is_drawdown_based(fresh_state, healthy):
    down_10 = AccountState(settled_cash=1_350.0, equity=1_350.0, as_of=NOW)
    assert "WEEKLY_HALT" in decide(make_quote(), down_10, fresh_state, healthy).reasons
    down_5 = AccountState(settled_cash=1_425.0, equity=1_425.0, as_of=NOW)
    assert "WEEKLY_HALT" not in decide(make_quote(), down_5, fresh_state, healthy).reasons


def test_circuit_breaker_latches(account, healthy):
    state = RiskState(week_start_equity=1_500, peak_equity=2_200)  # 1500/2200 = -31.8%
    assert "CIRCUIT_BREAKER" in decide(make_quote(), account, state, healthy).reasons
    latched = RiskState(week_start_equity=1_500, peak_equity=1_500, circuit_tripped=True)
    assert "CIRCUIT_BREAKER" in decide(make_quote(), account, latched, healthy).reasons


def test_second_trade_only_above_2k(fresh_state, healthy):
    rich = AccountState(settled_cash=2_500.0, equity=2_500.0, as_of=NOW)
    st1 = replace(fresh_state, trades_today=1, week_start_equity=2_500, peak_equity=2_500)
    assert decide(make_quote(), rich, st1, healthy).allowed


def test_flat_by(account, fresh_state, healthy):
    late = datetime(2026, 11, 3, 15, 45, tzinfo=NY)
    assert must_flatten(late)
    h = replace(healthy, last_quote_ts=late)
    assert "AFTER_FLAT_BY" in decide(make_quote(ts=late), account, fresh_state, h, now=late).reasons


def test_llm_can_only_restrict(account, fresh_state, healthy):
    q = make_quote()
    # Logged-only flag (not promoted) changes nothing.
    assert decide(q, account, fresh_state, healthy, llm_mode=LlmMode.SIT_OUT).allowed
    assert "LLM_SIT_OUT" in decide(
        q, account, fresh_state, healthy, llm_mode=LlmMode.SIT_OUT, llm_flag_enabled=True
    ).reasons
    sized = decide(q, account, fresh_state, healthy, llm_mode=LlmMode.SIZE_DOWN,
                   llm_flag_enabled=True)
    assert sized.max_premium == 150.0


@given(entry=st.floats(0.05, 20), strat=st.floats(0.0, 2.0))
def test_stop_never_wider_than_guardrail(entry, strat):
    stop = effective_stop_price(entry, strat)
    assert stop >= round(entry * (1 - LIMITS.guard_stop_pct), 2) - 0.005


@given(cash=st.floats(1, 1_000_000), ask=st.floats(0.05, 50))
def test_approved_cost_never_exceeds_cap_or_cash(cash, ask):
    healthy = Health(NOW + timedelta(days=3), NOW, True)
    acct = AccountState(settled_cash=cash, equity=cash, as_of=NOW)
    d = decide(make_quote(bid=max(ask - 0.02, 0.01), ask=ask), acct,
               RiskState(week_start_equity=cash, peak_equity=cash), healthy)
    if d.allowed:
        cost = ask * 100 + 1.15
        assert cost <= d.max_premium and cost <= cash
