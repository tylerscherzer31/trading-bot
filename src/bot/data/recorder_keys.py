"""Storage key layout shared by the recorder and sinks."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

from bot import NY


def s3_key(kind: str, symbol: str, ts: datetime) -> str:
    """kind/symbol=X/date=YYYY-MM-DD/HHMMSS.parquet, date and time in New York."""
    local = ts.astimezone(NY)
    clean = symbol.lstrip("$")
    return str(
        PurePosixPath(kind, f"symbol={clean}", f"date={local.date().isoformat()}",
                      f"{local:%H%M%S}.parquet")
    )
