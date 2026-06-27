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

_COINGLASS_BASE = "https://open-api-v4.coinglass.com/api"
# Tier-detection endpoint. Picked because it has the lowest signal cost — even
# `supported-coins` returns "Upgrade plan" on the free tier as of 2026-06-27, so
# any 200-with-"Upgrade plan"-body response definitively flags a paywalled key.
_COINGLASS_TIER_PROBE = "/futures/supported-coins"
# The exact body marker CoinGlass returns when a paid endpoint is hit by a free key.
# Documented response shape: {"code":"401","msg":"Upgrade plan"}.
_PAYWALL_MARKER = "Upgrade plan"


def _get_coinglass_key() -> str | None:
    """Returns the CoinGlass API key from keyring (preferred) or os.environ.

    Order matters: keyring first lets the user manage the key via Settings UI;
    env fallback handles the existing .env-stored key without forcing migration.

    Two env var names are accepted: the canonical COINGLASS_API_KEY and the
    shorter COINGLASS_API (which the operator picked for their .env). Keyring
    always wins regardless of which env var is set.
    """
    from aletheia.config.secrets import get_secret
    keyring_value = get_secret("coinglass.api_key")
    if keyring_value:
        return keyring_value
    env_value = os.environ.get("COINGLASS_API_KEY") or os.environ.get("COINGLASS_API")
    return env_value if env_value else None


class CoinGlassCollector:
    domain = "coinglass"

    # Class-level tier-status cache. None = not yet probed; "free_paywalled"
    # = confirmed all endpoints are paid; "paid" = at least the probe endpoint
    # returned real data. Lives across collect() calls within one process.
    # (Per [[aletheia-coinglass-free-tier-paywalled]]: as of 2026-06-27 the
    # free tier paywalls EVERY non-trivial endpoint, so a single probe is
    # enough to decide whether collect() can do useful work at all.)
    _tier_status: str | None = None

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Pull aggregated OI/liquidations for `symbols`. No-key early return.

        Returns {rows, errors}. rows is empty when:
          (a) the key is absent (CG-C01 no-key safety, lexical AST-asserted), OR
          (b) the key is present but resolves to a free CoinGlass tier (every
              paid endpoint returns "Upgrade plan"). In that case we record
              `coinglass_free_tier_paywalled` once and never re-probe in this
              process — see [[aletheia-coinglass-free-tier-paywalled]].
        """
        # SPEC CG-C01: key check must lexically precede any httpx_client(...) call.
        key = _get_coinglass_key()
        if not key:
            return {"rows": [], "errors": ["coinglass_key_missing"]}

        # Tier gate — runs at most once per process. If the cached status is
        # "free_paywalled", we degrade gracefully without burning another
        # request: every endpoint we would call is paywalled anyway.
        if CoinGlassCollector._tier_status == "free_paywalled":
            return {"rows": [], "errors": ["coinglass_free_tier_paywalled"]}

        # Lazy import — keep no-key / cached-paywall paths free of httpx import.
        from aletheia.net import httpx_client

        rows: list[dict] = []
        errors: list[str] = []
        try:
            async with httpx_client(timeout=15) as client:
                # First-call tier probe.
                if CoinGlassCollector._tier_status is None:
                    is_paywalled = await _probe_tier_paywalled(client, key)
                    CoinGlassCollector._tier_status = (
                        "free_paywalled" if is_paywalled else "paid"
                    )
                    if is_paywalled:
                        logger.info(
                            "CoinGlass free tier detected — every paid endpoint "
                            "returns 'Upgrade plan'. Collector will skip until "
                            "the key is upgraded. See "
                            "[[aletheia-coinglass-free-tier-paywalled]]."
                        )
                        errors.append("coinglass_free_tier_paywalled")
                        return {"rows": rows, "errors": errors}

                # Paid tier — TODO(round-2-step-5-followup): wire the actual
                # endpoints (open-interest/aggregated-history, liquidation/
                # aggregated-history, funding-rate/history) here. Endpoint
                # shapes have been probed and the auth header is `CG-API-KEY`,
                # but writing the real fetches requires a paid key on the
                # operator's account so the responses can be inspected for
                # field naming. Until then we no-op even on paid tier.
                _ = client
        except Exception as e:
            errors.append(f"coinglass: {e}")
            logger.debug("CoinGlass fetch failed (non-fatal): %s", e)

        return {"rows": rows, "errors": errors}


async def _probe_tier_paywalled(client, key: str) -> bool:
    """Probe a known-paywalled-on-free endpoint to detect tier.

    Returns True when CoinGlass responds with the documented "Upgrade plan"
    marker. ANY other response (success, network error, unexpected JSON) is
    treated as "tier OK / unknown" — i.e. we lean toward NOT paywall-flagging
    on ambiguous responses so the next collect() attempt can still try the
    real endpoints.
    """
    try:
        resp = await client.get(
            _COINGLASS_BASE + _COINGLASS_TIER_PROBE,
            headers={"CG-API-KEY": key, "accept": "application/json"},
        )
        # CoinGlass returns 200 with a JSON body carrying code="401" + msg="Upgrade plan"
        # on paywalled endpoints — a real HTTP 401 is rare. We grep the body for the marker.
        body = resp.text or ""
        return _PAYWALL_MARKER in body
    except Exception:
        return False
