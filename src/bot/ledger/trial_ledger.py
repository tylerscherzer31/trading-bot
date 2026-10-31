"""Append-only trial ledger (SPEC §6, CLAUDE.md rule 7).

Logs every proposal, backtest run, and holdout touch. Each entry carries the
hash of the previous one, so edits or deletions are detectable. Local JSONL
backend now; DynamoDB + S3 export backend arrives with the infra.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class EntryKind(StrEnum):
    PROPOSAL = "proposal"
    BACKTEST = "backtest"
    HOLDOUT_TOUCH = "holdout_touch"
    DEPLOY = "deploy"


GENESIS = "0" * 64


@dataclass
class TrialLedger:
    path: Path

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, kind: EntryKind, payload: dict[str, Any]) -> dict[str, Any]:
        entries = self._entries()
        prev = entries[-1]["hash"] if entries else GENESIS
        body = {
            "seq": len(entries),
            "ts": datetime.now(UTC).isoformat(),
            "kind": str(kind),
            "payload": payload,
            "prev": prev,
        }
        body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(body, sort_keys=True) + "\n")
        return body

    def verify(self) -> bool:
        prev = GENESIS
        for i, e in enumerate(self._entries()):
            stored = e.get("hash")
            body = {k: v for k, v in e.items() if k != "hash"}
            recomputed = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            if e["seq"] != i or e["prev"] != prev or stored != recomputed:
                return False
            prev = stored
        return True

    def trial_count(self) -> int:
        """Total trials for Deflated Sharpe: proposals AND backtests count (SPEC §8)."""
        kinds = {EntryKind.PROPOSAL, EntryKind.BACKTEST}
        return sum(1 for e in self._entries() if e["kind"] in kinds)

    def holdout_touches(self) -> int:
        return sum(1 for e in self._entries() if e["kind"] == EntryKind.HOLDOUT_TOUCH)
