"""yFinance macro-context provider — OPTIONAL, default OFF.

Gated by ``market_data.enable_yfinance_macro`` (default False). When on,
``fetch_macro_context`` returns a small markdown block summarising macro proxies
(S&P 500, VIX, Dollar index, Gold) for the LLM prompt. Pure augmentation: it has
no agent instrumentation, is never persisted, and returns "" on any failure so the
caller (market_context.build_market_context) silently omits the section.

yfinance.download is synchronous and blocking, so it runs in the default executor —
mirroring the pattern in agents/stock_observer.py (never await yfinance directly).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("aletheia.yfinance")

# yFinance native tickers → display label. ^GSPC/^VIX/DX-Y.NYB/GC=F are the
# canonical Yahoo symbols for these macro proxies.
_MACRO_TICKERS = [
    ("^GSPC", "S&P 500"),
    ("^VIX", "VIX"),
    ("DX-Y.NYB", "Dollar Index"),
    ("GC=F", "Gold"),
]
_DAILY_BARS = 90


def _fetch_macro_sync() -> str:
    """Blocking yfinance fetch + render. Returns markdown, or "" on any failure."""
    try:
        import yfinance as yf

        from aletheia.trading.indicators import momentum_pct, rsi

        rows = ["| Asset | Close | 5d % | RSI14 |", "|---|---:|---:|---:|"]
        any_row = False
        for ticker, label in _MACRO_TICKERS:
            try:
                hist = yf.download(
                    ticker, period=f"{_DAILY_BARS}d", interval="1d",
                    auto_adjust=True, progress=False, show_errors=False,
                )
                if hist is None or hist.empty:
                    continue
                closes = [float(x) for x in hist["Close"].dropna().tolist()]
                if len(closes) < 6:
                    continue
                last = closes[-1]
                ret_5d = momentum_pct(closes, 5)
                rsi14 = rsi(closes, 14)
                ret_s = "n/a" if ret_5d is None else f"{ret_5d:+.2f}"
                rsi_s = "n/a" if rsi14 is None else f"{rsi14:.1f}"
                rows.append(f"| {label} | {last:.2f} | {ret_s} | {rsi_s} |")
                any_row = True
            except Exception as e:  # noqa: BLE001 — one bad ticker must not sink the block
                logger.debug("macro ticker %s failed: %s", ticker, e)
        if not any_row:
            # All tickers returned empty — yfinance may have a broken API or schema change.
            logger.warning(
                "yfinance macro context: all %d tickers returned empty data "
                "(yfinance library change or Yahoo Finance API outage?)",
                len(_MACRO_TICKERS),
            )
            return ""
        return "### Macro context (daily)\n" + "\n".join(rows) + "\n"
    except ImportError:
        logger.warning("yfinance not installed — macro context block unavailable")
        return ""
    except Exception as e:  # noqa: BLE001
        logger.warning("macro context fetch failed: %s", e)
        return ""


async def fetch_macro_context() -> str:
    """Return a markdown macro-context block, or "" on failure. Never raises."""
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch_macro_sync)
    except Exception as e:  # noqa: BLE001
        logger.warning("macro context executor failed: %s", e)
        return ""
