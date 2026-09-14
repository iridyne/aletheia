"""yFinance macro-context provider — OPTIONAL, default OFF.

Gated by ``market_data.enable_yfinance_macro`` (default False). When on,
``fetch_macro_context`` returns a small markdown block summarising macro proxies
(S&P 500, VIX, Dollar index, Gold) for the LLM prompt. Pure augmentation: it has
no agent instrumentation, is never persisted, and returns "" on any failure so the
caller (market_context.build_market_context) silently omits the section.

yfinance.download is synchronous and blocking, so it runs in the default executor —
mirroring the pattern in agents/equity_observer.py (never await yfinance directly).
"""

from __future__ import annotations

import asyncio
import logging
from aletheia.data.models import EquityFetchError

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
                    auto_adjust=True, progress=False,
                )
                if hist is None or hist.empty:
                    continue
                close_data = hist["Close"]
                if hasattr(close_data, "columns"):
                    close_data = close_data.iloc[:, 0]
                closes = [float(x) for x in close_data.dropna().tolist()]
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


def fetch_equity_fundamentals(tickers: list[str]) -> list[str]:
    """Read public Yahoo fundamentals for the caller-selected research universe."""
    try:
        import yfinance as yf
    except ImportError:
        return []
    lines = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            pe = info.get("trailingPE") or info.get("forwardPE")
            ps = info.get("priceToSalesTrailing12Months")
            mcap = info.get("marketCap")
            rev_growth = info.get("revenueGrowth")
            margin = info.get("profitMargins")
            sector = info.get("sector", "")
            parts = [f"{ticker}:"]
            if pe:
                parts.append(f"PE={pe:.1f}")
            if ps:
                parts.append(f"PS={ps:.1f}")
            if mcap:
                parts.append(f"MCap=${mcap / 1e9:.0f}B")
            if rev_growth is not None:
                parts.append(f"RevGrowth={rev_growth * 100:.0f}%")
            if margin is not None:
                parts.append(f"Margin={margin * 100:.0f}%")
            if sector:
                parts.append(f"({sector})")
            if len(parts) > 1:
                lines.append(" ".join(parts))
        except Exception:
            continue
    return lines


def fetch_equity_indicators(ticker: str, _retry: bool = True) -> dict | EquityFetchError | None:
    """Fetch yfinance daily bars + compute technical indicators.

    Returns:
      - dict: success (indicator values + raw close for cross-validation)
      - None: soft failure (empty / insufficient data)
      - EquityFetchError: hard failure (import error / network exception)
    """
    try:
        import yfinance as yf
    except ImportError:
        return EquityFetchError(reason="yfinance_not_installed")

    import time

    try:
        from aletheia.trading.indicators import sma, rsi, atr, bollinger, momentum_pct, zscore_last

        hist = yf.download(
            ticker,
            period=f"{_DAILY_BARS}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if hist is None or hist.empty:
            if _retry:
                time.sleep(3)
                return fetch_equity_indicators(ticker, _retry=False)
            return None

        # yfinance >=1.4 returns MultiIndex DataFrame for single ticker;
        # .squeeze() collapses it to a Series regardless of version.
        closes = [float(x) for x in hist["Close"].squeeze().dropna().tolist()]
        highs = [float(x) for x in hist["High"].squeeze().dropna().tolist()]
        lows = [float(x) for x in hist["Low"].squeeze().dropna().tolist()]
        volumes = [float(x) for x in hist["Volume"].squeeze().dropna().tolist()]

        if len(closes) < 20:
            return None

        n = min(len(closes), len(highs), len(lows), len(volumes))
        closes, highs, lows, volumes = closes[-n:], highs[-n:], lows[-n:], volumes[-n:]

        last = closes[-1]
        sma20 = sma(closes, 20)
        sma50 = sma(closes, 50)
        rsi14 = rsi(closes, 14)
        atr14 = atr(highs, lows, closes, 14)
        mom10 = momentum_pct(closes, 10)
        vol_z = zscore_last(volumes, 20)

        bb = bollinger(closes, period=20, k=2.0)
        bb_lower = bb_upper = bb_pct_b = None
        if bb:
            bb_lower, _, bb_upper = bb
            if bb_upper and bb_lower and bb_upper != bb_lower:
                bb_pct_b = (last - bb_lower) / (bb_upper - bb_lower)

        return {
            "ticker": ticker,
            "last": last,
            "bars": n,
            "sma20": sma20,
            "sma50": sma50,
            "rsi14": rsi14,
            "atr14": atr14,
            "bb_lower": bb_lower,
            "bb_upper": bb_upper,
            "bb_pct_b": bb_pct_b,
            "momentum_10": mom10,
            "volume_zscore": vol_z,
        }
    except Exception as e:
        if _retry:
            time.sleep(3)
            return fetch_equity_indicators(ticker, _retry=False)
        return EquityFetchError(reason=f"exception:{e}")


def fetch_live_price(ticker: str) -> float | None:
    """Fetch live intraday price via yfinance fast_info (no API key required).

    Returns None on any failure so callers can fall back to prior-close.
    """
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).fast_info
        price = getattr(info, "last_price", None)
        if price and float(price) > 0:
            return float(price)
    except Exception:
        pass
    return None
