"""Market context builder — fix the "all neutral / speculative" root cause.

Diagnosis (`docs/research/market-data/market_context_eval.md`):
  market_observer was feeding the LLM exactly one line per symbol —
  "BTCUSDT: price=X 24h_change=0.05%". With a 1-D input the model can only
  reply at the base rate, so every opportunity came back neutral / speculative.

Fix (this module):
  Borrow the TradingAgents pattern (multi-timeframe pre-computed indicators +
  short close series + markdown verified-snapshot tables) using stdlib only —
  no numpy, no pandas, no ta-lib. Every datum here is something we already
  collect via the existing read-only public Bitget endpoints (`get_ohlcv`).

  - 3 timeframes per symbol: 1h × 48, 4h × 30, 1d × 30
  - Pre-computed indicators per timeframe: SMA20/50, RSI14, ATR14, Bollinger,
    momentum (10-bar), volume z-score
  - Cross-asset relative-strength rank over 24h
  - Rendered as markdown with explicit "use as source of truth" wording

Governance red lines:
  - Read-only public market data only (no creds needed; client.py untouched)
  - No new external sources (no funding/OI/news — those need new client surface)
  - Degrades gracefully: any failure returns the legacy 1-line context so
    market_observer never breaks (red line: agents must never crash)
  - No execution / risk / config side effects — purely a string builder
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from aletheia.trading.indicators import (
    sma, rsi, atr, bollinger, momentum_pct, zscore_last, relative_strength_rank,
)
from aletheia.trading.technical_snapshot_service import get_technical_snapshot_service

logger = logging.getLogger("aletheia.market_context")


# Timeframes in canonical format (1h, 4h, 1d).
# Limits sized for SMA50 coverage: 1h×60=2.5d, 4h×50=8d, 1d×30=1mo.
_TIMEFRAMES = [
    ("1h", 60),
    ("4h", 50),
    ("1d", 30),
]


@dataclass
class _IndicatorSnap:
    last_close: float | None
    sma_20: float | None
    sma_50: float | None
    rsi_14: float | None
    atr_14: float | None
    bb_lower: float | None
    bb_upper: float | None
    bb_pct_b: float | None  # 0..1 = where price sits in the band; >1/<0 = outside
    momentum_10: float | None  # % change over last 10 bars
    volume_zscore_20: float | None
    timeframe: str
    bars_used: int
    ema_20: float | None = None
    ema_50: float | None = None
    atr_pct: float | None = None
    bb_width_pct: float | None = None
    volume_ratio_20: float | None = None
    vwap_20: float | None = None
    donchian_high_20: float | None = None
    donchian_low_20: float | None = None
    obv_normalized: float | None = None
    exchange: str = "unknown"
    symbol: str = ""
    market_type: str = "spot"
    bar_close_ts: int | None = None
    warmup_complete: bool = False
    data_quality: str = "unknown"
    quality_reasons: tuple[str, ...] = ()
    algorithm_version: str = "unknown"
    input_fingerprint: str = ""


def _compute_snap(closes: list[float], highs: list[float], lows: list[float],
                  volumes: list[float], timeframe: str) -> _IndicatorSnap:
    """Single-timeframe indicator pack. All inputs in oldest→newest order."""
    finite_closes = bool(closes) and all(math.isfinite(value) for value in closes)
    finite_volumes = bool(volumes) and all(math.isfinite(value) for value in volumes)
    last = closes[-1] if finite_closes else None
    bb = bollinger(closes, period=20, k=2.0) if finite_closes else None
    if bb:
        bb_lower, bb_mid, bb_upper = bb
        # %B = (price - lower) / (upper - lower); 0=lower band, 1=upper band.
        if bb_upper != bb_lower:
            pct_b = (last - bb_lower) / (bb_upper - bb_lower)
        else:
            pct_b = 0.5
    else:
        bb_lower = bb_upper = pct_b = None
    finite_atr_inputs = finite_closes and all(
        math.isfinite(value) for value in [*highs, *lows]
    )
    rsi_14 = rsi(closes, 14) if finite_closes else None
    atr_14 = atr(highs, lows, closes, 14) if finite_atr_inputs else None
    rsi_14 = rsi_14 if rsi_14 is not None and math.isfinite(rsi_14) else None
    atr_14 = atr_14 if atr_14 is not None and math.isfinite(atr_14) else None
    return _IndicatorSnap(
        last_close=last,
        sma_20=sma(closes, 20) if finite_closes else None,
        sma_50=sma(closes, 50) if finite_closes else None,
        rsi_14=rsi_14,
        atr_14=atr_14,
        bb_lower=bb_lower,
        bb_upper=bb_upper,
        bb_pct_b=pct_b,
        momentum_10=momentum_pct(closes, 10) if finite_closes else None,
        volume_zscore_20=zscore_last(volumes, 20) if finite_volumes else None,
        timeframe=timeframe,
        bars_used=len(closes),
    )


def _indicator_snap_from_shared(snapshot) -> _IndicatorSnap:
    """Adapt the versioned shared snapshot to the stable observer renderer."""
    values = snapshot.values
    return _IndicatorSnap(
        last_close=values.get("last_close"),
        sma_20=values.get("sma_20"),
        sma_50=values.get("sma_50"),
        rsi_14=values.get("rsi_14"),
        atr_14=values.get("atr_14"),
        bb_lower=values.get("bb_lower"),
        bb_upper=values.get("bb_upper"),
        bb_pct_b=values.get("bb_pct_b"),
        momentum_10=values.get("momentum_10"),
        volume_zscore_20=values.get("volume_zscore_20"),
        timeframe=snapshot.timeframe,
        bars_used=snapshot.bars_used,
        ema_20=values.get("ema_20"),
        ema_50=values.get("ema_50"),
        atr_pct=values.get("atr_pct"),
        bb_width_pct=values.get("bb_width_pct"),
        volume_ratio_20=values.get("volume_ratio_20"),
        vwap_20=values.get("vwap_20"),
        donchian_high_20=values.get("donchian_high_20"),
        donchian_low_20=values.get("donchian_low_20"),
        obv_normalized=values.get("obv_normalized"),
        exchange=snapshot.exchange,
        symbol=snapshot.symbol,
        market_type=snapshot.market_type,
        bar_close_ts=snapshot.bar_close_ts,
        warmup_complete=snapshot.warmup_complete,
        data_quality=snapshot.data_quality,
        quality_reasons=snapshot.quality_reasons,
        algorithm_version=snapshot.algorithm_version,
        input_fingerprint=snapshot.input_fingerprint,
    )


def _unavailable_snap(
    timeframe: str,
    reason: str,
    *,
    exchange: str,
    symbol: str,
    market_type: str,
) -> _IndicatorSnap:
    return _IndicatorSnap(
        last_close=None,
        sma_20=None,
        sma_50=None,
        rsi_14=None,
        atr_14=None,
        bb_lower=None,
        bb_upper=None,
        bb_pct_b=None,
        momentum_10=None,
        volume_zscore_20=None,
        timeframe=timeframe,
        bars_used=0,
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        data_quality="missing",
        quality_reasons=(reason,),
    )


def _fmt(value: float | None, fmt: str = "{:.2f}") -> str:
    """None-safe number formatter for markdown table cells."""
    if value is None:
        return "n/a"
    try:
        return fmt.format(value)
    except (ValueError, TypeError):
        return "n/a"


def _pct_delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None or baseline == 0:
        return None
    return (value - baseline) / baseline * 100.0


def _atr_distance(value: float | None, baseline: float | None, atr_value: float | None) -> float | None:
    if value is None or baseline is None or atr_value is None or atr_value <= 0:
        return None
    return (value - baseline) / atr_value


def _trend(snap: _IndicatorSnap) -> str:
    spread = _pct_delta(snap.ema_20, snap.ema_50)
    if None in (snap.last_close, snap.ema_20, snap.ema_50):
        return "n/a"
    if snap.last_close > snap.ema_20 > snap.ema_50:
        state = "up"
    elif snap.last_close < snap.ema_20 < snap.ema_50:
        state = "down"
    else:
        state = "mixed"
    return f"{state}({_fmt(spread, '{:+.2f}%')})"


def _quality(snap: _IndicatorSnap) -> str:
    if snap.data_quality in {"missing", "empty"}:
        reasons = ",".join(snap.quality_reasons[:2]) or snap.data_quality
        return f"unavailable:{reasons[:60]}"
    if not snap.warmup_complete:
        return f"unavailable:warmup({snap.bars_used})"
    if snap.data_quality != "complete":
        reasons = ",".join(snap.quality_reasons[:2]) or snap.data_quality
        return f"unavailable:{reasons[:60]}"
    return "complete"


def _render_snap_table(symbol: str, snaps: list[_IndicatorSnap]) -> str:
    """Render one bounded, normalized technical-context-v1 bundle."""
    if not snaps:
        return f"### {symbol}\n_no data_\n"
    identity = next((snap for snap in snaps if snap.data_quality != "missing"), snaps[0])
    rows = []
    for s in snaps:
        quality = _quality(s)
        if quality != "complete":
            rows.append(f"- {s.timeframe} q={quality} close={_fmt(s.last_close)}")
            continue
        vwap_atr = _atr_distance(s.last_close, s.vwap_20, s.atr_14)
        high_atr = _atr_distance(s.donchian_high_20, s.last_close, s.atr_14)
        low_atr = _atr_distance(s.last_close, s.donchian_low_20, s.atr_14)
        rows.append(
            f"- {s.timeframe} q=ok close={_fmt(s.last_close)} trend={_trend(s)} "
            f"rsi={_fmt(s.rsi_14, '{:.1f}')} mom={_fmt(s.momentum_10, '{:+.2f}%')} "
            f"atr={_fmt(s.atr_pct, '{:.2f}%')} "
            f"bb={_fmt(s.bb_pct_b, '{:.2f}')}/{_fmt(s.bb_width_pct, '{:.2f}%')} "
            f"vwap={_fmt(vwap_atr, '{:+.2f}')}ATR "
            f"donchian=H{_fmt(high_atr, '{:.2f}')}/L{_fmt(low_atr, '{:.2f}')}ATR "
            f"vol={_fmt(s.volume_zscore_20, '{:+.2f}')}z/{_fmt(s.volume_ratio_20, '{:.2f}x')} "
            f"obv={_fmt(s.obv_normalized, '{:+.2f}')}"
        )
    return (
        f"### {symbol} technical-context-v1 ({identity.exchange} {identity.market_type}; closed bars)\n"
        + "\n".join(rows)
        + "\n"
    )


def _technical_context_artifact(symbol: str, snaps: list[_IndicatorSnap], rendered: str) -> dict:
    identity = next((snap for snap in snaps if snap.data_quality != "missing"), snaps[0])
    latest_bar_ts = max(
        (snap.bar_close_ts for snap in snaps if snap.bar_close_ts is not None),
        default=None,
    )
    fingerprint = hashlib.sha256(
        "|".join(
            f"{snap.timeframe}:{snap.bar_close_ts}:{snap.input_fingerprint}"
            for snap in snaps
        ).encode("utf-8")
    ).hexdigest()
    complete = sum(_quality(snap) == "complete" for snap in snaps)
    quality = "complete" if complete == len(snaps) else ("partial" if complete else "unavailable")
    return {
        "adapter_id": "technical-context-v1",
        "stable_id": (
            f"technical-context-v1:{identity.exchange}:{identity.symbol or symbol}:"
            f"{identity.market_type}:{fingerprint}"
        ),
        "source": identity.algorithm_version,
        "observed_at": (
            datetime.fromtimestamp(latest_bar_ts / 1000, tz=timezone.utc).isoformat()
            if latest_bar_ts is not None
            else None
        ),
        "rendered_chars": len(rendered),
        "quality_state": quality,
        "timeframes": [snap.timeframe for snap in snaps],
    }


def _render_relative_strength(returns_24h: dict[str, float | None]) -> str:
    """Rank table for cross-asset relative strength."""
    valid = {s: r for s, r in returns_24h.items() if r is not None}
    # MO-6: a cross-asset RANK needs ≥2 assets. With one symbol "Rank 1 of 1" reads
    # to the LLM as "strongest in market". market_observer analyzes one symbol at a
    # time, so this block was always a meaningless Rank 1 — omit it.
    if len(valid) < 2:
        return ""
    ranks = relative_strength_rank({**valid})
    rows = ["| Rank | Symbol | 24h % |", "|---:|---|---:|"]
    for sym, rank in ranks:
        ret = returns_24h.get(sym)
        rows.append(f"| {rank} | {sym} | {_fmt(ret, '{:+.2f}%')} |")
    return "### Cross-asset relative strength (24h)\n" + "\n".join(rows) + "\n"


_INDICATOR_GLOSSARY = """\
### Indicator glossary (use these meanings; don't invent rules)
- **EMA trend** — up means close > EMA20 > EMA50; down is the mirror; the percentage is EMA20 vs EMA50.
- **RSI14** — momentum oscillator 0–100. >70 overbought, <30 oversold; 50 = neutral. In strong trends RSI can stay extreme — confirm with trend direction.
- **ATR%** — ATR14 normalized by close. Bigger ATR = wider expected swings; it is not directional.
- **BB pos/width** — Bollinger percent-B and normalized band width.
- **Mom10** — % change over the last 10 bars. Positive trending up, negative trending down.
- **VWAP ΔATR** — close minus rolling VWAP20 in ATR units.
- **Donchian H/L ATR** — ATR distance below the 20-bar high / above the 20-bar low.
- **Vol z/x** — volume z-score and ratio to its 20-bar mean. **OBVn** is normalized OBV.
"""


async def build_market_context(
    symbols: list[str],
    *,
    market_type: str = "spot",
    exchange: str | None = None,
    client=None,   # legacy param — ignored, kept for call-site compatibility
    provider=None, # MarketDataProvider; created from config if None
    include_glossary: bool = True,
) -> tuple[str, dict]:
    """Build the verified market snapshot block for the LLM.

    Returns (markdown_text, debug_dict). On any failure, falls back to a
    1-line legacy context derived from the latest stored ticker so callers
    never see an exception.

    debug_dict carries raw indicator values per symbol/timeframe — the agent
    can attach it to a record_step() call so the reasoning trace shows what
    the model actually saw.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    md_cfg = cfg.market_data

    _owned_provider = False
    if provider is None:
        from aletheia.data.ccxt_provider import get_market_data_provider
        provider = get_market_data_provider()
        _owned_provider = True

    from aletheia.data.cache import get_cache
    cache = get_cache()

    debug: dict = {
        "symbols": {},
        "errors": [],
        "technical_context": {"adapter_id": "technical-context-v1", "artifacts": []},
    }
    sections: list[str] = []
    returns_24h: dict[str, float | None] = {}
    technical_service = get_technical_snapshot_service()

    try:
        from aletheia.data.symbols import (
            InstrumentId,
            MARKET_FUTURES,
            MARKET_PERP,
            MARKET_SPOT,
        )

        raw_market_type = str(market_type or MARKET_SPOT).lower()
        instrument_market_type = {
            "perp": MARKET_PERP,
            "swap": MARKET_PERP,
            "usdt_futures": MARKET_PERP,
            "futures": MARKET_FUTURES,
        }.get(raw_market_type, MARKET_SPOT)
        data_exchange = exchange or md_cfg.ccxt_exchange

        for sym in symbols:
            sym_debug: dict = {}
            snaps: list[_IndicatorSnap] = []
            # Strings lose the selected market type inside MarketDataProvider.
            # Keep identity intact through the provider and its cache.
            instrument = InstrumentId.from_parts(
                data_exchange, sym, instrument_market_type,
            )
            for tf, limit in _TIMEFRAMES:
                candles = None
                missing_reason = None
                try:
                    candles = cache.get(
                        instrument.symbol, tf, limit,
                        market_type=instrument.market_type,
                        exchange=instrument.exchange,
                    )
                    if candles is None:
                        candles = await provider.get_ohlcv(
                            instrument, timeframe=tf, limit=limit,
                        )
                except Exception as e:
                    debug["errors"].append(f"{sym}/{tf}: {e}")
                    logger.warning("OHLCV fetch failed %s/%s: %s", sym, tf, e)
                    missing_reason = "fetch_failed"
                if not missing_reason and not candles:
                    missing_reason = "no_candles"
                if missing_reason:
                    snaps.append(_unavailable_snap(
                        tf,
                        missing_reason,
                        exchange=instrument.exchange,
                        symbol=instrument.symbol,
                        market_type=instrument.market_type,
                    ))
                    sym_debug[tf] = {
                        "bars": 0,
                        "technical_snapshot": {
                            "data_quality": "missing",
                            "quality_reasons": [missing_reason],
                        },
                    }
                    continue
                # Sort oldest→newest (providers should guarantee this, but be defensive)
                candles = sorted(candles, key=lambda c: c.timestamp)
                # Exclude the trailing open bar from indicator calculations.
                # CCXT returns the currently-forming bar as the last element;
                # using its partial close/high/low as confirmed data skews RSI,
                # ATR, and momentum, especially for daily candles.
                closed_candles = [c for c in candles if c.is_closed]
                if not closed_candles:
                    snaps.append(_unavailable_snap(
                        tf,
                        "no_closed_bars",
                        exchange=instrument.exchange,
                        symbol=instrument.symbol,
                        market_type=instrument.market_type,
                    ))
                    sym_debug[tf] = {
                        "bars": 0,
                        "technical_snapshot": {
                            "data_quality": "missing",
                            "quality_reasons": ["no_closed_bars"],
                        },
                    }
                    continue
                candle_exchanges = {
                    str(candle.exchange).lower()
                    for candle in closed_candles
                    if getattr(candle, "exchange", None)
                }
                if len(candle_exchanges) > 1:
                    reason = "mixed_candle_venues"
                    debug["errors"].append(f"{sym}/{tf}: {reason}")
                    snaps.append(_unavailable_snap(
                        tf,
                        reason,
                        exchange=instrument.exchange,
                        symbol=instrument.symbol,
                        market_type=instrument.market_type,
                    ))
                    sym_debug[tf] = {
                        "bars": 0,
                        "technical_snapshot": {
                            "data_quality": "missing",
                            "quality_reasons": [reason],
                        },
                    }
                    continue
                candle_exchange = next(iter(candle_exchanges), instrument.exchange)
                closes = [c.close for c in closed_candles]
                highs = [c.high for c in closed_candles]
                lows = [c.low for c in closed_candles]
                volumes = [c.volume for c in closed_candles]
                technical_snapshot = technical_service.snapshot_from_bars(
                    closed_candles,
                    exchange=candle_exchange,
                    symbol=instrument.symbol,
                    market_type=instrument.market_type,
                    timeframe=tf,
                )
                snap = _indicator_snap_from_shared(technical_snapshot)
                snaps.append(snap)
                sym_debug[tf] = {
                    "bars": len(closed_candles), "last_close": snap.last_close,
                    "rsi_14": snap.rsi_14, "sma_50": snap.sma_50,
                    "atr_14": snap.atr_14,
                    "atr_pct": (
                        snap.atr_14 / snap.last_close * 100
                        if snap.atr_14 is not None
                        and snap.last_close is not None
                        and math.isfinite(snap.last_close)
                        and snap.last_close > 0
                        else None
                    ),
                    "momentum_10": snap.momentum_10,
                    "technical_snapshot": {
                        "algorithm_version": technical_snapshot.algorithm_version,
                        "input_fingerprint": technical_snapshot.input_fingerprint,
                        "bar_close_ts": technical_snapshot.bar_close_ts,
                        "history_bars_retained": technical_snapshot.history_bars_retained,
                        "warmup_complete": technical_snapshot.warmup_complete,
                        "data_quality": technical_snapshot.data_quality,
                        "quality_reasons": list(technical_snapshot.quality_reasons),
                    },
                }
                if tf == "1h":
                    if len(closes) >= 25 and all(
                        math.isfinite(close) for close in closes
                    ):
                        past = closes[-25]
                        last = closes[-1]
                        if past:
                            returns_24h[sym] = (last - past) / past * 100
                    else:
                        returns_24h[sym] = None
            available_snaps = [snap for snap in snaps if snap.data_quality != "missing"]
            venues = {snap.exchange for snap in available_snaps}
            if len(venues) > 1:
                reason = "inconsistent_timeframe_venues"
                debug["errors"].append(f"{sym}: {reason}")
                logger.warning("Technical context unavailable %s: %s", sym, reason)
            elif available_snaps:
                rendered = _render_snap_table(sym, snaps)
                sections.append(rendered)
                debug["technical_context"]["artifacts"].append(
                    _technical_context_artifact(sym, snaps, rendered)
                )
            debug["symbols"][sym] = sym_debug
    finally:
        if _owned_provider:
            await provider.close()

    if not sections:
        # Hard fallback: nothing came back at all (network, bad symbols, etc.).
        # Return the legacy 1-liner so the agent still produces *something* and
        # the LLM can output speculative neutral honestly.
        from aletheia.trading.market_data import latest_snapshot
        legacy_lines = []
        for sym in symbols:
            snap = latest_snapshot(sym, market_type=instrument_market_type)
            if snap:
                legacy_lines.append(
                    f"{sym}: price={snap['price']} 24h_change="
                    f"{snap.get('change_pct_24h')}%"
                )
            else:
                legacy_lines.append(f"{sym}: no snapshot yet")
        debug["fallback"] = True
        # MO-7: mark the degraded path explicitly. The normal snapshot tells the LLM
        # "if you cite a number it must come from this block"; the fallback dropped
        # that framing, so the model couldn't tell live data from last-known.
        header = ("## Market data snapshot (⚠️ DEGRADED — live OHLCV unavailable; "
                  "figures below are last-known and may be stale)\n")
        return header + "\n".join(legacy_lines), debug

    rs_block = _render_relative_strength(returns_24h)

    # Optional macro block (yFinance, default off)
    macro_block = ""
    if md_cfg.enable_yfinance_macro:
        try:
            from aletheia.data.yfinance_provider import fetch_macro_context
            macro_block = await fetch_macro_context()
        except Exception as e:
            logger.warning("Macro context fetch failed: %s", e)

    body = (
        "## Verified market data snapshot\n"
        "Source-of-truth for the read below. If you cite a number, it must "
        "come from this block.\n\n"
        + "\n".join(sections)
        + ("\n" + rs_block if rs_block else "")
        + ("\n" + macro_block if macro_block else "")
        + ("\n" + _INDICATOR_GLOSSARY if include_glossary else "")
    )
    debug["fallback"] = False
    return body, debug
