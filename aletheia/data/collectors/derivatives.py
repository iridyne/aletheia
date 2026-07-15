"""Derivatives collector — funding rate and open interest.

Liquidations are now collected by CoinalyzeCollector
(aletheia/data/collectors/coinalyze.py). The previous CCXT-based liquidation
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

from aletheia.net import httpx_client
from aletheia.data.models import (
    FundingRateSnapshot,
    OpenInterestSnapshot,
)

logger = logging.getLogger("aletheia.data.collectors.derivatives")

# Binance Futures globalLongShortAccountRatio — public, no API key.
# Scope: USDT-margined linear perpetuals only (BTC/USDT → BTCUSDT). Inverse perps
# would need a different symbol form; deliberately out of scope this batch.
# Fed into OpenInterestSnapshot.long_short_ratio at write time; normalizer's
# compute_leverage_state already consumes long_short_ratio into crowding_score.
_BINANCE_LSR_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
# LSR enriches OI but is not allowed to consume the primary venue's live
# Funding/OI budget.  A partial result is honest and remains useful.
_LSR_BUDGET_SEC = 4.0


class DerivativesCollector:
    domain = "derivatives"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.config.loader import load_config

        provider = get_market_data_provider()
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        errors: list[str] = []
        primary_name = provider._primary_name

        cfg = load_config()
        # LSR is a single-exchange (Binance) signal regardless of primary. Pull once
        # for all symbols up-front so a failure here cannot mask the OI write — per
        # spec MS-C01: LSR failure must NOT prevent OpenInterestSnapshot persistence.
        lsr_map: dict[str, float] = {}
        try:
            if cfg.market_data.enable_long_short_ratio:
                lsr_map = await asyncio.wait_for(
                    _fetch_lsr_binance_batch(symbols), timeout=_LSR_BUDGET_SEC
                )
        except asyncio.TimeoutError:
            errors.append("lsr_batch_timeout")
            logger.debug("LSR batch fetch exceeded %.1fs local budget", _LSR_BUDGET_SEC)
        except Exception as e:
            errors.append(f"lsr_batch: {e}")
            logger.debug("LSR batch fetch failed (non-fatal): %s", e)

        try:
            exchange = provider._ensure_primary()
            await exchange.load_markets()

            for sym in symbols:
                perp_sym = _to_perp_symbol(sym, exchange)
                if not perp_sym:
                    continue

                # Funding rate. Capture mark price here so the OI parse can
                # convert a coin/contract amount to USD when the venue omits
                # openInterestValue (e.g. Binance live OI).
                mark_price = None
                try:
                    fr = await exchange.fetch_funding_rate(perp_sym)
                    mark_price = fr.get("markPrice")
                    snap = _parse_funding(sym, primary_name, fr)
                    if snap:
                        funding.append(snap)
                except Exception as e:
                    errors.append(f"funding {sym}: {e}")
                    logger.debug("funding fetch failed for %s: %s", sym, e)

                # Open interest (LSR is folded into the same dataclass — no follow-up UPDATE)
                try:
                    raw_oi = await exchange.fetch_open_interest(perp_sym)
                    snap = _parse_oi(
                        sym, primary_name, raw_oi,
                        lsr=lsr_map.get(sym), mark_price=mark_price,
                    )
                    if snap:
                        oi.append(snap)
                except Exception as e:
                    errors.append(f"oi {sym}: {e}")
                    logger.debug("OI fetch failed for %s: %s", sym, e)

        finally:
            await provider.close()

        return {
            "funding": funding,
            "open_interest": oi,
            "errors": errors,
        }

    async def collect_history(self, symbols: list[str]) -> dict[str, Any]:
        """Best-effort sparse history backfill outside the live Funding/OI path."""
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.config.loader import load_config

        cfg = load_config()
        history_hours = getattr(cfg.market_data, "derivatives_history_hours", 0)
        if history_hours <= 0:
            return {"errors": []}

        provider = get_market_data_provider()
        errors: list[str] = []
        primary_name = provider._primary_name
        try:
            exchange = provider._ensure_primary()
            await exchange.load_markets()
            for sym in symbols:
                perp_sym = _to_perp_symbol(sym, exchange)
                if not perp_sym:
                    continue
                try:
                    await _maybe_backfill_funding_history(
                        exchange, sym, perp_sym, primary_name, history_hours,
                        errors=errors,
                    )
                except Exception as exc:
                    errors.append(f"funding_history {sym}: {exc}")
                try:
                    await _maybe_backfill_oi_history(
                        exchange, sym, perp_sym, primary_name, history_hours,
                        errors=errors,
                    )
                except Exception as exc:
                    errors.append(f"oi_history {sym}: {exc}")
        finally:
            await provider.close()
        return {"errors": errors}


def _to_perp_symbol(spot_symbol: str, exchange) -> str | None:
    """Convert canonical spot BTC/USDT to the perp symbol on this exchange."""
    # CCXT perp: BTC/USDT:USDT
    perp = f"{spot_symbol}:USDT"
    if perp in (exchange.markets or {}):
        return perp
    return None


def _parse_funding(
    symbol: str, exchange: str, raw: dict
) -> FundingRateSnapshot | None:
    rate = raw.get("fundingRate")
    if rate is None:
        return None
    rate = float(rate)
    annualized = rate * 3 * 365  # 8h intervals → annual
    next_time = raw.get("fundingDatetime") or raw.get("nextFundingDatetime")
    mark = raw.get("markPrice")

    return FundingRateSnapshot(
        symbol=symbol,
        exchange=exchange,
        funding_rate=rate,
        funding_rate_annualized=round(annualized, 4),
        next_funding_time=str(next_time) if next_time else None,
        mark_price=float(mark) if mark else None,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


# OI-delta sanity ceiling. A legitimate 1h open-interest move is at most a few
# tens of percent; anything larger is a data artifact — a stored-units
# transition, a venue mismatch that slipped the exchange filter, or a
# thin/relisted market. Drop it to None rather than feed a fake surge to the
# derivatives/event scanners.
_OI_DELTA_SANITY_PCT = 300.0


def extract_oi_usd(
    raw: dict, mark_price: float | None = None
) -> tuple[float, float | None] | None:
    """Return ``(open_interest_usd, open_interest_coin)`` from a CCXT OI dict.

    Prefer the exchange-reported USD notional (CCXT unified ``openInterestValue``,
    e.g. OKX ``oiUsd``); fall back to coin/contract amount × mark price. Returns
    None when neither is available, so callers store None rather than a
    wrong-unit value. Single source of truth for the OI→USD rule shared by the
    collector and the universe feature path.
    """
    oi_amount = raw.get("openInterestAmount")
    oi_value_usd = raw.get("openInterestValue")
    if oi_value_usd is not None:
        oi_usd = float(oi_value_usd)
    elif oi_amount is not None and mark_price:
        oi_usd = float(oi_amount) * float(mark_price)
    else:
        return None

    oi_coin = float(oi_amount) if oi_amount is not None else None
    info = raw.get("info", {})
    if oi_coin is None and "sumOpenInterest" in info:
        oi_coin = float(info["sumOpenInterest"])
    return oi_usd, oi_coin


def _parse_oi(
    symbol: str,
    exchange: str,
    raw: dict,
    lsr: float | None = None,
    mark_price: float | None = None,
) -> OpenInterestSnapshot | None:
    # Prefer the exchange-reported USD notional. Storing the raw amount as USD
    # (the previous behaviour) was wrong by the asset price — ~660× on OKX BTC,
    # where openInterestAmount is a contract count and openInterestValue (the
    # true USD) was silently ignored.
    extracted = extract_oi_usd(raw, mark_price)
    if extracted is None:
        return None  # cannot express in USD — honest None beats a wrong-unit value
    oi_usd, oi_coin = extracted

    # TRUE ~1h change vs a snapshot ~1h ago on THIS venue — not the ~2-min
    # previous tick (data_collectors runs every 2 min, so the old delta almost
    # never cleared the scanner's 1h-calibrated thresholds), and not a
    # foreign-venue row with different units (which fabricated ±100%–2500%
    # deltas). open_interest_near returns None outside its age band, so a
    # collector gap yields "no delta" instead of a bogus multi-hour jump.
    oi_change_1h_pct = None
    try:
        from aletheia.storage.repositories import open_interest_near
        prev = open_interest_near(symbol, exchange)
        if prev and prev.get("open_interest_usd"):
            prev_oi = float(prev["open_interest_usd"])
            if prev_oi > 0:
                delta = round((oi_usd - prev_oi) / prev_oi * 100, 2)
                if abs(delta) <= _OI_DELTA_SANITY_PCT:
                    oi_change_1h_pct = delta
    except Exception:
        pass

    return OpenInterestSnapshot(
        symbol=symbol,
        exchange=exchange,
        open_interest_usd=oi_usd,
        open_interest_coin=oi_coin,
        oi_change_1h_pct=oi_change_1h_pct,
        long_short_ratio=lsr,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


async def _maybe_backfill_funding_history(
    exchange, sym: str, perp_sym: str, exchange_name: str, since_hours: int,
    errors: list[str] | None = None,
) -> int:
    """Backfill funding rate history when the local window is sparse.

    Idempotency (per spec MS-C05): no-op if ≥3 rows already exist within
    since_hours for this symbol. Persisted via insert_funding_rate_snapshot
    one-by-one (each row independent; row volume is bounded by limit=24).
    """
    from aletheia.storage.repositories import _select_all, insert_funding_rate_snapshot
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all(
        "funding_rate_snapshots",
        where="symbol=? AND observed_at>?", params=(sym, cutoff), limit=3,
    )
    if len(existing) >= 3:
        return 0
    try:
        history = await exchange.fetch_funding_rate_history(perp_sym, limit=24)
    except Exception as e:
        if errors is not None:
            errors.append(f"funding_history {sym}: {e}")
        return 0
    n = 0
    for item in history:
        rate = item.get("fundingRate")
        ts = item.get("timestamp")
        if rate is None or ts is None:
            continue
        observed = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
        # Skip rows we already have (string-equal observed_at suffices on this codebase).
        prior = _select_all(
            "funding_rate_snapshots",
            where="symbol=? AND observed_at=?", params=(sym, observed), limit=1,
        )
        if prior:
            continue
        snap = FundingRateSnapshot(
            symbol=sym,
            exchange=exchange_name,
            funding_rate=float(rate),
            funding_rate_annualized=round(float(rate) * 3 * 365, 4),
            next_funding_time=None,
            mark_price=None,
            observed_at=observed,
        )
        insert_funding_rate_snapshot(snap.model_dump())
        n += 1
    return n


async def _maybe_backfill_oi_history(
    exchange, sym: str, perp_sym: str, exchange_name: str, since_hours: int,
    errors: list[str] | None = None,
) -> int:
    """Same shape as funding backfill. Bitget raises NotSupported — caller must
    not re-raise (per spec MS-C06)."""
    from aletheia.storage.repositories import _select_all, insert_open_interest_snapshot
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all(
        "open_interest_snapshots",
        where="symbol=? AND observed_at>?", params=(sym, cutoff), limit=3,
    )
    if len(existing) >= 3:
        return 0
    try:
        history = await exchange.fetch_open_interest_history(perp_sym, "1h", limit=24)
    except Exception as e:
        # Bitget surfaces this as a NotSupported instance — collector contract
        # says swallow and log, not raise. Same for any other transient failure.
        if errors is not None:
            errors.append(f"oi_history {sym}: {type(e).__name__}: {e}")
        logger.warning(
            "OI history not available for %s on %s (%s: %s) — skipping backfill",
            sym, exchange_name, type(e).__name__, e,
        )
        return 0
    n = 0
    for item in history:
        ts = item.get("timestamp")
        oi_value_usd = item.get("openInterestValue")
        oi_amount = item.get("openInterestAmount")
        if ts is None:
            continue
        # Store USD, consistent with the live path. History rows on OKX/Binance
        # carry openInterestValue (USD); if a venue omits it we skip rather than
        # persist a coin/contract amount as USD and mix units within the table.
        if oi_value_usd is None:
            continue
        observed = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
        prior = _select_all(
            "open_interest_snapshots",
            where="symbol=? AND observed_at=?", params=(sym, observed), limit=1,
        )
        if prior:
            continue
        snap = OpenInterestSnapshot(
            symbol=sym,
            exchange=exchange_name,
            open_interest_usd=float(oi_value_usd),
            open_interest_coin=float(oi_amount) if oi_amount is not None else None,
            oi_change_1h_pct=None,
            long_short_ratio=None,
            observed_at=observed,
        )
        insert_open_interest_snapshot(snap.model_dump())
        n += 1
    return n


async def _fetch_lsr_binance_batch(symbols: list[str]) -> dict[str, float]:
    """Fetch latest 1h global long/short account ratio from Binance Futures.

    Returns a dict mapping canonical symbol → ratio. Symbols Binance does not
    recognize are silently omitted. USDT-margined linear perps only.
    """
    results: dict[str, float] = {}
    async with httpx_client(timeout=10) as client:
        for sym in symbols[:20]:  # cap to keep tick budget tight
            binance_sym = sym.replace("/", "")
            try:
                resp = await client.get(
                    _BINANCE_LSR_URL,
                    params={"symbol": binance_sym, "period": "1h", "limit": 1},
                )
                if resp.status_code != 200:
                    continue
                items = resp.json()
                if items and isinstance(items, list):
                    ratio = items[0].get("longShortRatio")
                    if ratio is not None:
                        results[sym] = float(ratio)
            except Exception as e:
                logger.debug("LSR fetch failed for %s: %s", sym, e)
    return results
