"""Least-privilege environment for updater child processes."""

from __future__ import annotations

import os
from collections.abc import Mapping


# These prefixes cover provider, messaging, market-data, and updater credentials
# consumed by the runtime.  Update children only need local paths and safe flags.
SECRET_PREFIXES = (
    "BITGET_",
    "OKX_",
    "BINANCE_",
    "OPENAI_",
    "ANTHROPIC_",
    "LLM_",
    "TELEGRAM_BOT_TOKEN",
    "COINALYZE_",
    "FRED_",
    "ALETHEIA_UPDATE_",
    "ALETHEIA_OPERATOR_SERVICE_TOKEN",
)


def safe_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment without secret-bearing variables."""
    values = os.environ if source is None else source
    return {
        key: value
        for key, value in values.items()
        if not key.startswith(SECRET_PREFIXES)
    }
