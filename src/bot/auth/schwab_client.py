"""Construct the Schwabdev client from environment/Secrets Manager values.

The only place that touches the Schwab app key/secret. Never logs them.
schwabdev is imported lazily so the rest of the code (and CI) runs without
the `broker` dependency group installed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class MissingCredentials(RuntimeError):
    pass


@dataclass(frozen=True)
class SchwabCredentials:
    app_key: str
    app_secret: str
    callback_url: str

    def __repr__(self) -> str:  # never leak secrets via logs/tracebacks
        key = f"***{self.app_key[-4:]}"
        return f"SchwabCredentials(app_key={key}, callback_url={self.callback_url})"

    __str__ = __repr__


def credentials_from_env(env: Mapping[str, str] | None = None) -> SchwabCredentials:
    env = os.environ if env is None else env
    missing = [k for k in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET") if not env.get(k)]
    if missing:
        raise MissingCredentials(
            f"Missing {', '.join(missing)}. Set them in .env once the Schwab app is approved."
        )
    return SchwabCredentials(
        app_key=env["SCHWAB_APP_KEY"],
        app_secret=env["SCHWAB_APP_SECRET"],
        callback_url=env.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1"),
    )


def make_client(creds: SchwabCredentials, tokens_file: str = "tokens.json") -> Any:
    """Return a schwabdev.Client. First run opens the browser login flow.

    tokens_file is gitignored; in AWS it will be swapped for a Secrets Manager-backed store.
    """
    try:
        import schwabdev  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("Install the broker group: `uv sync --group broker`") from e
    return schwabdev.Client(
        creds.app_key, creds.app_secret, creds.callback_url, tokens_file=tokens_file
    )
