"""Feature Collector — batch tickers → volume pre-filter → concurrent OHLCV."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.data.freshness import fresh_row
from aletheia.data.symbols import (
    instrument_analysis_context,
    instrument_background_from_metadata,
)
from aletheia.storage.models import UniverseFeatureSnapshot
from aletheia.storage import repositories as repo
from aletheia.trading.indicators import rsi, atr, sma, bollinger, volume_sma_ratio
from aletheia.trading.technical_analysis import TechnicalBar, timeframe_milliseconds
from aletheia.trading.technical_snapshot_service import get_technical_snapshot_service

from aletheia.data.providers import _nonnegative_float

logger = logging.getLogger("aletheia.universe.features")

CANDIDATE_FEATURE_ACQUISITION_VERSION = "candidate-feature-acquisition/v2"

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
    feature["feature_ts"] = snap.ts
    feature["feature_snapshot_ts"] = snap.ts
    return snap.id

_OHLCV_CONCURRENCY = 10
_OHLCV_TIMEOUT = 12.0
_OHLCV_1H_LIMIT = 168  # 7 days of 1h candles
_OHLCV_15M_LIMIT = 100
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
    *,
    min_volume: float,
    priority_asset_ids: set[str] | None = None,
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Evaluate ticker availability and volume in order without sampling facts."""
    candidates: list[tuple[dict, dict]] = []
    ticker_facts: list[dict] = []
    volume_facts: list[dict] = []
    priority_asset_ids = priority_asset_ids or set()

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
        market_type = "perp" if is_perp else "spot"
        ticker = tickers.get((market_type, symbol))
        if not isinstance(ticker, dict) or not ticker:
            ticker_facts.append(_ticker_candidate_fact(
                asset,
                decision="rejected",
                reason_code="ticker_unavailable",
                inputs={"ticker_symbol": symbol, "market_type": market_type},
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
            inputs={"ticker_symbol": symbol, "market_type": market_type},
        ))
        volume = _nonnegative_float(ticker.get("quote_volume_24h"))
        if volume is None:
            volume_facts.append(_ticker_candidate_fact(
                asset,
                decision="rejected",
                reason_code="ticker_numeric_invalid",
                inputs={"ticker_symbol": symbol, "market_type": market_type},
            ))
            continue
        if volume < min_volume and asset_id not in priority_asset_ids:
            volume_facts.append(_ticker_candidate_fact(
                asset,
                decision="rejected",
                reason_code="quote_volume_below_prefilter_minimum",
                inputs={"quote_volume_24h": volume},
            ))
            continue

        volume_facts.append(_ticker_candidate_fact(
            asset,
            decision=("not_applicable" if asset_id in priority_asset_ids else "passed"),
            reason_code=(
                "pending_candidate_volume_prefilter_bypassed"
                if asset_id in priority_asset_ids
                else "quote_volume_prefilter_sufficient"
            ),
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


async def _fetch_feature_orderbook(reader, asset: dict) -> dict | None:
    """Acquire and durably publish one exact physical-market observation."""
    from aletheia.data.symbols import InstrumentId

    try:
        venue = str(asset.get("venue") or "")
        if not venue:
            return None
        instrument = InstrumentId.from_parts(
            symbol=asset["symbol"], exchange=venue, market_type=str(asset["market_type"]),
        )
        snap = await reader(instrument)
        if (snap is None or snap.symbol != instrument.symbol
                or snap.exchange != instrument.exchange
                or snap.market_type != instrument.market_type):
            return None
        row = snap.model_dump()
        repo.insert_orderbook_snapshot(row)
        return row
    except Exception:
        logger.debug("Discovery orderbook unavailable", exc_info=True)
        return None


async def _fetch_single_feature(
    asset: dict, ticker: dict, now: datetime,
    btc_return_24h: float | None = None,
    btc_return_4h: float | None = None,
    btc_closes_1h: list[float] | None = None,
    snapshot_buffer: list[UniverseFeatureSnapshot] | None = None,
    force_persist: bool = False,
    *, market_data_provider, diagnostics_out: dict[str, object] | None = None,
) -> dict | None:
    """Fetch OHLCV + enrich for a single asset. Returns feature dict or None."""
    symbol = asset.get("symbol", "")
    asset_id = asset.get("id", "")
    is_perp = _is_derivatives_market(asset.get("market_type"))

    from aletheia.data.symbols import InstrumentId
    venue = str(asset.get("venue") or "")
    if not venue:
        raise ValueError("feature venue required")
    market_type = str(asset.get("market_type") or "spot")
    instrument = InstrumentId.from_parts(venue, symbol, market_type)

    quote_volume_24h = _nonnegative_float(ticker.get("quote_volume_24h"))
    bid = ticker.get("bid_price")
    ask = ticker.get("ask_price")
    spread = _spread_bps(
        float(bid) if bid else None,
        float(ask) if ask else None,
    )

    ohlcv_15m = []
    ohlcv_1h = []
    ohlcv_4h = []
    try:
        ohlcv_15m = await asyncio.wait_for(
            market_data_provider.get_primary_ohlcv(instrument, timeframe="15m", limit=_OHLCV_15M_LIMIT),
            timeout=_OHLCV_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("Discovery OHLCV 15m unavailable: %s", type(exc).__name__)

    try:
        ohlcv_1h = await asyncio.wait_for(
            market_data_provider.get_primary_ohlcv(instrument, timeframe="1h", limit=_OHLCV_1H_LIMIT),
            timeout=_OHLCV_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("Discovery OHLCV 1h unavailable: %s", type(exc).__name__)

    # P1b: 4h candles for multi-timeframe features
    try:
        ohlcv_4h = await asyncio.wait_for(
            market_data_provider.get_primary_ohlcv(instrument, timeframe="4h", limit=50),
            timeout=_OHLCV_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("Discovery OHLCV 4h unavailable: %s", type(exc).__name__)

    now_ms = int(now.timestamp() * 1000)
    from aletheia.data.market_history import persist_closed_candles

    accepted_candles = {}

    def _closed_rows(rows: list, timeframe: str) -> list:
        interval = timeframe_milliseconds(timeframe)
        candles = [candle for candle in rows
                   if candle.exchange == venue and candle.is_closed
                   and (interval is None or candle.timestamp + interval <= now_ms)]
        accepted_candles[timeframe] = candles
        return [[c.timestamp, c.open, c.high, c.low, c.close, c.volume] for c in candles]

    ohlcv_15m = _closed_rows(ohlcv_15m, "15m")
    ohlcv_1h = _closed_rows(ohlcv_1h, "1h")
    ohlcv_4h = _closed_rows(ohlcv_4h, "4h")

    if not ohlcv_1h or (force_persist and (not ohlcv_15m or not ohlcv_4h)):
        if diagnostics_out is not None:
            diagnostics_out["ohlcv_unavailable"] = 1
        return None

    def _technical_bars(rows: list) -> list[TechnicalBar]:
        return [
            TechnicalBar(
                ts=int(c[0]), open=float(c[1]), high=float(c[2]), low=float(c[3]),
                close=float(c[4]), volume=float(c[5]),
            )
            for c in rows
        ]

    # Persist the exact closed inputs before handing their features to planning.
    for timeframe, candles in accepted_candles.items():
        await asyncio.to_thread(persist_closed_candles, symbol, timeframe, candles,
                                exchange=venue, market_type=market_type, provider="ccxt")

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

    instrument_background = instrument_background_from_metadata(asset.get("metadata"))
    analysis_context = instrument_analysis_context(instrument_background)
    crypto_context = (analysis_context or {}).get("kind") == "crypto"

    relative_strength_24h = None
    relative_strength_4h = None
    if crypto_context and return_24h is not None and btc_return_24h is not None:
        relative_strength_24h = return_24h - btc_return_24h
    if crypto_context and return_4h is not None and btc_return_4h is not None:
        relative_strength_4h = return_4h - btc_return_4h

    # Beta coefficient vs BTC (P3: for beta-adjusted RS)
    btc_beta = None
    if (
        crypto_context
        and btc_closes_1h
        and len(closes_1h) >= 72
        and len(btc_closes_1h) >= 72
    ):
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

    enrichment_at = datetime.now(timezone.utc)
    ob_row, fr_row, oi_row = _load_fresh_enrichment_rows(
        asset, now=enrichment_at,
    )
    if ob_row is None:
        ob_row = await _fetch_feature_orderbook(market_data_provider.get_orderbook, asset)

    if ob_row is None and diagnostics_out is not None:
        diagnostics_out["orderbook_unavailable"] = 1
    depth_10bps = ob_row["total_depth_10bps_usd"] if ob_row else None
    from aletheia.universe.candidate_funding import stored_funding_proof
    funding_evidence = stored_funding_proof(fr_row, now=enrichment_at)
    funding_rate = fr_row["funding_rate"] if fr_row else None
    funding_observed_at = fr_row.get("observed_at") if fr_row else None
    open_interest = oi_row["open_interest_usd"] if oi_row else None
    oi_delta_1h = oi_row["oi_change_1h_pct"] if oi_row else None
    long_short_ratio = oi_row["long_short_ratio"] if oi_row else None
    top_trader_position_ratio = (
        oi_row["top_trader_position_ratio"] if oi_row else None
    )

    # P1a: Real-time funding rate for perp-eligible symbols overrides the fresh DB fallback.
    # P2: Taker buy/sell ratio for perp symbols
    taker_buy_ratio = None
    if is_perp:
        try:
            observation = await asyncio.wait_for(
                market_data_provider.get_primary_funding(instrument, ticker), timeout=5.0,
            )
            received_at = datetime.now(timezone.utc)
            observed_at = observation.get("source_observed_at")
            if (observation.get("venue") == venue and observation.get("symbol") == symbol
                    and observation.get("funding_rate") is not None and observed_at
                    and fresh_row({"observed_at": observed_at}, "funding", now=received_at)):
                from aletheia.universe.candidate_funding import PERP_FUNDING_EVIDENCE_VERSION
                funding_rate = observation["funding_rate"]
                funding_observed_at = observed_at
                funding_evidence = {**observation["evidence"], "version": PERP_FUNDING_EVIDENCE_VERSION}
        except Exception:
            pass  # Keep only a freshness-qualified DB value as fallback.

        # Live and stored OI use the same proved canonical units.
        try:
            fact = await asyncio.wait_for(
                market_data_provider.get_primary_open_interest(instrument),
                timeout=5.0,
            )
            received_at = datetime.now(timezone.utc)
            observed_at = fact.get("source_observed_at")
            if (fact.get("venue") == venue
                    and (fact.get("instrument") or {}).get("canonical_symbol") == symbol
                    and fact.get("fact_type") == "open_interest"
                    and fact.get("unit") == "USD"
                    and fact.get("source_scope") == "venue_native"
                    and fact.get("value_kind") == "observed"
                    and fact.get("availability") == "available"
                    and observed_at and fresh_row(
                {"observed_at": observed_at}, "open_interest", now=received_at,
            )):
                open_interest = fact['value']
        except Exception:
            pass  # Keep only a freshness-qualified DB value as fallback.

        # Taker buy/sell ratio. Only the event family uses it, and event only
        # fires on a sharp 1h move — so fetch it lazily for movers to keep the
        # scan cheap. Venue-matched: Binance fapi or OKX rubik taker-volume.
        if return_1h is not None and abs(return_1h) > _TAKER_FETCH_THRESHOLD:
            try:
                taker_buy_ratio = await asyncio.wait_for(
                    market_data_provider.get_primary_taker_ratio(instrument), timeout=5.0,
                )
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
        # Exact registry/source facts consumed by the target Hard Gate.  They are
        # additive in-memory evidence and do not alter the legacy filter path.
        "registry_status": asset.get("status"),
        "feature_source": "universe_feature_snapshot/v1",
        "instrument_background": instrument_background,
        "analysis_context": analysis_context,
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
        "funding_observed_at": funding_observed_at,
        "funding_evidence_json": json.dumps(funding_evidence, sort_keys=True),
        "open_interest": open_interest,
        "oi_delta_1h": oi_delta_1h,
        "long_short_ratio": long_short_ratio,
        "top_trader_position_ratio": top_trader_position_ratio,
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
        # In-memory only: the Momentum setup builder needs the exact 21 closed
        # bars already fetched on this venue. UniverseFeatureSnapshot remains
        # compact; no duplicate JSON candle blob is written to SQLite.
        "_closed_ohlcv_15m": [
            {
                "ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
            }
            for c in ohlcv_15m
        ],
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

    assembled_at = datetime.now(timezone.utc)
    if ob_row is not None and fresh_row(ob_row, "orderbook", now=assembled_at) is None:
        depth_10bps = None
        feat["depth_10bps_usd"] = None
        if diagnostics_out is not None:
            diagnostics_out["orderbook_unavailable"] = 1
    snap = UniverseFeatureSnapshot(
        asset_id=asset_id,
        ts=assembled_at.isoformat(),
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
        long_short_ratio=long_short_ratio,
        top_trader_position_ratio=top_trader_position_ratio,
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
    feat["feature_ts"] = snap.ts
    feat["feature_snapshot_ts"] = snap.ts

    # Phase 3 retention: change-suppression. Skip near-duplicate writes; force
    # a write past the 60-min heartbeat floor so per-asset freshness stays live.
    try:
        prev_row = repo.get_latest_feature_snapshot(asset_id)
    except Exception:
        if diagnostics_out is not None:
            diagnostics_out["feature_persistence_failed"] = 1
        return None
    should_write = True
    derivatives_changed = prev_row is not None and any(
        prev_row.get(field) != getattr(snap, field) for field in ('open_interest', 'oi_delta_1h')
    )
    if prev_row is not None and not force_persist and not derivatives_changed:
        age_sec = _seconds_between(prev_row.get("ts"), assembled_at)
        if age_sec is not None:
            if age_sec < _FEATURE_SUPPRESSION_MIN_SEC:
                # Within the 30-min minimum-interval window — never write.
                should_write = False
                # Reuse the previous snapshot id for downstream consumers so
                # the feat dict still references a real row.
                feat["feature_snapshot_id"] = prev_row.get("id", snap.id)
                feat["feature_ts"] = prev_row.get("ts") or snap.ts
                feat["feature_snapshot_ts"] = feat["feature_ts"]
            elif age_sec < _FEATURE_SUPPRESSION_HEARTBEAT_SEC:
                # 30-60 min window — write only if the numeric shape moved.
                if _feature_snapshot_equivalent(prev_row, snap):
                    should_write = False
                    feat["feature_snapshot_id"] = prev_row.get("id", snap.id)
                    feat["feature_ts"] = prev_row.get("ts") or snap.ts
                    feat["feature_snapshot_ts"] = feat["feature_ts"]

    if should_write:
        if snapshot_buffer is None:
            try:
                _persist_feature_snapshot(snap)
            except Exception:
                if diagnostics_out is not None:
                    diagnostics_out["feature_persistence_failed"] = 1
                return None
        else:
            snapshot_buffer.append(snap)

    return feat


async def collect_features(
    assets: list[dict],
    cfg: AletheiaConfig,
    scanner_run_id: str | None = None,
    priority_asset_ids: set[str] | None = None,
    *, diagnostics_out: dict[str, object] | None = None,
) -> list[dict]:
    """Two-phase feature collection: batch tickers → volume pre-filter → concurrent OHLCV.

    Phase A: fetch all tickers in batch (spot + perp separately) — ~2-4s total.
    Phase B: pre-filter by min_volume, then fetch OHLCV concurrently with semaphore.
    """
    from aletheia.data.ccxt_provider import get_market_data_provider
    from aletheia.data.symbols import InstrumentId

    # #91 fix (features.py counterpart to registry.py): use market_data.ccxt_fallback
    # instead of same-exchange fallback so a primary outage falls through cleanly.
    md = cfg.market_data
    venue = md.ccxt_exchange
    fallback_venue = md.ccxt_fallback or venue
    spot_provider = get_market_data_provider(
        exchange=venue, native_ccxt=True, fallback_exchange=fallback_venue,
    )
    providers = [spot_provider]
    results = []
    priority_asset_ids = set(priority_asset_ids or ())
    diagnostics = {
        "assets_total": len(assets), "ticker_observed": 0, "prefilter_passed": 0,
        "feature_available": 0, "feature_unavailable": 0,
        "orderbook_unavailable": 0, "reason_counts": {},
    }
    if diagnostics_out is not None:
        diagnostics_out.update(diagnostics)

    try:
        # --- Phase A: batch ticker fetch (spot + perp) ---
        spot_assets = [a for a in assets if not _is_derivatives_market(a.get("market_type"))]
        perp_assets = [a for a in assets if _is_derivatives_market(a.get("market_type"))]
        spot_ready = False
        try:
            await spot_provider.prepare_primary_market("spot")
            spot_ready = True
        except Exception as exc:
            logger.warning("Spot market bootstrap failed: %s", type(exc).__name__)
        perp_ready = False
        if perp_assets:
            perp_provider = get_market_data_provider(
                exchange=venue, native_ccxt=True, fallback_exchange=fallback_venue,
            )
            providers.append(perp_provider)
            try:
                await perp_provider.prepare_primary_market("perp")
                perp_ready = True
            except Exception as exc:
                logger.warning("Perp market bootstrap failed: %s", type(exc).__name__)

        spot_symbols = list(dict.fromkeys(a["symbol"] for a in spot_assets if a.get("symbol")))
        perp_symbols = list(dict.fromkeys(a["symbol"] for a in perp_assets if a.get("symbol")))

        tickers: dict = {}

        if spot_symbols and spot_ready:
            tickers.update(await spot_provider.get_tickers(spot_symbols, market_type="spot"))

        if perp_symbols and perp_ready:
            tickers.update(await perp_provider.get_tickers(perp_symbols, market_type="perp"))

        # --- Volume pre-filter ---
        min_vol = cfg.universe.min_volume_24h_usdt
        candidates, ticker_facts, volume_facts = _evaluate_ticker_prefilters(
            assets,
            tickers,
            min_volume=min_vol,
            priority_asset_ids=priority_asset_ids,
        )
        _record_ticker_prefilter_batches(
            scanner_run_id, ticker_facts, volume_facts, cfg,
        )

        logger.info(
            "Volume pre-filter: %d/%d assets pass (min_vol=%.0f USDT)",
            len(candidates), len(assets), min_vol,
        )

        # --- BTC baseline for crypto-relative features only ---
        btc_return_24h = None
        btc_return_4h = None
        btc_closes_1h: list[float] = []
        needs_btc_context = any(
            (instrument_analysis_context(instrument_background_from_metadata(
                asset.get("metadata")
            )) or {}).get("kind") == "crypto"
            for asset, _ticker in candidates
        )
        if needs_btc_context and spot_ready:
            try:
                btc_ohlcv = await asyncio.wait_for(
                    spot_provider.get_primary_ohlcv(
                        InstrumentId.from_parts(venue, "BTC/USDT", "spot"),
                        timeframe="1h", limit=_OHLCV_1H_LIMIT
                    ),
                    timeout=_OHLCV_TIMEOUT,
                )
                if btc_ohlcv:
                    from aletheia.data.market_history import persist_closed_candles

                    cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    btc_candles = [c for c in btc_ohlcv
                                   if c.exchange == venue and c.is_closed
                                   and c.timestamp + 3600000 <= cutoff_ms]
                    await asyncio.to_thread(
                        persist_closed_candles, "BTC/USDT", "1h", btc_candles,
                        venue, market_type="spot", provider="ccxt",
                    )
                    btc_closes_1h = [c.close for c in btc_candles]
                    btc_return_24h = _pct_return(btc_closes_1h, 24)
                    btc_return_4h = _pct_return(btc_closes_1h, 4)
            except Exception as e:
                logger.warning("BTC baseline acquisition/persistence unavailable: %s", type(e).__name__)

        # --- Phase B: concurrent OHLCV fetch ---
        now = datetime.now(timezone.utc)
        sem = asyncio.Semaphore(_OHLCV_CONCURRENCY)
        pending_snapshots: list[UniverseFeatureSnapshot] = []
        asset_diagnostics: dict[str, dict] = {}

        async def _guarded_fetch(asset_ticker_pair):
            asset, ticker = asset_ticker_pair
            local_diagnostics: dict[str, object] = {}
            asset_diagnostics[str(asset.get("id") or "")] = local_diagnostics
            async with sem:
                try:
                    return await _fetch_single_feature(
                        asset, ticker, now,
                        market_data_provider=(perp_provider if _is_derivatives_market(asset.get("market_type"))
                                              else spot_provider),
                        btc_return_24h=btc_return_24h,
                        btc_return_4h=btc_return_4h,
                        btc_closes_1h=btc_closes_1h,
                        snapshot_buffer=pending_snapshots,
                        force_persist=(
                            str(asset.get("id") or "") in priority_asset_ids
                        ),
                        diagnostics_out=local_diagnostics,
                    )
                except Exception as e:
                    local_diagnostics["ohlcv_unavailable"] = 1
                    logger.debug("Feature collect failed: %s", type(e).__name__)
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

        reasons = diagnostics["reason_counts"]
        for result in gathered:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, dict):
                try:
                    if result.get("feature_snapshot_id") in failed_snapshot_ids:
                        raise RuntimeError("feature_persistence_failed")
                    persist_feature_snapshot_for_emission(result)
                except Exception:
                    reasons["feature_persistence_failed"] = reasons.get("feature_persistence_failed", 0) + 1
                    continue
                results.append(result)
        for local in asset_diagnostics.values():
            for reason, count in local.items():
                reasons[reason] = reasons.get(reason, 0) + count
        invalid_tickers = {row["asset_id"] for row in volume_facts if row["reason_code"] == "ticker_numeric_invalid"}
        diagnostics["ticker_observed"] = sum(
            row["decision"] == "passed" and row["asset_id"] not in invalid_tickers
            for row in ticker_facts
        )
        diagnostics["prefilter_passed"] = len(candidates)
        diagnostics["feature_available"] = len(results)
        diagnostics["feature_unavailable"] = len(candidates) - len(results)
        diagnostics["orderbook_unavailable"] = reasons.get("orderbook_unavailable", 0)
        if len(assets) > diagnostics["ticker_observed"]:
            reasons["ticker_unavailable"] = len(assets) - diagnostics["ticker_observed"]
        if diagnostics_out is not None:
            diagnostics_out.update(diagnostics)
        if (assets and not diagnostics["ticker_observed"]) or (candidates and not results):
            raise RuntimeError("universe_feature_collection_unavailable")

    finally:
        await asyncio.gather(
            *(provider.close() for provider in providers),
            return_exceptions=True,
        )

    logger.info("Features collected for %d / %d assets", len(results), len(assets))
    return results
