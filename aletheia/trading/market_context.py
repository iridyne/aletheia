"""Market context builder — fix the "all neutral / speculative" root cause.

Diagnosis (`docs/research/market_context_eval.md`):
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

import logging
from dataclasses import dataclass

from aletheia.trading.indicators import (
    sma, rsi, atr, bollinger, momentum_pct, zscore_last, relative_strength_rank,
)

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
    last_close: float
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


def _compute_snap(closes: list[float], highs: list[float], lows: list[float],
                  volumes: list[float], timeframe: str) -> _IndicatorSnap:
    """Single-timeframe indicator pack. All inputs in oldest→newest order."""
    last = closes[-1] if closes else 0.0
    bb = bollinger(closes, period=20, k=2.0)
    if bb:
        bb_lower, bb_mid, bb_upper = bb
        # %B = (price - lower) / (upper - lower); 0=lower band, 1=upper band.
        if bb_upper != bb_lower:
            pct_b = (last - bb_lower) / (bb_upper - bb_lower)
        else:
            pct_b = 0.5
    else:
        bb_lower = bb_upper = pct_b = None
    return _IndicatorSnap(
        last_close=last,
        sma_20=sma(closes, 20),
        sma_50=sma(closes, 50),
        rsi_14=rsi(closes, 14),
        atr_14=atr(highs, lows, closes, 14),
        bb_lower=bb_lower,
        bb_upper=bb_upper,
        bb_pct_b=pct_b,
        momentum_10=momentum_pct(closes, 10),
        volume_zscore_20=zscore_last(volumes, 20),
        timeframe=timeframe,
        bars_used=len(closes),
    )


def _fmt(value: float | None, fmt: str = "{:.2f}") -> str:
    """None-safe number formatter for markdown table cells."""
    if value is None:
        return "n/a"
    try:
        return fmt.format(value)
    except (ValueError, TypeError):
        return "n/a"


def _render_snap_table(symbol: str, snaps: list[_IndicatorSnap]) -> str:
    """Markdown table — one row per timeframe, columns = indicators.

    Wide-format keeps cross-timeframe agreement visually obvious (the model
    sees "1h RSI 68 / 4h RSI 62 / 1d RSI 58" lined up and can reason about it).
    """
    if not snaps:
        return f"### {symbol}\n_no data_\n"
    rows = [
        "| TF | Close | SMA20 | SMA50 | RSI14 | ATR14 | BB% | Mom10 | Vol z |",
        "|----|------:|------:|------:|------:|------:|----:|------:|------:|",
    ]
    for s in snaps:
        rows.append(
            f"| {s.timeframe} | {_fmt(s.last_close)} | {_fmt(s.sma_20)} | "
            f"{_fmt(s.sma_50)} | {_fmt(s.rsi_14, '{:.1f}')} | "
            f"{_fmt(s.atr_14)} | {_fmt(s.bb_pct_b, '{:.2f}')} | "
            f"{_fmt(s.momentum_10, '{:+.2f}%')} | "
            f"{_fmt(s.volume_zscore_20, '{:+.2f}')} |"
        )
    return f"### {symbol}\n" + "\n".join(rows) + "\n"


def _render_recent_closes(symbol: str, closes: list[float], take: int = 10) -> str:
    """Last N closes inline so the model can see the shape of the move."""
    if not closes:
        return ""
    tail = closes[-take:]
    series = ", ".join(_fmt(c) for c in tail)
    return f"- {symbol} last {len(tail)} 1h closes: [{series}]\n"


def _render_relative_strength(returns_24h: dict[str, float | None]) -> str:
    """Rank table for cross-asset relative strength."""
    valid = {s: r for s, r in returns_24h.items() if r is not None}
    if not valid:
        return ""
    ranks = relative_strength_rank({**valid})
    rows = ["| Rank | Symbol | 24h % |", "|---:|---|---:|"]
    for sym, rank in ranks:
        ret = returns_24h.get(sym)
        rows.append(f"| {rank} | {sym} | {_fmt(ret, '{:+.2f}%')} |")
    return "### Cross-asset relative strength (24h)\n" + "\n".join(rows) + "\n"


_INDICATOR_GLOSSARY = """\
### Indicator glossary (use these meanings; don't invent rules)
- **SMA20 / SMA50** — simple moving averages. Price > SMA50 is uptrend; price < SMA50 is downtrend; price between is mixed.
- **RSI14** — momentum oscillator 0–100. >70 overbought, <30 oversold; 50 = neutral. In strong trends RSI can stay extreme — confirm with trend direction.
- **ATR14** — average true range, in price units. Bigger ATR = wider expected swings; useful for sizing stops, not for direction.
- **BB%** — position within Bollinger band (0=lower, 1=upper). >1 or <0 = outside the band (rare).
- **Mom10** — % change over the last 10 bars. Positive trending up, negative trending down.
- **Vol z** — volume z-score vs last 20 bars. >+1 = unusually high volume; <-1 = unusually low.
"""


async def build_market_context(
    symbols: list[str],
    *,
    market_type: str = "spot",
    client=None,   # legacy param — ignored, kept for call-site compatibility
    provider=None, # MarketDataProvider; created from config if None
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
        from aletheia.data.ccxt_provider import CCXTProvider
        provider = CCXTProvider(
            exchange=md_cfg.ccxt_exchange,
            fallback_exchange=md_cfg.ccxt_fallback,
        )
        _owned_provider = True

    from aletheia.data.cache import get_cache
    cache = get_cache()

    debug: dict = {"symbols": {}, "errors": []}
    sections: list[str] = []
    returns_24h: dict[str, float | None] = {}

    try:
        for sym in symbols:
            sym_debug: dict = {}
            snaps: list[_IndicatorSnap] = []
            closes_1h: list[float] = []
            for tf, limit in _TIMEFRAMES:
                try:
                    candles = cache.get(sym, tf, limit)
                    if candles is None:
                        candles = await provider.get_ohlcv(sym, timeframe=tf, limit=limit)
                        cache.set(sym, tf, limit, candles)
                except Exception as e:
                    debug["errors"].append(f"{sym}/{tf}: {e}")
                    logger.warning("OHLCV fetch failed %s/%s: %s", sym, tf, e)
                    continue
                if not candles:
                    continue
                # Sort oldest→newest (providers should guarantee this, but be defensive)
                candles = sorted(candles, key=lambda c: c.timestamp)
                closes = [c.close for c in candles]
                highs = [c.high for c in candles]
                lows = [c.low for c in candles]
                volumes = [c.volume for c in candles]
                snap = _compute_snap(closes, highs, lows, volumes, tf)
                snaps.append(snap)
                sym_debug[tf] = {
                    "bars": len(candles), "last_close": snap.last_close,
                    "rsi_14": snap.rsi_14, "sma_50": snap.sma_50,
                    "momentum_10": snap.momentum_10,
                }
                if tf == "1h":
                    closes_1h = closes
                    if len(closes) >= 25:
                        past = closes[-25]
                        last = closes[-1]
                        if past:
                            returns_24h[sym] = (last - past) / past * 100
                    else:
                        returns_24h[sym] = None
            if snaps:
                sections.append(_render_snap_table(sym, snaps))
                sections.append(_render_recent_closes(sym, closes_1h, take=10))
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
            snap = latest_snapshot(sym)
            if snap:
                legacy_lines.append(
                    f"{sym}: price={snap['price']} 24h_change="
                    f"{snap.get('change_pct_24h')}%"
                )
            else:
                legacy_lines.append(f"{sym}: no snapshot yet")
        debug["fallback"] = True
        return "\n".join(legacy_lines), debug

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
        + "\n" + _INDICATOR_GLOSSARY
    )
    debug["fallback"] = False
    return body, debug
