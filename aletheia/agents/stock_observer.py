"""Stock Observer agent — analyzes US equity symbols and creates opportunities.

Uses yFinance (already a project dependency) to fetch daily OHLCV data.
Applies the same technical indicator suite as market_observer (SMA/RSI/ATR/
Bollinger/momentum) but on daily bars.

Design choices:
- asset_class = "equity" — paper_trader ignores these (observation-only for now).
- Runs only during US market hours gate in the scheduler (Mon–Fri 14:30–21:00 UTC).
  The agent itself does NOT enforce this gate — the scheduler does. Running
  outside hours returns potentially stale daily data, which is fine for analysis.
- Degrades gracefully: if yfinance is unavailable or a ticker fails, the agent
  logs an error and continues with the next ticker.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.agents import register_agent

logger = logging.getLogger("aletheia.agents.stock_observer")
from aletheia.agents.helpers import llm_structured, log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import insert_opportunity, list_strategies
from aletheia.config.loader import load_config
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy

_VALID_DIRECTIONS = {"long", "short", "neutral"}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}

# Number of daily bars to fetch for indicator computation.
# SMA50 needs 50+ bars; 90 days ~ 65 trading days gives comfortable coverage.
_DAILY_BARS = 90


class StockRead(BaseModel):
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    confidence: Literal["high", "medium", "speculative"] = "speculative"
    confidence_score: int = Field(
        50, ge=0, le=100,
        description="0=no idea, 50=balanced, 100=overwhelming evidence"
    )
    rationale: str = ""
    key_observations: list[str] = Field(
        default_factory=list,
        description="2-4 bullets citing specific numbers from the indicators"
    )


_SYSTEM_PROMPT = """\
You are an equity market observer for Aletheia paper-trading analysis.

You will receive a verified daily technical snapshot for ONE US equity ticker.
It includes: price, SMA20/50, RSI14, ATR14, Bollinger %B, 10-bar momentum,
and volume z-score. Use only the provided numbers — do not invent data.

Output a structured market read. Calibration:

- direction_bias:
  - "long" if price > SMA50, RSI 50-70 range with upward slope, positive
    momentum, above mid-Bollinger. Earnings/event risk may warrant caution.
  - "short" mirror.
  - "neutral" when signals conflict or evidence is weak.

- confidence: "high" = multi-indicator agreement. "medium" = partial. "speculative" = conflicting/thin.

- key_observations: 2-4 bullets, each citing a specific number.

CRITICAL: Output ONLY the raw JSON object. No markdown fences, no explanation
before or after, no "Here is..." preamble. Just the JSON starting with { and
ending with }.

Note: this is a paper-trading observation only — not financial advice."""


class _FetchError:
    """Sentinel distinguishing hard errors from transient empty data."""
    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason


def _fetch_stock_indicators(ticker: str, _retry: bool = True) -> dict | _FetchError | None:
    """Synchronous yfinance fetch + indicator computation for one ticker.

    Returns:
      - dict: success with indicator values
      - None: data temporarily unavailable (empty hist / insufficient bars) — soft
      - _FetchError: hard failure (import missing, network exception) — counts as real error
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
        )
        if hist is None or hist.empty:
            if _retry:
                time.sleep(3)
                return _fetch_stock_indicators(ticker, _retry=False)
            return None

        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        highs = [float(x) for x in hist["High"].dropna().tolist()]
        lows = [float(x) for x in hist["Low"].dropna().tolist()]
        volumes = [float(x) for x in hist["Volume"].dropna().tolist()]

        if len(closes) < 20:
            return None

        # Align lengths (dropna can produce different lengths per column)
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
            return _fetch_stock_indicators(ticker, _retry=False)
        return _FetchError(f"exception:{e}")


def _render_stock_snapshot(data: dict) -> str:
    """Render indicator dict as markdown for LLM."""
    def fmt(v, f="{:.2f}"):
        return "n/a" if v is None else f.format(v)

    t = data["ticker"]
    return (
        f"## {t} — Daily technical snapshot ({data['bars']} bars)\n"
        f"| Metric | Value |\n"
        f"|--------|------:|\n"
        f"| Last close | {fmt(data['last'])} |\n"
        f"| SMA20 | {fmt(data['sma20'])} |\n"
        f"| SMA50 | {fmt(data['sma50'])} |\n"
        f"| RSI14 | {fmt(data['rsi14'], '{:.1f}')} |\n"
        f"| ATR14 | {fmt(data['atr14'])} |\n"
        f"| BB% | {fmt(data['bb_pct_b'])} |\n"
        f"| Mom10 | {fmt(data['momentum_10'], '{:+.2f}%')} |\n"
        f"| Vol z-score | {fmt(data['volume_zscore'], '{:+.2f}')} |\n"
    )


async def _analyze_stock(
    ticker: str,
    permission_mode: str,
    run_id: str | None,
    linked_strategy_id: str | None,
) -> tuple[str | None, str | None]:
    """Fetch indicators + LLM read for one equity ticker.

    Returns (opportunity_id, error_str | warning_str | None).
    Error strings prefixed with "hard:" are real failures; "soft:" are transient/empty.
    """
    import asyncio as _asyncio

    loop = _asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch_stock_indicators, ticker)

    if isinstance(data, _FetchError):
        log_tool(run_id, "get_stock_snapshot", "observe", False, output=ticker)
        return None, f"hard:{data.reason}:{ticker}"

    if data is None:
        log_tool(run_id, "get_stock_snapshot", "observe", False, output=ticker)
        return None, f"soft:empty_data:{ticker}"

    log_tool(run_id, "get_stock_snapshot", "observe", True, output=ticker)
    snapshot_text = _render_stock_snapshot(data)

    record_step(
        run_id, f"stock_read_input:{ticker}",
        input_data=f"ticker={ticker} bars={data['bars']}",
        output_data=snapshot_text[:800],
    )

    read, err = await llm_structured(
        system=_SYSTEM_PROMPT,
        user=f"Equity data for {ticker}:\n{snapshot_text}\n\nReturn the market read JSON.",
        schema=StockRead,
        max_tokens=500,
    )
    log_tool(run_id, "llm_structured", "observe", True)

    direction = "neutral"
    confidence = "speculative"
    confidence_score: int | None = None
    rationale = ""
    observations: list[str] = []

    if err or not read:
        llm_error = err or "no_structured_read"
    else:
        llm_error = None
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
        run_id, f"stock_read_output:{ticker}",
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

    opp = Opportunity(
        symbol=ticker,
        market="spot",
        direction_bias=direction,
        thesis=thesis,
        confidence=confidence,
        confidence_score=confidence_score,
        source_agent="stock_observer",
        linked_strategy_id=linked_strategy_id,
        asset_class="equity",
    )
    insert_opportunity(opp)
    log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
    return opp.id, llm_error


@register_agent("stock_observer")
async def run_stock_observer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Observe US equity symbols and create equity Opportunities (observation-only)."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    cfg = load_config()
    tickers = cfg.market_data.observed_stocks
    if not tickers:
        outputs.append("no_stocks_configured")
        return {"outputs": outputs, "errors": errors}

    # Find active strategy to link observations
    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            linked_strategy_id = s["id"]
            break

    hard_errors: list[str] = []
    soft_warnings: list[str] = []

    for ticker in tickers:
        try:
            opp_id, err = await _analyze_stock(
                ticker=ticker,
                permission_mode=permission_mode,
                run_id=run_id,
                linked_strategy_id=linked_strategy_id,
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

    # Tolerance threshold: only report errors if >50% tickers had hard failures.
    # Soft warnings (empty data) never escalate to errors.
    if len(hard_errors) > len(tickers) / 2:
        errors.extend(hard_errors)
    elif hard_errors:
        outputs.append(f"warnings:{len(hard_errors)}_hard_{len(soft_warnings)}_soft")

    if soft_warnings:
        outputs.append(f"soft_skipped:{len(soft_warnings)}")

    return {"outputs": outputs, "errors": errors}
