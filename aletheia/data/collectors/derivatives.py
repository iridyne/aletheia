"""Derivatives collector — funding rate, open interest, liquidations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import (
    FundingRateSnapshot,
    LiquidationEvent,
    OpenInterestSnapshot,
)

logger = logging.getLogger("aletheia.data.collectors.derivatives")

# Binance Futures globalLongShortAccountRatio — public, no API key.
# Scope: USDT-margined linear perpetuals only (BTC/USDT → BTCUSDT). Inverse perps
# would need a different symbol form; deliberately out of scope this batch.
# Fed into OpenInterestSnapshot.long_short_ratio at write time; normalizer's
# compute_leverage_state already consumes long_short_ratio into crowding_score.
_BINANCE_LSR_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"

# Window for liquidation event collection. 4h gives enough events even during low-activity
# periods; events are deduplicated by id (event_time+symbol+side+price hash) on insert.
_LIQUIDATION_WINDOW_HOURS = 4


class _CCXTLiquidationsUnsupported(Exception):
    """Raised when the exchange's CCXT driver lacks fetch_liquidations support."""


class DerivativesCollector:
    domain = "derivatives"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.config.loader import load_config

        provider = get_market_data_provider()
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        liquidations: list[LiquidationEvent] = []
        errors: list[str] = []
        primary_name = provider._primary_name

        cfg = load_config()
        # LSR is a single-exchange (Binance) signal regardless of primary. Pull once
        # for all symbols up-front so a failure here cannot mask the OI write — per
        # spec MS-C01: LSR failure must NOT prevent OpenInterestSnapshot persistence.
        lsr_map: dict[str, float] = {}
        try:
            if cfg.market_data.enable_long_short_ratio:
                lsr_map = await _fetch_lsr_binance_batch(symbols)
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

                # Funding rate
                try:
                    fr = await exchange.fetch_funding_rate(perp_sym)
                    snap = _parse_funding(sym, primary_name, fr)
                    if snap:
                        funding.append(snap)
                except Exception as e:
                    errors.append(f"funding {sym}: {e}")
                    logger.debug("funding fetch failed for %s: %s", sym, e)

                # Open interest (LSR is folded into the same dataclass — no follow-up UPDATE)
                try:
                    raw_oi = await exchange.fetch_open_interest(perp_sym)
                    snap = _parse_oi(sym, primary_name, raw_oi, lsr=lsr_map.get(sym))
                    if snap:
                        oi.append(snap)
                except Exception as e:
                    errors.append(f"oi {sym}: {e}")
                    logger.debug("OI fetch failed for %s: %s", sym, e)

            # Optional history backfill — runs only when local DB is sparse (per
            # spec MS-C05 idempotency). funding_rate_history is well-supported on
            # all four exchanges; open_interest_history is unsupported on Bitget
            # (per spec MS-C06: must not raise on that path).
            history_hours = getattr(cfg.market_data, "derivatives_history_hours", 0)
            if history_hours > 0:
                for sym in symbols:
                    perp_sym = _to_perp_symbol(sym, exchange)
                    if not perp_sym:
                        continue
                    try:
                        await _maybe_backfill_funding_history(
                            exchange, sym, perp_sym, primary_name, history_hours,
                            errors=errors,
                        )
                    except Exception as e:
                        errors.append(f"funding_history {sym}: {e}")
                    try:
                        await _maybe_backfill_oi_history(
                            exchange, sym, perp_sym, primary_name, history_hours,
                            errors=errors,
                        )
                    except Exception as e:
                        errors.append(f"oi_history {sym}: {e}")

            # Liquidations: try primary exchange via CCXT first; fall back to Binance REST.
            try:
                ccxt_liqs = await _fetch_liquidations_ccxt(exchange, primary_name, symbols)
                liquidations.extend(ccxt_liqs)
                logger.info(
                    "liquidations via ccxt/%s: %d events", primary_name, len(ccxt_liqs)
                )
            except _CCXTLiquidationsUnsupported:
                # Binance forceOrders REST endpoint requires API key since 2025.
                # Skip fallback silently — liquidation data has no downstream consumer.
                logger.debug(
                    "liquidations: %s lacks CCXT fetchLiquidations; "
                    "Binance REST fallback disabled (requires auth)",
                    primary_name,
                )
            except Exception as e:
                errors.append(f"liquidations: {e}")
                logger.warning("liquidation fetch failed: %s", e)

        finally:
            await provider.close()

        return {
            "funding": funding,
            "open_interest": oi,
            "liquidations": liquidations,
            "errors": errors,
        }


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


def _parse_oi(
    symbol: str, exchange: str, raw: dict, lsr: float | None = None
) -> OpenInterestSnapshot | None:
    oi_val = raw.get("openInterestAmount") or raw.get("openInterestValue")
    if oi_val is None:
        return None

    oi_usd = float(oi_val)
    # Some exchanges return in coin, some in USD. CCXT unified uses openInterestValue (USD).
    info = raw.get("info", {})
    oi_coin = None
    if "sumOpenInterest" in info:
        oi_coin = float(info["sumOpenInterest"])

    # Compute 1h change pct against previous snapshot
    oi_change_1h_pct = None
    try:
        from aletheia.storage.repositories import latest_open_interest
        prev = latest_open_interest(symbol)
        if prev and prev.get("open_interest_usd"):
            prev_oi = float(prev["open_interest_usd"])
            if prev_oi > 0:
                oi_change_1h_pct = round((oi_usd - prev_oi) / prev_oi * 100, 2)
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
        oi_val = item.get("openInterestAmount") or item.get("openInterestValue")
        ts = item.get("timestamp")
        if oi_val is None or ts is None:
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
            open_interest_usd=float(oi_val),
            open_interest_coin=None,
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


async def _fetch_liquidations_ccxt(
    exchange, exchange_name: str, symbols: list[str]
) -> list[LiquidationEvent]:
    """Fetch recent liquidations from the primary exchange via CCXT unified API.

    Raises _CCXTLiquidationsUnsupported if the exchange driver does not implement
    fetch_liquidations, allowing the caller to fall back to Binance REST.
    """
    if not exchange.has.get("fetchLiquidations"):
        raise _CCXTLiquidationsUnsupported(exchange_name)

    results: list[LiquidationEvent] = []
    now = datetime.now(timezone.utc)
    since_ms = int((now - timedelta(hours=_LIQUIDATION_WINDOW_HOURS)).timestamp() * 1000)

    for sym in symbols[:10]:  # cap to avoid rate limits
        perp_sym = _to_perp_symbol(sym, exchange)
        if not perp_sym:
            continue
        try:
            raw_list = await exchange.fetch_liquidations(perp_sym, since=since_ms, limit=50)
            for item in raw_list:
                price = float(item.get("price") or 0)
                qty = float(item.get("amount") or item.get("quantity") or 0)
                side_raw = (item.get("side") or "").lower()
                # CCXT unified: side is the position side being liquidated
                side = side_raw if side_raw in ("long", "short") else "short"
                ts = item.get("timestamp")
                event_str = (
                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
                    if ts
                    else now.isoformat()
                )
                results.append(LiquidationEvent(
                    symbol=sym,
                    exchange=exchange_name,
                    side=side,
                    quantity_usd=price * qty,
                    price=price,
                    event_time=event_str,
                    observed_at=now.isoformat(),
                ))
        except Exception as e:
            logger.warning("ccxt liquidations fetch failed for %s: %s", sym, e)

    return results
