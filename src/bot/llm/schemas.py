"""Schemas for Claude's off-hours outputs (SPEC §7). Claude is never on the order path.

The schemas are the enforcement point: a flag can only restrict trading,
and a proposal can only touch strategy.yaml keys, one at a time.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bot.risk.guardrails import LlmMode


class PremarketMode(StrEnum):
    NORMAL = "NORMAL"
    SIZE_DOWN = "SIZE_DOWN"
    SIT_OUT = "SIT_OUT"

    def to_risk_mode(self) -> LlmMode:
        return {
            "NORMAL": LlmMode.NONE,
            "SIZE_DOWN": LlmMode.SIZE_DOWN,
            "SIT_OUT": LlmMode.SIT_OUT,
        }[self.value]


class Lean(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


class PremarketFlag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: PremarketMode
    lean: Lean
    lean_probability: float = Field(ge=0.0, le=1.0)  # scored with Brier
    reasons: list[str] = Field(max_length=6)
    model: str
    prompt_version: str


# Keys the weekly review may propose to change. Guardrails are absent by design.
PROPOSABLE_KEYS = frozenset(
    {
        "entry.no_entry_first_minutes",
        "entry.last_entry_time_0dte",
        "entry.target_delta",
        "exit.take_profit_pct",
        "exit.stop_pct",
        "exit.signal_flip_exit",
        "exit.time_exit",
        "h1_noise_area.lookback_days",
        "h1_noise_area.band_multiplier",
        "h1_noise_area.check_every_minutes",
    }
)


class ParameterProposal(BaseModel):
    """Exactly one strategy.yaml change, stated as a hypothesis before testing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    current_value: str | float | int | bool
    proposed_value: str | float | int | bool
    hypothesis: str = Field(min_length=20)
    expected_effect: str

    @field_validator("key")
    @classmethod
    def key_must_be_proposable(cls, v: str) -> str:
        if v not in PROPOSABLE_KEYS:
            raise ValueError(f"{v!r} is not a proposable strategy.yaml key")
        return v
