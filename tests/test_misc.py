import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from bot.broker.schwab import LiveTradingDisabled, SchwabBroker
from bot.broker.sim import SimBroker
from bot.config import load_strategy
from bot.data.recorder import RecorderPlan, main, s3_key
from bot.engine.loop import try_enter
from bot.ledger.trial_ledger import EntryKind, TrialLedger
from bot.llm.schemas import ParameterProposal, PremarketFlag
from bot.signals.base import Direction, Signal, assert_closed_bars
from bot.signals.c0 import c0_signal
from tests.conftest import NOW, make_quote

ROOT = Path(__file__).resolve().parents[1]


def test_strategy_yaml_valid():
    s = load_strategy(ROOT / "strategy.yaml")
    assert s.exit.stop_pct <= 0.40


def test_strategy_rejects_wider_stop(tmp_path):
    text = (ROOT / "strategy.yaml").read_text().replace("stop_pct: 0.30", "stop_pct: 0.55")
    p = tmp_path / "s.yaml"
    p.write_text(text)
    with pytest.raises(ValidationError):
        load_strategy(p)


def test_live_broker_refuses_by_default(monkeypatch):
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    with pytest.raises(LiveTradingDisabled):
        SchwabBroker()


def test_ledger_append_only_and_tamper_evident(tmp_path):
    led = TrialLedger(tmp_path / "ledger.jsonl")
    led.append(EntryKind.PROPOSAL, {"key": "exit.stop_pct"})
    led.append(EntryKind.BACKTEST, {"sharpe": 0.4})
    led.append(EntryKind.HOLDOUT_TOUCH, {"candidate": "x"})
    assert led.verify() and led.trial_count() == 2 and led.holdout_touches() == 1
    lines = led.path.read_text().splitlines()
    e = json.loads(lines[1])
    e["payload"]["sharpe"] = 2.5  # someone "improves" a result
    lines[1] = json.dumps(e, sort_keys=True)
    led.path.write_text("\n".join(lines) + "\n")
    assert not led.verify()


def test_proposal_cannot_touch_guardrails():
    with pytest.raises(ValidationError):
        ParameterProposal(key="risk.guard_stop_pct", current_value=0.4, proposed_value=0.6,
                          hypothesis="wider stops reduce whipsaw losses", expected_effect="?")
    ParameterProposal(key="exit.stop_pct", current_value=0.3, proposed_value=0.25,
                      hypothesis="tighter stops cut average loss size", expected_effect="lower DD")


def test_premarket_flag_schema_is_closed():
    with pytest.raises(ValidationError):
        PremarketFlag(mode="BUY_NOW", lean="BULL", lean_probability=0.6, reasons=[],
                      model="m", prompt_version="v1")


def test_c0_deterministic_and_no_lookahead():
    idx = pd.date_range("2026-11-03 09:31", periods=60, freq="1min", tz="America/New_York")
    bars = pd.DataFrame({"close": range(60)}, index=idx)
    t = idx[-1]
    assert c0_signal(bars, t, 7) == c0_signal(bars, t, 7)
    with pytest.raises(ValueError):
        assert_closed_bars(bars, idx[10])
    dirs = {c0_signal(bars, t, s).direction for s in range(50)}
    assert dirs == {Direction.LONG_CALL, Direction.LONG_PUT}


def test_recorder_dry_run_and_budget(capsys):
    assert main(["--dry-run", "--date", "2026-11-03"]) == 0
    assert "API budget" in capsys.readouterr().out
    assert RecorderPlan().requests_per_minute() < 120
    assert main(["--dry-run", "--date", "2026-11-07"]) == 0  # Saturday
    assert s3_key("chains", "$XSP", NOW).startswith("chains/symbol=XSP/date=2026-11-03/")


def test_engine_entry_places_protective_stop(healthy, fresh_state, account):
    b = SimBroker(settled_cash=1_500.0)
    b.set_clock(NOW)
    sig = Signal(Direction.LONG_CALL, pd.Timestamp(NOW))
    r = try_enter(sig, make_quote(), b, fresh_state, healthy, NOW,
                  strategy_stop_pct=0.30, take_profit_pct=0.60, est_fees=1.15)
    assert r.position is not None
    assert r.position.stop_price == round(r.position.entry_price * 0.70, 2)
    assert b.open_position() is r.position


def test_engine_flattens_if_stop_rejected(healthy, fresh_state):
    class NoStops(SimBroker):
        def place_stop(self, position):
            return False

    b = NoStops(settled_cash=1_500.0)
    b.set_clock(NOW)
    r = try_enter(Signal(Direction.LONG_CALL, pd.Timestamp(NOW)), make_quote(), b, fresh_state,
                  healthy, NOW, 0.30, 0.60, 1.15)
    assert r.position is None and "flattened" in r.note
    assert [f.side.value for f in b.fills] == ["BUY_TO_OPEN", "SELL_TO_CLOSE"]
