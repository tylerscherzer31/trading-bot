from datetime import UTC, datetime, timedelta

import pytest

from bot import NY
from bot.auth.schwab_client import MissingCredentials, credentials_from_env
from bot.auth.tokens import (
    FileTokenStore,
    TokenRecord,
    TokenStatus,
    alert_message,
    next_reauth_slot,
    should_alert,
    survives_trading_week,
    token_health,
)

SAT_10 = datetime(2026, 10, 31, 10, 0, tzinfo=NY)


def rec(issued: datetime) -> TokenRecord:
    return TokenRecord(refresh_issued_at=issued, access_issued_at=issued)


def test_health_states():
    r = rec(SAT_10)
    assert token_health(None, SAT_10).status == TokenStatus.MISSING
    assert token_health(r, SAT_10 + timedelta(days=3)).status == TokenStatus.OK
    assert token_health(r, SAT_10 + timedelta(days=6, hours=1)).status == TokenStatus.EXPIRING
    assert token_health(r, SAT_10 + timedelta(days=7)).status == TokenStatus.EXPIRED
    # Expiry is exactly 168h later even across the Nov 1 DST change.
    assert (r.refresh_expires_at - SAT_10).total_seconds() == 7 * 24 * 3600
    assert token_health(r, SAT_10 + timedelta(days=6, hours=1)).can_trade
    assert not token_health(r, SAT_10 + timedelta(days=8)).can_trade


def test_alert_throttled_every_6h():
    r = rec(SAT_10)
    now = SAT_10 + timedelta(days=6, hours=2)
    h = token_health(r, now)
    assert should_alert(h, now, None)
    assert not should_alert(h, now, now - timedelta(hours=2))
    assert should_alert(h, now, now - timedelta(hours=6))
    assert not should_alert(token_health(r, SAT_10), SAT_10, None)
    assert "expires in" in alert_message(h)
    assert "EXPIRED" in alert_message(token_health(r, SAT_10 + timedelta(days=8)))


def test_reauth_slot_is_next_saturday_10am_et():
    fri = datetime(2026, 10, 30, 22, 0, tzinfo=NY)
    assert next_reauth_slot(fri) == SAT_10
    sat_noon = datetime(2026, 10, 31, 12, 0, tzinfo=NY)
    assert next_reauth_slot(sat_noon) == datetime(2026, 11, 7, 10, 0, tzinfo=NY)
    # Works across the DST change (Nov 1 2026) and from UTC input.
    assert next_reauth_slot(datetime(2026, 11, 2, 15, 0, tzinfo=UTC)).hour == 10


def test_token_from_weekly_slot_survives_but_wednesday_login_does_not():
    # Saturday login covers the whole next trading week.
    assert survives_trading_week(rec(SAT_10), SAT_10 + timedelta(hours=1))
    # A Thursday-morning login expires next Thursday: mid-week death.
    thu = datetime(2026, 10, 29, 9, 0, tzinfo=NY)
    assert not survives_trading_week(rec(thu), datetime(2026, 11, 1, 12, 0, tzinfo=NY))


def test_file_store_roundtrip_and_permissions(tmp_path):
    store = FileTokenStore(tmp_path / "meta" / "token_meta.json")
    assert store.load() is None
    store.save(rec(SAT_10))
    assert store.load().refresh_expires_at == SAT_10.astimezone(UTC) + timedelta(days=7)
    assert (store.path.stat().st_mode & 0o777) == 0o600


def test_credentials_never_leak_in_repr():
    c = credentials_from_env({"SCHWAB_APP_KEY": "abcd1234WXYZ", "SCHWAB_APP_SECRET": "s3cr3t!!"})
    assert "s3cr3t" not in repr(c) and "abcd1234" not in str(c) and c.app_key.endswith("WXYZ")
    with pytest.raises(MissingCredentials):
        credentials_from_env({})
