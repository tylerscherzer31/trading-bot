"""Load and validate strategy.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.risk.guardrails import LIMITS


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_entry_first_minutes: int = Field(ge=15)
    last_entry_time_0dte: str
    target_delta: float


class Exit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    take_profit_pct: float = Field(gt=0)
    stop_pct: float = Field(gt=0)
    signal_flip_exit: bool
    time_exit: str


class C0(BaseModel):
    seed: int


class H1(BaseModel):
    lookback_days: int = Field(ge=5)
    band_multiplier: float = Field(gt=0)
    check_every_minutes: int = Field(ge=1)


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    instrument: str = Field(pattern="^(XSP|SPY)$")
    expiration_dte: int = Field(ge=0, le=1)
    hypothesis: str = Field(pattern="^(H1|H2|H3|C0)$")
    entry: Entry
    exit: Exit
    c0: C0
    h1_noise_area: H1

    @model_validator(mode="after")
    def respect_guardrails(self) -> Strategy:
        if not (LIMITS.delta_min <= self.entry.target_delta <= LIMITS.delta_max):
            raise ValueError("target_delta outside guardrail band")
        if self.exit.stop_pct > LIMITS.guard_stop_pct:
            raise ValueError("strategy stop may be tighter than the guardrail, never wider")
        if self.exit.time_exit >= LIMITS.flat_by.strftime("%H:%M"):
            raise ValueError("time_exit must be before the 15:45 flat-by")
        return self


def load_strategy(path: str | Path = "strategy.yaml") -> Strategy:
    return Strategy.model_validate(yaml.safe_load(Path(path).read_text()))
