"""Derivatives collector — funding rate and open interest.

Liquidations are now collected by CoinalyzeCollector
(extensions/coinalyze/collector.py). The previous CCXT-based liquidation
path was removed because:
  - Most exchanges don't implement CCXT fetchLiquidations.
  - Binance forceOrders REST requires API key authentication since 2025.
  - The collected data had no downstream consumer (confirmed by inline comment).
See GAP-DU-C02 / data_utilization.md §4 for the full rationale.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from aletheia.data.models import (
    FundingRateSnapshot,
    OpenInterestSnapshot,
)
from aletheia.data import derivatives_facts

logger = logging.getLogger("aletheia.data.collectors.derivatives")

_positioning_reader = None


def configure_positioning_reader(reader) -> None:
    global _positioning_reader
    _positioning_reader = reader


async def collect_positioning(symbols: list[str], family: str) -> dict[str, dict]:
    if _positioning_reader is None:
        return {}
    return await _positioning_reader(symbols, family)

class DerivativesCollector:
    domain = "derivatives"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.config.loader import load_config

        provider = get_market_data_provider(native_ccxt=True)
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        errors: list[str] = []

        cfg = load_config()
        # LSR is a single-exchange (Binance) signal regardless of primary. Pull once
        # for all symbols up-front so a failure here cannot mask the OI write — per
        # spec MS-C01: LSR failure must NOT prevent OpenInterestSnapshot persistence.
        lsr_map: dict[str, dict] = {}
        top_position_map: dict[str, dict] = {}
        try:
            if cfg.market_data.enable_long_short_ratio:
                positioning_results = await asyncio.gather(
                    collect_positioning(symbols, "global_account_ratio"),
                    collect_positioning(symbols, "top_position_ratio"),
                    return_exceptions=True,
                )
                for label, result in zip(
                    ("global_account_ratio", "top_position_ratio"),
                    positioning_results,
                    strict=True,
                ):
                    if isinstance(result, BaseException):
                        errors.append(f"{label}_batch:{type(result).__name__}")
                    elif label == "global_account_ratio":
                        lsr_map = result
                    else:
                        top_position_map = result
                    if not isinstance(result, BaseException):
                        for sym in dict.fromkeys(symbols):
                            if sym not in result:
                                errors.append(f"{label}_missing {sym}")
        except Exception as e:
            errors.append(f"lsr_batch: {e}")
            logger.debug("LSR batch fetch failed (non-fatal): %s", e)

        try:
            primary_name, batched = await provider.prepare_live_derivatives()
            # Registry-wide targets are affordable only with batch acquisition.
            target = (_perp_target_set(primary_name, symbols) if batched
                      else list(dict.fromkeys(s for s in (symbols or []) if s)))
            result = await provider.get_live_derivatives(
                target, batched=batched, lsr_map=lsr_map,
                top_position_map=top_position_map,
            )
            funding = result["funding"]
            oi = result["open_interest"]
            for snapshot in oi:
                current = snapshot.model_dump()
                fact = derivatives_facts.project_snapshot(current, "open_interest")[0]
                delta = derivatives_facts._open_interest_change(
                    current, fact, include_comparison=False,
                )["value"]
                snapshot.oi_change_1h_pct = delta * 100 if delta is not None else None
            errors.extend(result["errors"])

        finally:
            await provider.close()

        return {
            "funding": funding,
            "open_interest": oi,
            "errors": errors,
        }

    async def collect_comparison(self, cfg) -> dict[str, Any]:
        """Keep comparison workset/lifecycle separate from primary snapshot writes."""
        from aletheia.data.ccxt_provider import get_market_data_provider

        workset = comparison_workset(cfg)
        acquired = datetime.now(timezone.utc)
        result = dict(funding=[], open_interest=[], prices=[], errors=[],
                      coverage={"acquired_at": acquired.isoformat(), "venues": {}})

        async def collect_venue(venue, symbols):
            provider = get_market_data_provider(
                exchange=venue, native_ccxt=True, fallback_exchange=venue,
            )
            try:
                part = await provider.get_derivatives_comparison(
                    symbols, owns_primary=venue == cfg.market_data.ccxt_exchange,
                    acquired=acquired,
                )
                for family in ("funding", "open_interest", "prices", "errors"):
                    result[family].extend(part[family])
                result["coverage"]["venues"][venue] = part["coverage"]
            finally:
                await provider.close()

        await asyncio.gather(*(collect_venue(venue, symbols) for venue, symbols in workset.items()))
        return result

    async def collect_history(self, symbols: list[str]) -> dict[str, Any]:
        """Best-effort sparse history backfill outside the live Funding/OI path."""
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.config.loader import load_config

        cfg = load_config()
        history_hours = getattr(cfg.market_data, "derivatives_history_hours", 0)
        if history_hours <= 0:
            return {"errors": []}

        provider = get_market_data_provider(native_ccxt=True)
        errors: list[str] = []
        funding_count = 0
        oi_count = 0
        already_current = 0
        empty: list[str] = []
        try:
            primary_name, supported = await provider.prepare_derivatives_history(symbols)
            for sym in dict.fromkeys(symbols):
                if sym not in supported:
                    errors.append(f"history_unsupported {sym}")
                    continue
                try:
                    inserted, current = await _maybe_backfill_funding_history(
                        provider, sym, primary_name, history_hours,
                        errors=errors,
                    )
                    funding_count += inserted
                    already_current += current
                    if not inserted and not current:
                        empty.append(f"funding_history_empty {sym}")
                except Exception as exc:
                    errors.append(f"funding_history {sym}: {exc}")
                try:
                    inserted, current = await _maybe_backfill_oi_history(
                        provider, sym, primary_name, history_hours,
                        errors=errors,
                    )
                    oi_count += inserted
                    already_current += current
                    if not inserted and not current:
                        empty.append(f"oi_history_empty {sym}")
                except Exception as exc:
                    errors.append(f"oi_history {sym}: {exc}")
        finally:
            await provider.close()
        if funding_count or oi_count or already_current:
            errors.extend(empty)
        return {
            "funding": funding_count,
            "open_interest": oi_count,
            "already_current": already_current,
            "errors": errors,
        }


def comparison_workset(cfg) -> dict[str, list[str]]:
    """Registry and configured/open-position instruments, never Candidate ranking."""
    from aletheia.storage import repositories as repo
    from aletheia.trading.instruments import observed_instruments_from_config
    configured = observed_instruments_from_config(cfg)
    required = {item.symbol for item in configured if item.market_type == 'perp'}
    required.update(row['symbol'] for row in repo.list_all_open_positions() if row.get('market_type') in {'perp', 'usdt_futures'})
    venues = list(dict.fromkeys(cfg.market_data.derivatives_comparison_venues))
    for venue in dict.fromkeys([cfg.market_data.ccxt_exchange, *venues]):
        required.update(row['symbol'] for row in repo.list_universe_assets(venue=venue, active_only=True, limit=None)
            if row.get('market_type') == 'perp')
    return {venue: sorted(required) for venue in venues}


def _perp_target_set(venue: str, extra_symbols: list[str] | None) -> list[str]:
    """Canonical perp symbols to enrich on ``venue``: every active registry perp
    plus any explicitly-passed symbol (open positions we must keep funding for
    even if they rotated out of the universe), de-duplicated in insertion order.

    Covering the whole perp universe — not just the active universe members — is
    what gives the feature/scanner path OI + oi_delta for every candidate.
    """
    from aletheia.storage import repositories as repo

    seen: dict[str, None] = {}
    try:
        for asset in repo.list_universe_assets(venue=venue, active_only=True, limit=None):
            if (asset.get("market_type") or "") == "perp" and asset.get("symbol"):
                seen.setdefault(str(asset["symbol"]), None)
    except Exception as e:  # noqa: BLE001 — registry read is best-effort
        logger.debug("registry perp lookup failed (%s); using passed symbols only", e)
    for s in extra_symbols or []:
        if s:
            seen.setdefault(str(s), None)
    return list(seen)


def _insert_historical_funding_snapshot(snapshot: FundingRateSnapshot) -> int:
    """Store an observed historical rate; do not invent a subsequent settlement."""
    from aletheia.storage.repositories import _select_all, insert_funding_rate_snapshot, LEGACY_DERIVATIVES_WHERE

    observed_at = snapshot.observed_at
    prior = _select_all(
        "funding_rate_snapshots",
        where=f"symbol=? AND exchange=? AND observed_at=? AND next_funding_time=? AND {LEGACY_DERIVATIVES_WHERE}",
        params=(snapshot.symbol, snapshot.exchange, observed_at, observed_at), limit=1,
    )
    if prior:
        return 0
    snapshot.next_funding_time = observed_at
    insert_funding_rate_snapshot(snapshot.model_dump())
    return 1


async def _maybe_backfill_funding_history(
    provider, sym: str, exchange_name: str, since_hours: int,
    errors: list[str] | None = None,
) -> tuple[int, bool]:
    """Core sparse-window policy and canonical history writer."""
    from aletheia.storage.repositories import _select_all, LEGACY_DERIVATIVES_WHERE
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all(
        "funding_rate_snapshots",
        where=f"symbol=? AND exchange=? AND observed_at>? AND {LEGACY_DERIVATIVES_WHERE}",
        params=(sym, exchange_name, cutoff), limit=3,
    )
    if len(existing) >= 3:
        return 0, True
    snapshots, acquisition_errors = await provider.get_funding_history(sym)
    if any(snap.symbol != sym or snap.exchange != exchange_name for snap in snapshots):
        raise ValueError("funding_history_identity_mismatch")
    if errors is not None:
        errors.extend(code for code in acquisition_errors if code not in errors)
    return sum(_insert_historical_funding_snapshot(snap) for snap in snapshots), False


async def _maybe_backfill_oi_history(
    provider, sym: str, exchange_name: str, since_hours: int,
    errors: list[str] | None = None,
) -> tuple[int, bool]:
    """Core sparse-window policy and exact-venue deduplicated history writer."""
    from aletheia.storage.repositories import _select_all, insert_open_interest_snapshot, LEGACY_DERIVATIVES_WHERE
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all(
        "open_interest_snapshots",
        where=f"symbol=? AND exchange=? AND observed_at>? AND {LEGACY_DERIVATIVES_WHERE}",
        params=(sym, exchange_name, cutoff), limit=3,
    )
    if len(existing) >= 3:
        return 0, True
    snapshots, acquisition_errors = await provider.get_open_interest_history(sym)
    if any(snap.symbol != sym or snap.exchange != exchange_name for snap in snapshots):
        raise ValueError("oi_history_identity_mismatch")
    if errors is not None:
        errors.extend(code for code in acquisition_errors if code not in errors)
    n = 0
    for snap in snapshots:
        prior = _select_all(
            "open_interest_snapshots",
            where=f"symbol=? AND exchange=? AND observed_at=? AND {LEGACY_DERIVATIVES_WHERE}",
            params=(sym, exchange_name, snap.observed_at), limit=1,
        )
        if prior:
            continue
        insert_open_interest_snapshot(snap.model_dump())
        n += 1
    return n, False
