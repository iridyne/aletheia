"""CoinGlass collector — aggregated open-interest and liquidation history.

Filled in as the Round 2 second batch ([[aletheia-coinglass-key-loading]]).
Reuses the canonical derivatives storage tables (funding_rate_snapshots /
open_interest_snapshots / liquidation_events) so consumers don't need new code.

KEY LOADING (per spec CG-S01 / CG-C01):
  get_secret("coinglass.api_key") → keyring / encrypted secrets.enc (preferred)
  os.environ.get("COINGLASS_API_KEY") → fallback for users who put the key in
    the project's .env (which aletheia/config/loader.py loads on startup).
  None → collect() returns immediately WITHOUT constructing any httpx request
    so the unauthenticated CoinGlass endpoint can't be hit by accident and the
    key value is never observable.

The key value is NEVER logged or returned — only the redact() string lands in
doctor/diagnostics output (spec CG-C02).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("aletheia.data.collectors.coinglass")

_COINGLASS_BASE = "https://open-api-v3.coinglass.com/api"


def _get_coinglass_key() -> str | None:
    """Returns the CoinGlass API key from keyring (preferred) or os.environ.

    Order matters: keyring first lets the user manage the key via Settings UI;
    env fallback handles the existing .env-stored key without forcing migration.
    """
    from aletheia.config.secrets import get_secret
    keyring_value = get_secret("coinglass.api_key")
    if keyring_value:
        return keyring_value
    env_value = os.environ.get("COINGLASS_API_KEY")
    return env_value if env_value else None


class CoinGlassCollector:
    domain = "coinglass"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Pull aggregated OI/liquidations for `symbols`. No-key early return.

        Returns {rows, errors}. rows is empty when the key is absent — the HTTP
        request is NEVER constructed in that case (CG-C01 no-key safety invariant).
        """
        # SPEC CG-C01: key check must lexically precede any httpx_client(...) call.
        key = _get_coinglass_key()
        if not key:
            return {"rows": [], "errors": ["coinglass_key_missing"]}

        # Lazy import — keep no-key path free of httpx dependency check.
        from aletheia.net import httpx_client

        rows: list[dict] = []
        errors: list[str] = []
        try:
            async with httpx_client(timeout=15) as client:
                # Endpoint set is intentionally minimal in this first batch — the
                # downstream "what we actually use" decision happens after the key
                # is live and we can inspect real responses.
                # Reference: https://docs.coinglass.com/
                # TODO(round-2-step-5-followup): wire specific endpoints (OI aggr
                # by exchange, liquidation map history) once endpoint shapes are
                # locked in against the live key.
                _ = client  # placeholder — real calls land in the follow-up commit
        except Exception as e:
            errors.append(f"coinglass: {e}")
            logger.debug("CoinGlass fetch failed (non-fatal): %s", e)

        return {"rows": rows, "errors": errors}
