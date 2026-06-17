"""Equity Observer agent — unified US equity observation via yfinance + Bitget xStocks.

Replaces the separate stock_observer (yfinance-only) and the xStocks branch inside
market_observer. For each ticker in cfg.market_data.observed_equities:

  1. Fetch 90-day daily bars from yfinance (authority price + technical indicators).
  2. Optionally fetch the matching Bitget ON-series xStock price in real-time.
  3. Cross-validate: deviation_pct = |xstock_price - yfinance_close| / yfinance_close.
     > 2%  → flagged in metadata as premium_alert
     > 5%  → data_anomaly; xstock data excluded from LLM context
  4. Run LLM analysis with equity-specific system prompt.
  5. Create ONE Opportunity per ticker:
       asset_class="equity"
       asset_subclass="equity_daily"
       canonical_ticker=ticker         (e.g. "AAPL")
       symbol="{ticker}/USDT"          (canonical /USDT form matching Bitget xStock pairs)
       execution_eligible=False        (observation-only; all execution via Bitget xStocks)
       metadata includes yfinance data, xstock price (if available), deviation_pct

Design notes:
- xStocks are tokenized US equities on Bitget — they are the ONLY execution channel
  for US equities since all trading goes through Bitget. deviation_pct is therefore
  NOT an arbitrage signal but a tracking-error / premium monitor.
- Runs Mon–Fri 14:30–21:00 UTC (US market hours). Scheduler enforces this gate.
- Degrades gracefully: yfinance failures log and skip; xStock absence is fine.
"""

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.data.symbols import ticker_to_xstock
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity, list_strategies
from aletheia.config.loader import load_config
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy

logger = logging.getLogger("aletheia.agents.equity_observer")

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}

_DAILY_BARS = 90
_XSTOCK_DEVIATION_ALERT_PCT = 2.0
_XSTOCK_DEVIATION_ANOMALY_PCT = 5.0


class EquityRead(BaseModel):
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(
        50, ge=0, le=100,
        description="0=no idea, 50=balanced, 100=overwhelming evidence",
    )
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-4 bullets citing specific numbers from the indicators",
    )


_SYSTEM_PROMPT = """\
You are an equity market observer for Aletheia paper-trading analysis.

You will receive a verified daily technical snapshot for ONE US equity ticker.
It includes: price (yfinance daily close as authority), SMA20/50, RSI14, ATR14,
Bollinger %B, 10-bar momentum, and volume z-score. When available, a real-time
Bitget xStock price is also shown (this is a tokenized on-chain proxy, not the
NYSE/NASDAQ price — a small premium/discount is normal).

Use only the provided numbers — do not invent data.

Output a structured market read. Calibration:

- direction_bias:
  - "long" if price > SMA50, RSI 50–70 with upward slope, positive momentum,
    above mid-Bollinger. Earnings/event risk may warrant caution.
  - "short" mirror.
  - "neutral" when signals conflict or evidence is weak.

- confidence: "high" = multi-indicator agreement. "medium" = partial. "speculative" = conflicting/thin.

- key_observations: 2-4 bullets, each citing a specific number.

CRITICAL: Output ONLY the raw JSON object. No markdown fences, no explanation
before or after. Just the JSON starting with { and ending with }.

Note: this is a paper-trading observation only — not financial advice. Execution,
if any, is via Bitget xStocks (on-chain tokenized equity)."""


class _FetchError:
    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason


def _fetch_equity_indicators(ticker: str, _retry: bool = True) -> dict | _FetchError | None:
    """Fetch yfinance daily bars + compute technical indicators.

    Returns:
      - dict: success (indicator values + raw close for cross-validation)
      - None: soft failure (empty / insufficient data)
      - _FetchError: hard failure (import error / network exception)
    """
    try:
        import yfinance as yf
    except ImportError:
        return _FetchError("yfinance_not_installed")

    import time

    try:
        from aletheia.trading.indicators import sma, rsi, atr, bollinger, momentum_pct, zscore_last

        hist = yf.download(
            ticker,
            period=f"{_DAILY_BARS}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            show_errors=False,
        )
        if hist is None or hist.empty:
            if _retry:
                time.sleep(3)
                return _fetch_equity_indicators(ticker, _retry=False)
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
            return _fetch_equity_indicators(ticker, _retry=False)
        return _FetchError(f"exception:{e}")


def _fetch_live_price(ticker: str) -> float | None:
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


async def _fetch_xstock_price(xstock_symbol: str) -> float | None:
    """Fetch current ask/mid price for a Bitget ON-series xStock.

    Returns the price as float, or None on any failure.
    Deliberately uses a dedicated CCXTProvider(bitget, bitget) — ON-series tokens
    only list on Bitget, so the usual market data provider (binance/bybit) won't work.
    """
    from aletheia.data.ccxt_provider import CCXTProvider
    provider = CCXTProvider(exchange="bitget", fallback_exchange="bitget")
    try:
        ticker = await provider.get_ticker(xstock_symbol)
        if ticker:
            price = ticker.get("last") or ticker.get("ask") or ticker.get("bid")
            if price and float(price) > 0:
                return float(price)
    except Exception as e:
        logger.debug("xstock fetch failed for %s: %s", xstock_symbol, e)
    finally:
        await provider.close()
    return None


def _render_equity_snapshot(data: dict, xstock_price: float | None, deviation_pct: float | None) -> str:
    """Render indicator dict + optional xstock data as markdown for LLM."""
    def fmt(v, f="{:.2f}"):
        return "n/a" if v is None else f.format(v)

    t = data["ticker"]
    lines = [
        f"## {t} — Daily technical snapshot ({data['bars']} bars, yfinance authority price)",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Last close (yfinance) | {fmt(data['last'])} |",
        f"| SMA20 | {fmt(data['sma20'])} |",
        f"| SMA50 | {fmt(data['sma50'])} |",
        f"| RSI14 | {fmt(data['rsi14'], '{:.1f}')} |",
        f"| ATR14 | {fmt(data['atr14'])} |",
        f"| BB% | {fmt(data['bb_pct_b'])} |",
        f"| Mom10 | {fmt(data['momentum_10'], '{:+.2f}%')} |",
        f"| Vol z-score | {fmt(data['volume_zscore'], '{:+.2f}')} |",
    ]

    if xstock_price is not None:
        lines.append(f"| Bitget xStock price (ON-series) | {fmt(xstock_price)} |")
        if deviation_pct is not None:
            flag = ""
            if deviation_pct > _XSTOCK_DEVIATION_ALERT_PCT:
                flag = " ⚠ premium alert"
            lines.append(f"| xStock deviation from close | {deviation_pct:.2f}%{flag} |")

    return "\n".join(lines)


async def _analyze_equity(
    ticker: str,
    permission_mode: str,
    run_id: str | None,
    linked_strategy_id: str | None,
    observed_xstocks: list[str],
) -> tuple[str | None, str | None]:
    """Fetch indicators + optional xstock price + LLM read for one equity ticker.

    Returns (opportunity_id, error_str | None).
    Error strings prefixed with "hard:" are real failures; "soft:" are transient/empty.
    """
    import asyncio as _asyncio

    loop = _asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch_equity_indicators, ticker)

    if isinstance(data, _FetchError):
        log_tool(run_id, "get_equity_snapshot", "observe", False, output=ticker)
        return None, f"hard:{data.reason}:{ticker}"

    if data is None:
        log_tool(run_id, "get_equity_snapshot", "observe", False, output=ticker)
        return None, f"soft:empty_data:{ticker}"

    log_tool(run_id, "get_equity_snapshot", "observe", True, output=ticker)

    # xStock cross-validation (optional): fetch real-time on-chain proxy price
    xstock_symbol = ticker_to_xstock(ticker)
    xstock_price: float | None = None
    deviation_pct: float | None = None
    xstock_in_config = xstock_symbol in observed_xstocks if xstock_symbol else False

    if xstock_symbol and xstock_in_config:
        xstock_price = await _fetch_xstock_price(xstock_symbol)
        if xstock_price is not None and data["last"] > 0:
            # data["last"] is yfinance previous-day close; xstock_price is live.
            # During market hours the stock may have moved legitimately, so we use
            # the intraday live price from yfinance (fast_info) when available to
            # give a same-timeframe comparison. Falls back to prior close otherwise.
            ref_price = await loop.run_in_executor(None, _fetch_live_price, data["ticker"])
            ref_price = ref_price if (ref_price and ref_price > 0) else data["last"]
            deviation_pct = abs(xstock_price - ref_price) / ref_price * 100.0
            if deviation_pct > _XSTOCK_DEVIATION_ANOMALY_PCT:
                logger.warning(
                    "xStock data anomaly for %s: deviation=%.2f%% (threshold=%.1f%%), excluding from LLM",
                    ticker, deviation_pct, _XSTOCK_DEVIATION_ANOMALY_PCT,
                )
                # Still record the deviation in metadata but don't pass to LLM
                xstock_price_for_llm = None
                deviation_for_llm = None
            else:
                xstock_price_for_llm = xstock_price
                deviation_for_llm = deviation_pct
    else:
        xstock_price_for_llm = None
        deviation_for_llm = None

    snapshot_text = _render_equity_snapshot(data, xstock_price_for_llm, deviation_for_llm)

    record_step(
        run_id, f"equity_read_input:{ticker}",
        input_data=f"ticker={ticker} bars={data['bars']} xstock={'yes' if xstock_price else 'no'}",
        output_data=snapshot_text[:800],
    )

    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"Equity data for {ticker}:\n{snapshot_text}\n\nReturn the market read JSON.",
        schema=EquityRead,
        max_tokens=500,
    )
    log_tool(run_id, "llm_structured", "observe", True)

    direction = "neutral"
    confidence = "speculative"
    confidence_score: int | None = None
    rationale = ""
    observations: list[str] = []
    llm_error: str | None = None

    if err or not read:
        llm_error = err or "no_structured_read"
    else:
        d = str(read.get("direction_bias", "")).lower()
        c = str(read.get("confidence", "")).lower()
        if d in _VALID_DIRECTIONS:
            direction = d
        if c in _VALID_CONFIDENCE:
            confidence = c
        rationale = str(read.get("rationale", ""))
        cs = read.get("confidence_score")
        if isinstance(cs, (int, float)) and 0 <= cs <= 100:
            confidence_score = int(cs)
        obs = read.get("key_observations") or []
        if isinstance(obs, list):
            observations = [str(o)[:200] for o in obs[:4] if o]

    record_step(
        run_id, f"equity_read_output:{ticker}",
        input_data=None,
        output_data=f"direction={direction} confidence={confidence} score={confidence_score}",
    )

    if not is_tool_permitted("create_opportunity", permission_mode):
        return None, llm_error

    thesis_parts = []
    if rationale:
        thesis_parts.append(rationale)
    if observations:
        thesis_parts.append("Observations: " + " | ".join(observations))
    if confidence_score is not None:
        thesis_parts.append(f"score={confidence_score}/100")
    thesis = " ".join(thesis_parts) if thesis_parts else (
        f"No structured rationale (direction={direction}, confidence={confidence})"
    )

    # Build metadata: yfinance indicators + xstock cross-validation data
    meta: dict = {
        "yfinance": {
            "last": data["last"],
            "sma20": data["sma20"],
            "sma50": data["sma50"],
            "rsi14": data["rsi14"],
            "atr14": data["atr14"],
            "bb_pct_b": data["bb_pct_b"],
            "momentum_10": data["momentum_10"],
            "volume_zscore": data["volume_zscore"],
            "bars": data["bars"],
        },
    }
    if xstock_price is not None:
        meta["xstock"] = {
            "symbol": xstock_symbol,
            "price": xstock_price,
        }
        if deviation_pct is not None:
            meta["deviation_pct"] = round(deviation_pct, 4)
            if deviation_pct > _XSTOCK_DEVIATION_ALERT_PCT:
                meta["premium_alert"] = True
            if deviation_pct > _XSTOCK_DEVIATION_ANOMALY_PCT:
                meta["data_anomaly"] = True

    opp = Opportunity(
        symbol=f"{ticker}/USDT",
        market="spot",
        direction_bias=direction,
        thesis=thesis,
        confidence=confidence,
        confidence_score=confidence_score,
        source_agent="equity_observer",
        linked_strategy_id=linked_strategy_id,
        asset_class="equity",
        asset_subclass="equity_daily",
        canonical_ticker=ticker,
        execution_eligible=False,
        metadata=json.dumps(meta),
    )
    insert_opportunity(opp)
    log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
    return opp.id, llm_error


@register_agent("equity_observer")
async def run_equity_observer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Observe US equities via yfinance daily bars + Bitget xStocks real-time price."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    tickers = cfg.market_data.observed_equities or cfg.market_data.observed_stocks
    if not tickers:
        outputs.append("no_equities_configured")
        return {"outputs": outputs, "errors": errors}

    observed_xstocks: list[str] = cfg.market_data.observed_xstocks or []

    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            linked_strategy_id = s["id"]
            break

    hard_errors: list[str] = []
    soft_warnings: list[str] = []

    for ticker in tickers:
        try:
            opp_id, err = await _analyze_equity(
                ticker=ticker,
                permission_mode=permission_mode,
                run_id=run_id,
                linked_strategy_id=linked_strategy_id,
                observed_xstocks=observed_xstocks,
            )
            if opp_id:
                outputs.append(f"opportunity:{opp_id}")
            if err:
                if err.startswith("hard:"):
                    hard_errors.append(err)
                else:
                    soft_warnings.append(err)
        except Exception as e:
            hard_errors.append(f"hard:unexpected:{ticker}:{e}")

    if len(hard_errors) > len(tickers) / 2:
        errors.extend(hard_errors)
    elif hard_errors:
        outputs.append(f"warnings:{len(hard_errors)}_hard_{len(soft_warnings)}_soft")

    if soft_warnings:
        outputs.append(f"soft_skipped:{len(soft_warnings)}")

    return {"outputs": outputs, "errors": errors}
