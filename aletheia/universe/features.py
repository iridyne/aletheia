"""Feature Collector — batch tickers → volume pre-filter → concurrent OHLCV."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.data.freshness import fresh_row
from aletheia.storage.models import UniverseFeatureSnapshot
from aletheia.storage import repositories as repo
from aletheia.trading.indicators import rsi, atr, sma, bollinger, volume_sma_ratio
from aletheia.trading.technical_analysis import TechnicalBar, timeframe_milliseconds
from aletheia.trading.technical_snapshot_service import get_technical_snapshot_service

logger = logging.getLogger("aletheia.universe.features")

# Phase 3 retention (2026-07-17): change-suppression on universe_feature_snapshots.
# Feature collection runs every ~10 min per asset. When the values are
# effectively unchanged, we still wrote a full row every cycle → most of
# the table's rows were near-duplicates.
#
# Rules (per (asset_id)):
#   age < 30 min  → skip write (minimum interval between writes).
#   age ≥ 60 min  → force write (heartbeat floor — data-freshness monitors and
#                    downstream freshness gates rely on a live row per asset per hour).
#   30 – 60 min   → skip only if all persisted numeric fields are equivalent
#                    (exact-round on most, 0.5% relative delta on the noisy
#                    percentage/rate fields).
_FEATURE_SUPPRESSION_MIN_SEC = 30 * 60
_FEATURE_SUPPRESSION_HEARTBEAT_SEC = 60 * 60
# Fields that are inherently noisy percentage/rate quantities — compare with
# a 0.5% relative delta rather than exact-rounded equality.
_FEATURE_SOFT_DELTA_FIELDS = (
    "atr_pct", "rsi_14", "return_1h", "funding_rate", "oi_delta_1h",
    "rsi_15m", "atr_15m_pct", "return_15m", "last_candle_range_atr_15m",
    "rsi_4h", "atr_4h_pct", "bb_width_4h", "vol_ratio_4h",
)
_FEATURE_SOFT_DELTA_TOLERANCE = 0.005  # 0.5%
# All other numeric fields on UniverseFeatureSnapshot. Compared via rounded equality.
_FEATURE_EXACT_FIELDS = (
    "quote_volume_24h", "volume_1h", "spread_bps", "depth_10bps_usd",
    "return_4h", "return_24h", "open_interest", "data_freshness_sec",
    "btc_beta", "taker_buy_ratio", "vol_ratio", "relative_strength_24h",
    "oi_price_divergence",
    "bb_squeeze",
    "technical_algorithm_version", "technical_snapshot_manifest",
)


def _values_close_soft(prev, new) -> bool:
    """Compare two floats with a 0.5% relative delta; None == None → equal."""
    if prev is None and new is None:
        return True
    if prev is None or new is None:
        return False
    try:
        prev_f = float(prev)
        new_f = float(new)
    except (TypeError, ValueError):
        return prev == new
    denom = max(abs(prev_f), 1e-9)
    return abs(new_f - prev_f) / denom <= _FEATURE_SOFT_DELTA_TOLERANCE


def _values_close_exact(prev, new) -> bool:
    """Compare two values with rounded equality; None == None → equal."""
    if prev is None and new is None:
        return True
    if prev is None or new is None:
        return False
    try:
        return round(float(prev), 6) == round(float(new), 6)
    except (TypeError, ValueError):
        return prev == new


def _feature_snapshot_equivalent(prev_row: dict, new_snap: "UniverseFeatureSnapshot") -> bool:
    """True if the persisted numeric fields of `new_snap` match `prev_row` closely."""
    for field in _FEATURE_SOFT_DELTA_FIELDS:
        if not _values_close_soft(prev_row.get(field), getattr(new_snap, field, None)):
            return False
    for field in _FEATURE_EXACT_FIELDS:
        if not _values_close_exact(prev_row.get(field), getattr(new_snap, field, None)):
            return False
    return True


def _seconds_between(prev_ts: str | None, now: datetime) -> float | None:
    """Return seconds between an ISO timestamp string and `now` (UTC)."""
    if not prev_ts:
        return None
    try:
        parsed = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds()


def _persist_feature_snapshot(snap: "UniverseFeatureSnapshot") -> None:
    """Insert a feature snapshot, tolerating the post-migration raw-column drop.

    A Phase 3 migration drops the always-empty `raw` column from
    `universe_feature_snapshots`. Filter the field out of the payload here so
    the writer stays compatible before and after the migration runs.
    """
    repo.insert_universe_feature_snapshot(snap)


def persist_feature_snapshot_for_emission(feature: dict) -> str | None:
    """Make a suppressed calculation durable before it becomes T0 evidence."""
    snap = feature.get("_computed_feature_snapshot")
    if not isinstance(snap, UniverseFeatureSnapshot):
        return feature.get("feature_snapshot_id")
    if feature.get("feature_snapshot_id") != snap.id:
        _persist_feature_snapshot(snap)
        feature["feature_snapshot_id"] = snap.id
    return snap.id

_OHLCV_CONCURRENCY = 10
_OHLCV_TIMEOUT = 12.0
_OHLCV_1H_LIMIT = 168  # 7 days of 1h candles
_OHLCV_15M_LIMIT = 100
_TICKER_CHUNK_SIZE = 100
# Perf (Phase 5): global semaphore bounding total OHLCV concurrency across all
# assets. With inter-asset concurrency already in place (gather over assets)
# and now intra-asset parallel timeframes (3 per asset), this caps the total
# in-flight OHLCV requests at the provider's safe limit.
_OHLCV_SEM = asyncio.Semaphore(_OHLCV_CONCURRENCY)
# taker_buy_ratio is only consumed by the event family, which gates on a sharp
# 1h move; fetch it lazily for movers above this |return_1h| % to keep the scan
# cheap (the taker endpoint is a per-symbol call with no batch form).
_TAKER_FETCH_THRESHOLD = 3.0
_DERIVATIVES_MARKET_TYPES = frozenset({"perp", "swap", "futures", "usdt_futures"})


def _is_derivatives_market(market_type: object) -> bool:
    return str(market_type or "").lower() in _DERIVATIVES_MARKET_TYPES


def _pct_return(closes: list[float], bars: int) -> float | None:
    """Percentage return over the last N bars."""
    if len(closes) < bars + 1:
        return None
    old = closes[-(bars + 1)]
    if old == 0:
        return None
    return ((closes[-1] - old) / old) * 100.0


def _atr_pct(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """ATR as a percentage of the last close."""
    atr_val = atr(highs, lows, closes, period)
    if atr_val is None or not closes or closes[-1] == 0:
        return None
    return (atr_val / closes[-1]) * 100.0


def _last_candle_range_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14,
) -> float | None:
    """Last candle high-low range expressed in ATR units."""
    atr_val = atr(highs, lows, closes, period)
    if atr_val is None or atr_val <= 0 or not highs or not lows:
        return None
    return (highs[-1] - lows[-1]) / atr_val


def _relative_squeeze_state(
    current_width: float | None,
    historical_widths: list[float],
) -> tuple[float | None, bool]:
    """Expose the degree behind the existing Bollinger squeeze Boolean.

    The Boolean behavior is unchanged. The ratio is transient task-4 Shadow
    input only; persistence and authority remain separate later gates.
    """
    if current_width is None or not historical_widths:
        return None, False
    average_width = sum(historical_widths) / len(historical_widths)
    is_squeeze = current_width < average_width * 0.6
    ratio = current_width / average_width if average_width > 0 else None
    return ratio, is_squeeze


def _compute_beta(asset_closes: list[float], btc_closes: list[float]) -> float | None:
    """Compute beta of asset vs BTC using hourly returns. Returns None if insufficient data."""
    n = min(len(asset_closes), len(btc_closes))
    if n < 20:
        return None
    asset_rets = [(asset_closes[i] - asset_closes[i-1]) / asset_closes[i-1]
                  for i in range(1, n) if asset_closes[i-1] != 0]
    btc_rets = [(btc_closes[i] - btc_closes[i-1]) / btc_closes[i-1]
                for i in range(1, n) if btc_closes[i-1] != 0]
    if len(asset_rets) != len(btc_rets) or len(asset_rets) < 20:
        return None
    mean_a = sum(asset_rets) / len(asset_rets)
    mean_b = sum(btc_rets) / len(btc_rets)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(asset_rets, btc_rets)) / len(asset_rets)
    var_b = sum((b - mean_b) ** 2 for b in btc_rets) / len(btc_rets)
    if var_b == 0:
        return None
    return cov / var_b


def _spread_bps(bid: float | None, ask: float | None) -> float | None:
    """Spread in basis points from ticker bid/ask."""
    if bid is None or ask is None or bid <= 0:
        return None
    return ((ask - bid) / bid) * 10000.0


def _is_valid_symbol(symbol: str) -> bool:
    """Reject symbols with non-ASCII chars (e.g. 龙虾/USDT) that fail exchange regex."""
    return symbol.isascii()


def _positive_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _load_fresh_enrichment_rows(
    asset: dict, *, now: datetime,
) -> tuple[dict | None, dict | None, dict | None]:
    """Load exact-instrument DB fallbacks and reject each stale snapshot as a unit.

    Derivatives reads stay strictly on the asset's own venue: cross-venue
    aggregate feeds (Hyperliquid/Coinalyze) must NOT stand in for the primary
    trading venue's view (see repositories._latest_deriv_where). Full perp
    coverage on the primary venue is provided by the batch collector sweep, so
    a cross-venue read fallback is both unnecessary and unit-unsafe here.
    """
    symbol = str(asset.get("symbol") or "")
    venue = str(asset.get("venue") or "")
    market_type = str(asset.get("market_type") or "spot")
    ob_row = fresh_row(
        repo.latest_orderbook(symbol, exchange=venue, market_type=market_type),
        "orderbook",
        now=now,
    )
    if not _is_derivatives_market(market_type):
        return ob_row, None, None
    deriv_exchange = venue or None
    fr_row = fresh_row(
        repo.latest_funding_rate(symbol, exchange=deriv_exchange),
        "funding",
        now=now,
    )
    oi_row = fresh_row(
        repo.latest_open_interest(symbol, exchange=deriv_exchange),
        "open_interest",
        now=now,
    )
    return ob_row, fr_row, oi_row


def _quote_volume_24h(exchange, ccxt_symbol: str, ticker: dict) -> float:
    """Return 24h quote-currency volume for spot or contract tickers.

    OKX swap tickers leave ``quoteVolume`` empty and expose ``baseVolume`` as
    contract count. Convert contracts to base units with ``contractSize`` before
    multiplying by the last price. Keeping this conversion in one helper ensures
    the volume pre-filter and persisted feature use identical economics.
    """
    direct = _positive_float(ticker.get("quoteVolume"))
    if direct is not None:
        return direct

    base_volume = _positive_float(ticker.get("baseVolume"))
    last = _positive_float(ticker.get("last") or ticker.get("close"))
    if base_volume is None or last is None:
        return 0.0

    markets = getattr(exchange, "markets", {}) or {}
    market = markets.get(ccxt_symbol) or {}
    if market.get("contract"):
        contract_size = _positive_float(market.get("contractSize"))
        if contract_size is None:
            return 0.0
        base_volume *= contract_size

    return base_volume * last


def _ticker_candidate_fact(
    asset: dict,
    *,
    decision: str,
    reason_code: str,
    inputs: dict | None = None,
) -> dict:
    symbol = str(asset.get("symbol") or "")
    venue = str(asset.get("venue") or "")
    market_type = str(asset.get("market_type") or "spot")
    candidate_key = asset.get("id") or f"instrument:{venue}:{market_type}:{symbol}"
    return {
        "candidate_key": str(candidate_key),
        "asset_id": asset.get("id"),
        "symbol": symbol,
        "venue": venue,
        "market_type": market_type,
        "decision": decision,
        "reason_code": reason_code,
        "inputs": inputs or {},
    }


def _evaluate_ticker_prefilters(
    assets: list[dict],
    tickers: dict,
    exchange,
    to_ccxt_perp,
    *,
    min_volume: float,
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Evaluate ticker availability and volume in order without sampling facts."""
    candidates: list[tuple[dict, dict]] = []
    ticker_facts: list[dict] = []
    volume_facts: list[dict] = []

    for asset in assets:
        symbol = str(asset.get("symbol") or "")
        asset_id = str(asset.get("id") or "")
        if not symbol or not asset_id:
            ticker_facts.append(_ticker_candidate_fact(
                asset,
                decision="rejected",
                reason_code="asset_identity_missing",
                inputs={"has_symbol": bool(symbol), "has_asset_id": bool(asset_id)},
            ))
            volume_facts.append(_ticker_candidate_fact(
                asset,
                decision="not_applicable",
                reason_code="ticker_prerequisite_failed",
            ))
            continue

        is_perp = _is_derivatives_market(asset.get("market_type"))
        ccxt_symbol = to_ccxt_perp(symbol) if is_perp else symbol
        ticker = tickers.get(ccxt_symbol)
        if ticker is None:
            ticker_facts.append(_ticker_candidate_fact(
                asset,
                decision="rejected",
                reason_code="ticker_unavailable",
                inputs={"ticker_symbol": ccxt_symbol},
            ))
            volume_facts.append(_ticker_candidate_fact(
                asset,
                decision="not_applicable",
                reason_code="ticker_prerequisite_failed",
            ))
            continue

        ticker_facts.append(_ticker_candidate_fact(
            asset,
            decision="passed",
            reason_code="ticker_available",
            inputs={"ticker_symbol": ccxt_symbol},
        ))
        volume = _quote_volume_24h(exchange, ccxt_symbol, ticker)
        if volume < min_volume:
            volume_facts.append(_ticker_candidate_fact(
                asset,
                decision="rejected",
                reason_code="quote_volume_below_prefilter_minimum",
                inputs={"quote_volume_24h": volume},
            ))
            continue

        volume_facts.append(_ticker_candidate_fact(
            asset,
            decision="passed",
            reason_code="quote_volume_prefilter_sufficient",
            inputs={"quote_volume_24h": volume},
        ))
        candidates.append((asset, ticker))

    return candidates, ticker_facts, volume_facts


def _record_ticker_prefilter_batches(
    scanner_run_id: str | None,
    ticker_facts: list[dict],
    volume_facts: list[dict],
    cfg: AletheiaConfig,
) -> None:
    if not scanner_run_id:
        return

    from aletheia.analytics.filter_evidence import (
        HEAVY_BATCH_MIN_INTERVAL_SEC,
        record_filter_decision_batch,
    )

    record_filter_decision_batch(
        run_id=scanner_run_id,
        candidate_kind="universe_asset",
        filter_name="ticker_availability",
        stage="ticker_prefilter",
        candidate_facts=ticker_facts,
        parameters={
            "primary_venue": cfg.market_data.ccxt_exchange,
            "fallback_venue": cfg.market_data.ccxt_fallback or cfg.market_data.ccxt_exchange,
        },
        min_interval_sec=HEAVY_BATCH_MIN_INTERVAL_SEC,
    )
    record_filter_decision_batch(
        run_id=scanner_run_id,
        candidate_kind="universe_asset",
        filter_name="ticker_volume_prefilter",
        stage="ticker_prefilter",
        candidate_facts=volume_facts,
        parameters={
            "minimum_quote_volume_24h_usdt": cfg.universe.min_volume_24h_usdt,
        },
        min_interval_sec=HEAVY_BATCH_MIN_INTERVAL_SEC,
    )


async def _chunked_fetch_tickers(exchange, symbols: list[str]) -> dict:
    """Fetch tickers in chunks to stay within Binance URL length limits."""
    all_tickers: dict = {}
    valid = [s for s in symbols if _is_valid_symbol(s) and s in exchange.markets]
    for i in range(0, len(valid), _TICKER_CHUNK_SIZE):
        chunk = valid[i:i + _TICKER_CHUNK_SIZE]
        try:
            all_tickers.update(await exchange.fetch_tickers(chunk))
        except Exception as e:
            logger.warning("Ticker chunk %d-%d failed: %s", i, i + len(chunk), e)
    return all_tickers


async def _fetch_single_feature(
    exchange, asset: dict, ticker: dict, now: datetime, to_ccxt_perp,
    btc_return_24h: float | None = None,
    btc_return_4h: float | None = None,
    btc_closes_1h: list[float] | None = None,
    snapshot_buffer: list[UniverseFeatureSnapshot] | None = None,
) -> dict | None:
    """Fetch OHLCV + enrich for a single asset. Returns feature dict or None."""
    symbol = asset.get("symbol", "")
    asset_id = asset.get("id", "")
    is_perp = _is_derivatives_market(asset.get("market_type"))

    ccxt_sym = to_ccxt_perp(symbol) if is_perp else symbol

    quote_volume_24h = _quote_volume_24h(exchange, ccxt_sym, ticker)
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    spread = _spread_bps(
        float(bid) if bid else None,
        float(ask) if ask else None,
    )

    # Perf (Phase 5): fetch the three OHLCV timeframes in parallel instead of
    # serial awaits. Each fetch is an independent network round-trip; running
    # them concurrently collapses ~3× latency into ~1×. The shared semaphore
    # (_OHLCV_SEM, created below) bounds total concurrency across all assets
    # so the provider's rate limit is not exceeded.
    async def _fetch_15m() -> list:
        try:
            return await asyncio.wait_for(
                exchange.fetch_ohlcv(ccxt_sym, timeframe="15m", limit=_OHLCV_15M_LIMIT),
                timeout=_OHLCV_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            return []

    async def _fetch_1h() -> list:
        try:
            return await asyncio.wait_for(
                exchange.fetch_ohlcv(ccxt_sym, timeframe="1h", limit=_OHLCV_1H_LIMIT),
                timeout=_OHLCV_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            return []

    async def _fetch_4h() -> list:
        # P1b: 4h candles for multi-timeframe features
        try:
            return await asyncio.wait_for(
                exchange.fetch_ohlcv(ccxt_sym, timeframe="4h", limit=50),
                timeout=_OHLCV_TIMEOUT,
            )
        except (asyncio.TimeoutError, Exception):
            return []

    async def _fetch_ohlcv_bounded(tf_fetch) -> list:
        """Run one OHLCV fetch under the global semaphore."""
        async with _OHLCV_SEM:
            return await tf_fetch()

    ohlcv_15m, ohlcv_1h, ohlcv_4h = await asyncio.gather(
        _fetch_ohlcv_bounded(_fetch_15m),
        _fetch_ohlcv_bounded(_fetch_1h),
        _fetch_ohlcv_bounded(_fetch_4h),
        return_exceptions=True,
    )
    # Normalize exceptions to empty lists (preserves original error isolation).
    ohlcv_15m = [] if isinstance(ohlcv_15m, BaseException) else ohlcv_15m
    ohlcv_1h = [] if isinstance(ohlcv_1h, BaseException) else ohlcv_1h
    ohlcv_4h = [] if isinstance(ohlcv_4h, BaseException) else ohlcv_4h

    now_ms = int(now.timestamp() * 1000)

    def _closed_rows(rows: list, timeframe: str) -> list:
        interval = timeframe_milliseconds(timeframe)
        if interval is None:
            return rows
        return [row for row in rows if int(row[0]) + interval <= now_ms]

    ohlcv_15m = _closed_rows(ohlcv_15m, "15m")
    ohlcv_1h = _closed_rows(ohlcv_1h, "1h")
    ohlcv_4h = _closed_rows(ohlcv_4h, "4h")

    def _technical_bars(rows: list) -> list[TechnicalBar]:
        return [
            TechnicalBar(
                ts=int(c[0]), open=float(c[1]), high=float(c[2]), low=float(c[3]),
                close=float(c[4]), volume=float(c[5]),
            )
            for c in rows
        ]

    venue = str(asset.get("venue") or getattr(exchange, "id", "unknown"))
    market_type = str(asset.get("market_type") or "spot")
    technical_service = get_technical_snapshot_service()
    technical_15m = technical_service.snapshot_from_bars(
        _technical_bars(ohlcv_15m), exchange=venue, symbol=symbol,
        market_type=market_type, timeframe="15m",
    )
    technical_1h = technical_service.snapshot_from_bars(
        _technical_bars(ohlcv_1h), exchange=venue, symbol=symbol,
        market_type=market_type, timeframe="1h",
    )
    technical_4h = technical_service.snapshot_from_bars(
        _technical_bars(ohlcv_4h), exchange=venue, symbol=symbol,
        market_type=market_type, timeframe="4h",
    )

    closes_15m = [float(c[4]) for c in ohlcv_15m]
    highs_15m = [float(c[2]) for c in ohlcv_15m]
    lows_15m = [float(c[3]) for c in ohlcv_15m]
    closes_1h = [float(c[4]) for c in ohlcv_1h]
    highs_1h = [float(c[2]) for c in ohlcv_1h]
    lows_1h = [float(c[3]) for c in ohlcv_1h]
    volumes_1h = [float(c[5]) for c in ohlcv_1h]
    closes_4h = [float(c[4]) for c in ohlcv_4h]
    highs_4h = [float(c[2]) for c in ohlcv_4h]
    lows_4h = [float(c[3]) for c in ohlcv_4h]
    volumes_4h = [float(c[5]) for c in ohlcv_4h]

    rsi_15m = technical_15m.values["rsi_14"]
    atr_15m_pct = technical_15m.values["atr_pct"]
    return_15m = _pct_return(closes_15m, 1)
    last_candle_range_atr_15m = (
        _last_candle_range_atr(highs_15m, lows_15m, closes_15m)
        if len(closes_15m) >= 15 else None
    )
    rsi_val = technical_1h.values["rsi_14"]
    atr_val = technical_1h.values["atr_pct"]
    return_1h = _pct_return(closes_1h, 1)
    return_4h = _pct_return(closes_1h, 4)
    return_24h = _pct_return(closes_1h, 24)
    volume_1h = volumes_1h[-1] * closes_1h[-1] if volumes_1h and closes_1h else None

    # P1b: 4h timeframe features (true 4h candles, not derived from 1h)
    rsi_4h = technical_4h.values["rsi_14"]
    atr_4h = technical_4h.values["atr_pct"]
    bb_width_4h = technical_4h.values["bb_width_pct"]
    vol_ratio_4h = technical_4h.values["volume_ratio_20"]

    # Phase 1+P0 features: vol_ratio, bb_width, bb_squeeze, relative_strength, beta
    vol_ratio = volume_sma_ratio(volumes_1h, 72) if len(volumes_1h) >= 72 else (
        volume_sma_ratio(volumes_1h, 20) if len(volumes_1h) >= 20 else None
    )

    bb = bollinger(closes_1h, 20, 2.0) if len(closes_1h) >= 20 else None
    bb_width_pct = None
    bb_squeeze_ratio = None
    bb_squeeze = False
    if bb:
        lower, mid, upper = bb
        if mid > 0:
            bb_width_pct = (upper - lower) / mid * 100
            # Relative squeeze: compare current BB width to historical average
            if len(closes_1h) >= 100:
                historical_widths = []
                for i in range(20, min(100, len(closes_1h))):
                    # US-5: window slice [i-20:i] is O(20); the prefix slice [:i]
                    # was O(i) → O(n²) overall (up to 80 full recomputes/asset).
                    # bollinger uses the last `period` points, so the two are
                    # numerically identical.
                    h_bb = bollinger(closes_1h[i - 20:i], 20, 2.0)
                    if h_bb and h_bb[1] > 0:
                        historical_widths.append((h_bb[2] - h_bb[0]) / h_bb[1] * 100)
                if historical_widths:
                    bb_squeeze_ratio, bb_squeeze = _relative_squeeze_state(
                        bb_width_pct, historical_widths,
                    )

    relative_strength_24h = None
    relative_strength_4h = None
    if return_24h is not None and btc_return_24h is not None:
        relative_strength_24h = return_24h - btc_return_24h
    if return_4h is not None and btc_return_4h is not None:
        relative_strength_4h = return_4h - btc_return_4h

    # Beta coefficient vs BTC (P3: for beta-adjusted RS)
    btc_beta = None
    if btc_closes_1h and len(closes_1h) >= 72 and len(btc_closes_1h) >= 72:
        btc_beta = _compute_beta(closes_1h[-72:], btc_closes_1h[-72:])

    # Beta-adjusted relative strength
    rs_beta_adj_24h = None
    if relative_strength_24h is not None and btc_beta is not None and btc_beta > 0:
        expected_move = btc_return_24h * btc_beta if btc_return_24h else 0
        rs_beta_adj_24h = return_24h - expected_move if return_24h is not None else None

    data_freshness_sec = None
    if ohlcv_1h:
        last_ts = int(ohlcv_1h[-1][0]) / 1000
        data_freshness_sec = int(time.time() - last_ts)

    ob_row, fr_row, oi_row = _load_fresh_enrichment_rows(asset, now=now)

    depth_10bps = ob_row["total_depth_10bps_usd"] if ob_row else None
    funding_rate = fr_row["funding_rate"] if fr_row else None
    open_interest = oi_row["open_interest_usd"] if oi_row else None
    oi_delta_1h = oi_row["oi_change_1h_pct"] if oi_row else None

    # P1a: Real-time funding rate for perp-eligible symbols overrides the fresh DB fallback.
    # P2: Taker buy/sell ratio for perp symbols
    taker_buy_ratio = None
    if is_perp:
        perp_sym = to_ccxt_perp(symbol)
        mark_price = None
        try:
            fr_data = await asyncio.wait_for(
                exchange.fetch_funding_rate(perp_sym),
                timeout=5.0,
            )
            if fr_data:
                mark_price = fr_data.get("markPrice")
                if fr_data.get("fundingRate") is not None:
                    funding_rate = float(fr_data["fundingRate"])
        except Exception:
            pass  # Keep only a freshness-qualified DB value as fallback.

        # Live open interest overrides a stale DB fallback for the absolute value.
        # oi_delta_1h stays from the collector's true-1h computation (oi_row) —
        # features has only the current instant, not a 1h-ago reference. Same
        # USD-unit rule as the collector (prefer openInterestValue, else
        # amount × mark) via the shared extractor.
        try:
            from aletheia.data.collectors.derivatives import extract_oi_usd
            oi_data = await asyncio.wait_for(
                exchange.fetch_open_interest(perp_sym),
                timeout=5.0,
            )
            extracted = extract_oi_usd(oi_data or {}, mark_price)
            if extracted is not None:
                open_interest = extracted[0]
        except Exception:
            pass  # Keep only a freshness-qualified DB value as fallback.

        # Taker buy/sell ratio. Only the event family uses it, and event only
        # fires on a sharp 1h move — so fetch it lazily for movers to keep the
        # scan cheap. Venue-matched: Binance fapi or OKX rubik taker-volume.
        if return_1h is not None and abs(return_1h) > _TAKER_FETCH_THRESHOLD:
            ex_id = getattr(exchange, "id", "")
            try:
                if ex_id == "binance":
                    raw_sym = symbol.replace("/", "")  # BTC/USDT → BTCUSDT
                    taker_data = await asyncio.wait_for(
                        exchange.fapipublic_get_takerlongshortratio({
                            "symbol": raw_sym, "period": "1h", "limit": 1,
                        }),
                        timeout=5.0,
                    )
                    if taker_data and len(taker_data) > 0:
                        ratio = float(taker_data[0].get("buySellRatio", 0))
                        if ratio > 0:
                            taker_buy_ratio = ratio
                elif ex_id == "okx":
                    base = symbol.split("/")[0]
                    resp = await asyncio.wait_for(
                        exchange.public_get_rubik_stat_taker_volume({
                            "ccy": base, "instType": "CONTRACTS", "period": "1H",
                        }),
                        timeout=5.0,
                    )
                    rows = (resp or {}).get("data") or []
                    if rows:
                        # OKX taker-volume row = [ts, sellVol, buyVol]
                        sell_vol = float(rows[0][1])
                        buy_vol = float(rows[0][2])
                        if sell_vol > 0:
                            taker_buy_ratio = buy_vol / sell_vol
            except Exception:
                pass

    if spread is None and ob_row:
        spread = ob_row.get("spread_bps")

    last_price = closes_1h[-1] if closes_1h else None

    feat = {
        "asset_id": asset_id,
        "symbol": symbol,
        "venue": asset.get("venue", ""),
        "market_type": asset.get("market_type", "spot"),
        "quote_volume_24h": quote_volume_24h,
        "volume_1h": volume_1h,
        "spread_bps": spread,
        "depth_10bps_usd": depth_10bps,
        "atr_pct": atr_val,
        "rsi_14": rsi_val,
        "rsi_15m": rsi_15m,
        "atr_15m_pct": atr_15m_pct,
        "return_15m": return_15m,
        "last_candle_range_atr_15m": last_candle_range_atr_15m,
        "return_1h": return_1h,
        "return_4h": return_4h,
        "return_24h": return_24h,
        "funding_rate": funding_rate,
        "open_interest": open_interest,
        "oi_delta_1h": oi_delta_1h,
        "data_freshness_sec": data_freshness_sec,
        "last_price": last_price,
        "vol_ratio": vol_ratio,
        "bb_width_pct": bb_width_pct,
        "bb_squeeze_ratio": bb_squeeze_ratio,
        "bb_squeeze": bb_squeeze,
        "relative_strength_24h": relative_strength_24h,
        "relative_strength_4h": relative_strength_4h,
        "btc_beta": btc_beta,
        "rs_beta_adj_24h": rs_beta_adj_24h,
        "rsi_4h": rsi_4h,
        "atr_4h": atr_4h,
        "bb_width_4h": bb_width_4h,
        "vol_ratio_4h": vol_ratio_4h,
        "taker_buy_ratio": taker_buy_ratio,
    }

    # OI-price divergence: +1=hidden_accumulation (OI↑ price↓ = possible accumulation),
    # -1=weak_rally (OI↓ price↑ = weak rally, distribution risk), 0=neutral
    oi_price_divergence = None
    if oi_delta_1h is not None and return_1h is not None:
        if oi_delta_1h > 3 and return_1h < -0.5:
            oi_price_divergence = 1
        elif oi_delta_1h < -3 and return_1h > 0.5:
            oi_price_divergence = -1
        else:
            oi_price_divergence = 0

    snap = UniverseFeatureSnapshot(
        asset_id=asset_id,
        ts=now.isoformat(),
        quote_volume_24h=quote_volume_24h,
        volume_1h=volume_1h,
        spread_bps=spread,
        depth_10bps_usd=depth_10bps,
        atr_pct=atr_val,
        rsi_14=rsi_val,
        rsi_15m=rsi_15m,
        atr_15m_pct=atr_15m_pct,
        return_15m=return_15m,
        last_candle_range_atr_15m=last_candle_range_atr_15m,
        return_1h=return_1h,
        return_4h=return_4h,
        return_24h=return_24h,
        funding_rate=funding_rate,
        open_interest=open_interest,
        oi_delta_1h=oi_delta_1h,
        data_freshness_sec=data_freshness_sec,
        btc_beta=btc_beta,
        taker_buy_ratio=taker_buy_ratio,
        vol_ratio=vol_ratio,
        relative_strength_24h=relative_strength_24h,
        oi_price_divergence=oi_price_divergence,
        rsi_4h=rsi_4h,
        atr_4h_pct=atr_4h,
        bb_width_4h=bb_width_4h,
        vol_ratio_4h=vol_ratio_4h,
        bb_squeeze=bb_squeeze,
        technical_algorithm_version=technical_1h.algorithm_version,
        technical_snapshot_manifest=json.dumps(
            {
                tf: {
                    "bar_close_ts": snapshot.bar_close_ts,
                    "bars_used": snapshot.bars_used,
                    "history_bars_retained": snapshot.history_bars_retained,
                    "warmup_complete": snapshot.warmup_complete,
                    "input_fingerprint": snapshot.input_fingerprint,
                    "data_quality": snapshot.data_quality,
                    "quality_reasons": list(snapshot.quality_reasons),
                }
                for tf, snapshot in (
                    ("15m", technical_15m), ("1h", technical_1h), ("4h", technical_4h)
                )
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    feat["_computed_feature_snapshot"] = snap
    feat["feature_snapshot_id"] = snap.id

    # Phase 3 retention: change-suppression. Skip near-duplicate writes; force
    # a write past the 60-min heartbeat floor so per-asset freshness stays live.
    prev_row = repo.get_latest_feature_snapshot(asset_id)
    should_write = True
    if prev_row is not None:
        age_sec = _seconds_between(prev_row.get("ts"), now)
        if age_sec is not None:
            if age_sec < _FEATURE_SUPPRESSION_MIN_SEC:
                # Within the 30-min minimum-interval window — never write.
                should_write = False
                # Reuse the previous snapshot id for downstream consumers so
                # the feat dict still references a real row.
                feat["feature_snapshot_id"] = prev_row.get("id", snap.id)
            elif age_sec < _FEATURE_SUPPRESSION_HEARTBEAT_SEC:
                # 30-60 min window — write only if the numeric shape moved.
                if _feature_snapshot_equivalent(prev_row, snap):
                    should_write = False
                    feat["feature_snapshot_id"] = prev_row.get("id", snap.id)

    if should_write:
        if snapshot_buffer is None:
            _persist_feature_snapshot(snap)
        else:
            snapshot_buffer.append(snap)

    return feat


async def collect_features(
    assets: list[dict],
    cfg: AletheiaConfig,
    scanner_run_id: str | None = None,
) -> list[dict]:
    """Two-phase feature collection: batch tickers → volume pre-filter → concurrent OHLCV.

    Phase A: fetch all tickers in batch (spot + perp separately) — ~2-4s total.
    Phase B: pre-filter by min_volume, then fetch OHLCV concurrently with semaphore.
    """
    from aletheia.data.ccxt_provider import CCXTProvider
    from aletheia.data.symbols import to_ccxt_perp

    # #91 fix (features.py counterpart to registry.py): use market_data.ccxt_fallback
    # instead of same-exchange fallback so a primary outage falls through cleanly.
    md = cfg.market_data
    venue = md.ccxt_exchange
    fallback_venue = md.ccxt_fallback or venue
    provider = CCXTProvider(exchange=venue, fallback_exchange=fallback_venue)
    results = []

    try:
        exchange = provider._ensure_primary()
        exchange.options["defaultType"] = "spot"
        await exchange.load_markets()

        # --- Phase A: batch ticker fetch (spot + perp) ---
        spot_assets = [a for a in assets if not _is_derivatives_market(a.get("market_type"))]
        perp_assets = [a for a in assets if _is_derivatives_market(a.get("market_type"))]

        spot_symbols = list(dict.fromkeys(a["symbol"] for a in spot_assets if a.get("symbol")))
        perp_symbols = list(dict.fromkeys(to_ccxt_perp(a["symbol"]) for a in perp_assets if a.get("symbol")))

        tickers: dict = {}

        exchange.options["defaultType"] = "spot"
        tickers.update(await _chunked_fetch_tickers(exchange, spot_symbols))

        if perp_symbols:
            exchange.options["defaultType"] = "swap"
            tickers.update(await _chunked_fetch_tickers(exchange, perp_symbols))

        # --- Volume pre-filter ---
        min_vol = cfg.universe.min_volume_24h_usdt
        candidates, ticker_facts, volume_facts = _evaluate_ticker_prefilters(
            assets,
            tickers,
            exchange,
            to_ccxt_perp,
            min_volume=min_vol,
        )
        _record_ticker_prefilter_batches(
            scanner_run_id, ticker_facts, volume_facts, cfg,
        )

        logger.info(
            "Volume pre-filter: %d/%d assets pass (min_vol=%.0f USDT)",
            len(candidates), len(assets), min_vol,
        )

        # --- BTC baseline for relative strength + beta ---
        btc_return_24h = None
        btc_return_4h = None
        btc_closes_1h: list[float] = []
        try:
            btc_ohlcv = await asyncio.wait_for(
                exchange.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=_OHLCV_1H_LIMIT),
                timeout=_OHLCV_TIMEOUT,
            )
            if btc_ohlcv:
                btc_closes_1h = [float(c[4]) for c in btc_ohlcv]
                btc_return_24h = _pct_return(btc_closes_1h, 24)
                btc_return_4h = _pct_return(btc_closes_1h, 4)
        except Exception as e:
            logger.warning("BTC baseline fetch failed: %s", e)

        # --- Phase B: concurrent OHLCV fetch ---
        now = datetime.now(timezone.utc)
        sem = asyncio.Semaphore(_OHLCV_CONCURRENCY)
        pending_snapshots: list[UniverseFeatureSnapshot] = []

        async def _guarded_fetch(asset_ticker_pair):
            asset, ticker = asset_ticker_pair
            async with sem:
                try:
                    return await _fetch_single_feature(
                        exchange, asset, ticker, now, to_ccxt_perp,
                        btc_return_24h=btc_return_24h,
                        btc_return_4h=btc_return_4h,
                        btc_closes_1h=btc_closes_1h,
                        snapshot_buffer=pending_snapshots,
                    )
                except Exception as e:
                    logger.debug("Feature collect failed for %s: %s", asset.get("symbol"), e)
                    return None

        gathered = await asyncio.gather(
            *[_guarded_fetch(pair) for pair in candidates],
            return_exceptions=True,
        )

        failed_snapshot_ids: set[str] = set()
        if pending_snapshots:
            try:
                repo.insert_universe_feature_snapshots_batch(pending_snapshots)
            except Exception as exc:
                failed_snapshot_ids = {snapshot.id for snapshot in pending_snapshots}
                logger.warning(
                    "Feature snapshot batch persist failed for %d assets: %s",
                    len(pending_snapshots),
                    exc,
                )

        # Phase A (2026-07-19) — fingerprint-refresh wake.
        # `skip_policy.candidate_feature_refresh_wakes_operator=True` (skip_policy.py:44)
        # declares that feature refreshes must reopen deferred Operator evaluations,
        # but the previous implementation only fired ONE wake at the end of the
        # universe_refresh cycle (universe/service.py). Under a full defer queue
        # this stranded reopens for up to 10 minutes. Wake pi_daemon per-batch so
        # a fresh feature_snapshot_id lands in the fingerprint within seconds,
        # not tens of minutes. Only wake when at least one deferred carrier is
        # currently live for a symbol that this batch touched — otherwise the
        # wake is guaranteed useless and only costs daemon debounce cycles.
        committed_asset_ids = {
            snap.asset_id
            for snap in pending_snapshots
            if snap.id not in failed_snapshot_ids and snap.asset_id
        }
        if committed_asset_ids:
            try:
                _wake_pi_daemon_for_refreshed_defers(committed_asset_ids)
            except Exception:
                logger.debug(
                    "feature_snapshot pi_daemon wake failed", exc_info=True
                )

        for result in gathered:
            if result is not None and not isinstance(result, BaseException):
                if result.get("feature_snapshot_id") not in failed_snapshot_ids:
                    results.append(result)

    finally:
        await provider.close()

    logger.info("Features collected for %d / %d assets", len(results), len(assets))
    return results


def _wake_pi_daemon_for_refreshed_defers(asset_ids: set[str]) -> None:
    """Wake pi_daemon iff a deferred carrier depends on one of these assets.

    A carrier is "depending on this batch" when it has (a) a live
    ``pi_decisions.action='signal_pending'`` row for a symbol registered under
    one of ``asset_ids`` in ``universe_registry`` AND (b) an active
    ``operator_evaluation_events`` defer with ``defer_until > now``. The join
    checks both in a single query and short-circuits at LIMIT 1. Without this
    guard the daemon wakes on every universe_refresh even when nothing is
    deferred — that is safe (2 s debounce + 45 s cooldown absorb noise) but
    it defeats the whole point of the wake, which is to notice defers.

    Only candidate-specific (data-availability) defer reasons trigger a wake.
    Market-quiet defers — ``direct_skip_not_confirmed``, ``operator_low_conviction``,
    ``debate_not_confirmed`` — reflect the operator's judgment, not missing data.
    Feature refreshes do not resolve the confirmation issue, so waking on them
    creates a tight re-evaluation loop with no change in outcome.
    """
    from aletheia.storage.sqlite import get_db

    if not asset_ids:
        return
    placeholders = ",".join("?" for _ in asset_ids)
    now_iso = datetime.now(timezone.utc).isoformat()
    row = get_db().execute(
        "SELECT 1 FROM operator_evaluation_events e "
        "JOIN pi_decisions p "
        "  ON json_extract(e.queue_snapshot_json, '$.opportunity_id')"
        "     = p.opportunity_id "
        "JOIN universe_registry r ON r.symbol = p.symbol "
        "WHERE e.event_kind LIKE 'operator_decision_deferred_%' "
        "AND json_extract(e.queue_snapshot_json, '$.defer_until') > ? "
        "AND p.action = 'signal_pending' "
        "AND json_extract(e.queue_snapshot_json, '$.defer_reason') IN ("
        "    'candidate_technical_snapshot_unavailable',"
        "    'candidate_technical_snapshot_incomplete',"
        "    'candidate_technical_snapshot_stale') "
        f"AND r.id IN ({placeholders}) "
        "LIMIT 1",
        (now_iso, *asset_ids),
    ).fetchone()
    if row is None:
        return
    from aletheia.agent_runtime.pi_daemon import notify_pi_daemon

    notify_pi_daemon(source="feature_snapshot_committed")
