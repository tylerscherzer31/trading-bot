"""Token lifecycle (SPEC §6 token service, §5 fail-closed).

Schwab issues a 30-minute access token (auto-refreshed) and a 7-day refresh
token that can only be renewed by a manual browser login. The refresh token's
expiry is therefore the bot's real deadline: it feeds `Health.token_expires_at`
in risk/, and the alerting here exists so that deadline never arrives mid-week.

Re-auth is a fixed weekly slot (Saturday 10:00 ET), not left to the alert.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from bot import NY

ACCESS_TTL = timedelta(minutes=30)
REFRESH_TTL = timedelta(days=7)
ALERT_WINDOW = timedelta(hours=24)
ALERT_REPEAT = timedelta(hours=6)
REAUTH_WEEKDAY = 5  # Saturday
REAUTH_TIME = time(10, 0)


@dataclass(frozen=True)
class TokenRecord:
    """Timestamps only. The token strings themselves stay inside schwabdev's store."""

    refresh_issued_at: datetime
    access_issued_at: datetime

    # TTLs are absolute durations: add them in UTC, or a DST change shifts expiry by an hour.
    @property
    def refresh_expires_at(self) -> datetime:
        return self.refresh_issued_at.astimezone(UTC) + REFRESH_TTL

    @property
    def access_expires_at(self) -> datetime:
        return self.access_issued_at.astimezone(UTC) + ACCESS_TTL


class TokenStatus(StrEnum):
    OK = "OK"
    EXPIRING = "EXPIRING"  # inside the 24h alert window
    EXPIRED = "EXPIRED"
    MISSING = "MISSING"


@dataclass(frozen=True)
class TokenHealth:
    status: TokenStatus
    refresh_expires_at: datetime | None
    time_left: timedelta | None

    @property
    def can_trade(self) -> bool:
        return self.status in (TokenStatus.OK, TokenStatus.EXPIRING)


def token_health(record: TokenRecord | None, now: datetime) -> TokenHealth:
    if record is None:
        return TokenHealth(TokenStatus.MISSING, None, None)
    exp = record.refresh_expires_at
    left = exp - now
    if left <= timedelta(0):
        status = TokenStatus.EXPIRED
    elif left <= ALERT_WINDOW:
        status = TokenStatus.EXPIRING
    else:
        status = TokenStatus.OK
    return TokenHealth(status, exp, left)


def should_alert(health: TokenHealth, now: datetime, last_alert_at: datetime | None) -> bool:
    """Alert inside the 24h window (and when expired/missing), at most every 6 hours."""
    if health.status == TokenStatus.OK:
        return False
    return last_alert_at is None or now - last_alert_at >= ALERT_REPEAT


def alert_message(health: TokenHealth) -> str:
    if health.status == TokenStatus.MISSING:
        return "Schwab tokens missing: bot is fail-closed until you log in."
    if health.status == TokenStatus.EXPIRED:
        return "Schwab refresh token EXPIRED: bot is fail-closed. Re-authenticate now."
    assert health.time_left is not None and health.refresh_expires_at is not None
    hours = health.time_left.total_seconds() / 3600
    when = health.refresh_expires_at.astimezone(NY).strftime("%a %b %d %H:%M ET")
    return f"Schwab refresh token expires in {hours:.1f}h ({when}). Re-authenticate."


def next_reauth_slot(now: datetime) -> datetime:
    """Next Saturday 10:00 ET at or after `now`."""
    local = now.astimezone(NY)
    days = (REAUTH_WEEKDAY - local.weekday()) % 7
    slot = datetime.combine(local.date() + timedelta(days=days), REAUTH_TIME, tzinfo=NY)
    if slot < local:
        slot += timedelta(days=7)
    return slot


def survives_trading_week(record: TokenRecord | None, now: datetime) -> bool:
    """True if the refresh token outlives Friday's close before the next re-auth slot.

    If False, waiting for the weekly slot means the token dies mid-week:
    re-authenticate early.
    """
    if record is None:
        return False
    friday_close = next_reauth_slot(now) - timedelta(hours=18)  # Sat 10:00 -> Fri 16:00
    return record.refresh_expires_at > friday_close


# --- storage -------------------------------------------------------------


class TokenStore(Protocol):
    def load(self) -> TokenRecord | None: ...

    def save(self, record: TokenRecord) -> None: ...


@dataclass
class FileTokenStore:
    """Local dev store for token *metadata*. DynamoDB store arrives with DataStack."""

    path: Path

    def load(self) -> TokenRecord | None:
        if not self.path.exists():
            return None
        d = json.loads(self.path.read_text())
        return TokenRecord(
            refresh_issued_at=datetime.fromisoformat(d["refresh_issued_at"]),
            access_issued_at=datetime.fromisoformat(d["access_issued_at"]),
        )

    def save(self, record: TokenRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "refresh_issued_at": record.refresh_issued_at.astimezone(UTC).isoformat(),
                    "access_issued_at": record.access_issued_at.astimezone(UTC).isoformat(),
                }
            )
        )
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
