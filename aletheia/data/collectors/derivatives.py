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
import math
from datetime import datetime, timezone, timedelta
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import (
    FundingRateSnapshot,
    OpenInterestSnapshot,
)
from aletheia.data.freshness import MAX_FUTURE_SKEW

logger = logging.getLogger("aletheia.data.collectors.derivatives")

# Binance Futures globalLongShortAccountRatio — public, no API key.
# Scope: USDT-margined linear perpetuals only (BTC/USDT → BTCUSDT). Inverse perps
# would need a different symbol form; deliberately out of scope this batch.
# Fed into OpenInterestSnapshot.long_short_ratio at write time; normalizer's
# compute_leverage_state already consumes long_short_ratio into crowding_score.
_BINANCE_LSR_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
_BINANCE_TOP_POSITION_RATIO_URL = (
    "https://fapi.binance.com/futures/data/topLongShortPositionRatio"
)
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
        top_position_map: dict[str, float] = {}
        try:
            if cfg.market_data.enable_long_short_ratio:
                positioning_results = await asyncio.gather(
                    _fetch_binance_ratio_batch(symbols, _BINANCE_LSR_URL),
                    _fetch_binance_ratio_batch(symbols, _BINANCE_TOP_POSITION_RATIO_URL),
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
            exchange = provider._ensure_primary()
            await exchange.load_markets()

            # Batch when the venue supports it (OKX: 2 calls cover every perp in
            # <1s) — this is what lets the feature/scanner path see OI for the
            # WHOLE perp universe, not just the ~13 already-active members.
            has = getattr(exchange, "has", {}) or {}
            batched = bool(has.get("fetchOpenInterests")) and bool(
                has.get("fetchFundingRates")
            )
            # Full-coverage target on batch venues = every active registry perp
            # plus any passed symbol (open positions). On per-symbol venues
            # (Bitget/bybit) we keep only the passed set so a 277-symbol
            # sequential loop cannot blow the collector's time budget — the
            # cross-venue read fallback in features supplements those.
            if batched:
                target = _perp_target_set(primary_name, symbols)
            else:
                target = list(dict.fromkeys(s for s in (symbols or []) if s))

            funding_map: dict = {}
            oi_map: dict = {}
            if batched:
                try:
                    funding_map = await exchange.fetch_funding_rates(
                        params={"instType": "SWAP"}
                    )
                except Exception as e:
                    errors.append(f"funding_batch: {e}")
                    logger.debug("batch funding fetch failed: %s", e)
                try:
                    oi_map = await exchange.fetch_open_interests(
                        params={"instType": "SWAP"}
                    )
                except Exception as e:
                    errors.append(f"oi_batch: {e}")
                    logger.debug("batch OI fetch failed: %s", e)

            for sym in target:
                perp_sym = _to_perp_symbol(sym, exchange)
                if not perp_sym:
                    continue

                fr = None
                raw_oi = None
                if batched:
                    fr = funding_map.get(perp_sym)
                    raw_oi = oi_map.get(perp_sym)
                else:
                    try:
                        fr = await exchange.fetch_funding_rate(perp_sym)
                    except Exception as e:
                        errors.append(f"funding {sym}: {e}")
                        logger.debug("funding fetch failed for %s: %s", sym, e)
                    try:
                        raw_oi = await exchange.fetch_open_interest(perp_sym)
                    except Exception as e:
                        errors.append(f"oi {sym}: {e}")
                        logger.debug("OI fetch failed for %s: %s", sym, e)

                # Capture mark price from funding so the OI parse can convert a
                # coin/contract amount to USD when the venue omits
                # openInterestValue (e.g. Binance/Bitget live OI).
                mark_price = fr.get("markPrice") if fr else None
                if fr:
                    snap = _parse_funding(sym, primary_name, fr)
                    if snap:
                        funding.append(snap)
                        if snap.source_time_status != "provided":
                            errors.append(
                                f"funding_source_time_{snap.source_time_status} {sym}"
                            )
                    else:
                        errors.append(f"funding_invalid {sym}")
                # LSR is folded into the same OI dataclass — no follow-up UPDATE.
                if raw_oi:
                    snap = _parse_oi(
                        sym, primary_name, raw_oi,
                        lsr=lsr_map.get(sym),
                        top_position_ratio=top_position_map.get(sym),
                        mark_price=mark_price,
                    )
                    if snap:
                        oi.append(snap)
                        if snap.source_time_status != "provided":
                            errors.append(
                                f"open_interest_source_time_{snap.source_time_status} {sym}"
                            )
                    else:
                        errors.append(f"open_interest_invalid {sym}")

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
        funding_count = 0
        oi_count = 0
        already_current = 0
        empty: list[str] = []
        primary_name = provider._primary_name
        try:
            exchange = provider._ensure_primary()
            await exchange.load_markets()
            for sym in dict.fromkeys(symbols):
                perp_sym = _to_perp_symbol(sym, exchange)
                if not perp_sym:
                    errors.append(f"history_unsupported {sym}")
                    continue
                try:
                    inserted, current = await _maybe_backfill_funding_history(
                        exchange, sym, perp_sym, primary_name, history_hours,
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
                        exchange, sym, perp_sym, primary_name, history_hours,
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
    rate = _finite_float(raw.get("fundingRate"))
    if rate is None:
        return None
    annualized = rate * 3 * 365  # 8h intervals → annual
    if not math.isfinite(annualized):
        return None
    next_time = raw.get("fundingDatetime") or raw.get("nextFundingDatetime")
    raw_mark = raw.get("markPrice")
    mark = _finite_float(raw_mark) if raw_mark is not None else None
    if raw_mark is not None and (mark is None or mark <= 0):
        return None
    acquired_at = datetime.now(timezone.utc)
    source_observed_at, source_time_status = provider_source_time(raw, acquired_at)

    try:
        return FundingRateSnapshot(
            symbol=symbol,
            exchange=exchange,
            funding_rate=rate,
            funding_rate_annualized=round(annualized, 4),
            next_funding_time=str(next_time) if next_time else None,
            mark_price=mark,
            source_observed_at=source_observed_at,
            acquired_at=acquired_at.isoformat(),
            source_time_status=source_time_status,
            observed_at=source_observed_at or acquired_at.isoformat(),
        )
    except ValueError:
        return None


# OI-delta sanity ceiling. A legitimate 1h open-interest move is at most a few
# tens of percent; anything larger is a data artifact — a stored-units
# transition, a venue mismatch that slipped the exchange filter, or a
# thin/relisted market. Drop it to None rather than feed a fake surge to the
# derivatives/event scanners.
_OI_DELTA_SANITY_PCT = 300.0


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def provider_source_time(
    raw: dict,
    acquired_at: datetime,
) -> tuple[str | None, str]:
    """Normalize one CCXT source timestamp without inventing source-now."""
    raw_timestamp = raw.get("timestamp")
    raw_datetime = raw.get("datetime")
    try:
        if raw_timestamp is not None:
            if isinstance(raw_timestamp, bool):
                return None, "invalid"
            timestamp = float(raw_timestamp)
            if not math.isfinite(timestamp):
                return None, "invalid"
            parsed = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        elif raw_datetime:
            parsed = datetime.fromisoformat(str(raw_datetime).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        else:
            return None, "missing"
    except (OSError, OverflowError, TypeError, ValueError):
        return None, "invalid"

    acquired_at = acquired_at.astimezone(timezone.utc)
    if parsed > acquired_at + MAX_FUTURE_SKEW:
        return parsed.isoformat(), "future"
    return parsed.isoformat(), "provided"


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
    raw_oi_amount = raw.get("openInterestAmount")
    raw_oi_value_usd = raw.get("openInterestValue")
    oi_amount = _finite_float(raw_oi_amount)
    if raw_oi_amount is not None and (oi_amount is None or oi_amount < 0):
        return None

    if raw_oi_value_usd is not None:
        oi_usd = _finite_float(raw_oi_value_usd)
    elif oi_amount is not None:
        mark = _finite_float(mark_price)
        if mark is None or mark <= 0:
            return None
        oi_usd = _finite_float(oi_amount * mark)
    else:
        return None
    if oi_usd is None or oi_usd < 0:
        return None

    oi_coin = oi_amount
    info = raw.get("info", {})
    if oi_coin is None and "sumOpenInterest" in info:
        oi_coin = _finite_float(info["sumOpenInterest"])
        if oi_coin is None or oi_coin < 0:
            return None
    return oi_usd, oi_coin


def _parse_oi(
    symbol: str,
    exchange: str,
    raw: dict,
    lsr: float | None = None,
    top_position_ratio: float | None = None,
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

    # Binance positioning ratios are optional enrichment.  Invalid enrichment
    # must degrade to unavailable, never discard an otherwise valid venue OI
    # snapshot (MS-C01).
    lsr = _finite_float(lsr)
    if lsr is not None and lsr < 0:
        lsr = None
    top_position_ratio = _finite_float(top_position_ratio)
    if top_position_ratio is not None and top_position_ratio < 0:
        top_position_ratio = None
    acquired_at = datetime.now(timezone.utc)
    source_observed_at, source_time_status = provider_source_time(raw, acquired_at)

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

    try:
        return OpenInterestSnapshot(
            symbol=symbol,
            exchange=exchange,
            open_interest_usd=oi_usd,
            open_interest_coin=oi_coin,
            oi_change_1h_pct=oi_change_1h_pct,
            long_short_ratio=lsr,
            top_trader_position_ratio=top_position_ratio,
            source_observed_at=source_observed_at,
            acquired_at=acquired_at.isoformat(),
            source_time_status=source_time_status,
            observed_at=source_observed_at or acquired_at.isoformat(),
        )
    except ValueError:
        return None


def _insert_historical_funding_snapshot(
    sym: str, exchange_name: str, ts_ms: int, rate: float,
) -> int:
    """Store one observed historical rate; do not invent a subsequent settlement."""
    from aletheia.storage.repositories import _select_all, insert_funding_rate_snapshot

    observed_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    prior = _select_all(
        "funding_rate_snapshots",
        where="symbol=? AND exchange=? AND observed_at=? AND next_funding_time=?",
        params=(sym, exchange_name, observed_at, observed_at),
        limit=1,
    )
    if prior:
        return 0
    snapshot = FundingRateSnapshot(
        symbol=sym, exchange=exchange_name, funding_rate=rate,
        funding_rate_annualized=round(rate * 3 * 365, 4),
        next_funding_time=observed_at, mark_price=None, observed_at=observed_at,
    )
    insert_funding_rate_snapshot(snapshot.model_dump())
    return 1


async def _maybe_backfill_funding_history(
    exchange, sym: str, perp_sym: str, exchange_name: str, since_hours: int,
    errors: list[str] | None = None,
) -> tuple[int, bool]:
    """Backfill sparse public rate history for market context, not account settlement.

    A window with at least three stored observations skips this refresh.
    """
    from aletheia.storage.repositories import _select_all
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all(
        "funding_rate_snapshots",
        where="symbol=? AND exchange=? AND observed_at>?", params=(sym, exchange_name, cutoff), limit=3,
    )
    if len(existing) >= 3:
        return 0, True
    try:
        history = await exchange.fetch_funding_rate_history(perp_sym, limit=24)
    except Exception as e:
        if errors is not None:
            errors.append(f"funding_history {sym}: {e}")
        return 0, False
    valid_items: list[tuple[int, float]] = []
    invalid_found = False
    for item in history:
        raw_ts = item.get("timestamp")
        raw_rate = item.get("fundingRate")
        ts = _finite_float(raw_ts)
        rate = _finite_float(raw_rate)
        annualized = _finite_float(rate * 3 * 365) if rate is not None else None
        if ts is None or ts <= 0 or rate is None or annualized is None:
            invalid_found = True
            continue
        valid_items.append((int(ts), rate))
    if invalid_found:
        code = f"funding_history_invalid {sym}"
        if errors is not None and code not in errors:
            errors.append(code)
    valid_items.sort(key=lambda item: item[0])
    n = 0
    for ts_ms, rate in valid_items:
        n += _insert_historical_funding_snapshot(sym, exchange_name, ts_ms, rate)
    return n, False


async def _maybe_backfill_oi_history(
    exchange, sym: str, perp_sym: str, exchange_name: str, since_hours: int,
    errors: list[str] | None = None,
) -> tuple[int, bool]:
    """Same shape as funding backfill. Bitget raises NotSupported — caller must
    not re-raise (per spec MS-C06)."""
    from aletheia.storage.repositories import _select_all, insert_open_interest_snapshot
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all(
        "open_interest_snapshots",
        where="symbol=? AND exchange=? AND observed_at>?", params=(sym, exchange_name, cutoff), limit=3,
    )
    if len(existing) >= 3:
        return 0, True
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
        return 0, False
    n = 0
    for item in history:
        raw_ts = item.get("timestamp")
        ts = _finite_float(raw_ts)
        if ts is None or ts <= 0:
            if raw_ts is not None:
                code = f"oi_history_invalid {sym}"
                if errors is not None and code not in errors:
                    errors.append(code)
            continue
        # Store USD, consistent with the live path. History rows on OKX/Binance
        # carry openInterestValue (USD); if a venue omits it we skip rather than
        # persist a coin/contract amount as USD and mix units within the table.
        extracted = extract_oi_usd(item)
        if extracted is None:
            code = f"oi_history_invalid {sym}"
            if errors is not None and code not in errors:
                errors.append(code)
            continue
        oi_usd, oi_coin = extracted
        observed = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
        prior = _select_all(
            "open_interest_snapshots",
            where="symbol=? AND exchange=? AND observed_at=?", params=(sym, exchange_name, observed), limit=1,
        )
        if prior:
            continue
        snap = OpenInterestSnapshot(
            symbol=sym,
            exchange=exchange_name,
            open_interest_usd=oi_usd,
            open_interest_coin=oi_coin,
            oi_change_1h_pct=None,
            long_short_ratio=None,
            top_trader_position_ratio=None,
            observed_at=observed,
        )
        insert_open_interest_snapshot(snap.model_dump())
        n += 1
    return n, False


async def _fetch_binance_ratio_batch(symbols: list[str], url: str) -> dict[str, float]:
    results: dict[str, float] = {}
    semaphore = asyncio.Semaphore(4)
    # Four requests per stream, eight combined; CCXT remains sequential.
    async with httpx_client(timeout=_LSR_BUDGET_SEC) as client:
        async def fetch(sym: str) -> None:
            async with semaphore:
                try:
                    resp = await client.get(
                        url,
                        params={"symbol": sym.replace("/", ""), "period": "1h", "limit": 1},
                    )
                    if resp.status_code != 200:
                        return
                    items = resp.json()
                    if isinstance(items, list) and items:
                        ratio = _finite_float(items[0].get("longShortRatio"))
                        if ratio is not None and ratio > 0:
                            results[sym] = ratio
                except Exception as exc:
                    logger.debug("Ratio fetch failed for %s: %s", sym, exc)

        tasks = [asyncio.create_task(fetch(sym)) for sym in dict.fromkeys(symbols)]
        try:
            if tasks:
                await asyncio.wait(tasks, timeout=_LSR_BUDGET_SEC)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return results
