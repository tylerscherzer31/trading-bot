"""Parquet sink, partitioned symbol/date (CLAUDE.md conventions).

LocalParquetSink writes under a root directory with the same keys S3 will use,
so moving to S3 is a root swap (s3:// via pyarrow/fsspec) rather than a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from bot.data.recorder_keys import s3_key


class Sink(Protocol):
    def write(self, df: pd.DataFrame, kind: str, symbol: str, ts: datetime) -> str | None: ...


@dataclass
class LocalParquetSink:
    root: Path
    written: list[str] = field(default_factory=list)

    def write(self, df: pd.DataFrame, kind: str, symbol: str, ts: datetime) -> str | None:
        if df is None or df.empty:
            return None
        key = s3_key(kind, symbol, ts)
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        self.written.append(key)
        return key
