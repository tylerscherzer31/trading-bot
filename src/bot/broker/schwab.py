"""SchwabBroker (live). PROTECTED PATH: changes require a human-approved PR.

Not implemented until Phase 3. Construction refuses unless LIVE_TRADING=true
and BOT_PHASE >= 3 (CLAUDE.md rule 1).
"""

from __future__ import annotations

import os


class LiveTradingDisabled(RuntimeError):
    pass


def live_trading_allowed() -> bool:
    return os.getenv("LIVE_TRADING", "false").lower() == "true" and int(
        os.getenv("BOT_PHASE", "0")
    ) >= 3


class SchwabBroker:
    def __init__(self) -> None:
        if not live_trading_allowed():
            raise LiveTradingDisabled("LIVE_TRADING must be true and BOT_PHASE >= 3")
        raise NotImplementedError("SchwabBroker is built in Phase 3 (SPEC §9)")
