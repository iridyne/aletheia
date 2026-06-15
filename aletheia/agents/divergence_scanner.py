"""Divergence Scanner — detects Polymarket/crypto price divergences.

When a prediction market probability moves sharply but the correlated crypto
asset's price has NOT moved proportionally, this module generates a tradeable
crypto-class Opportunity (execution_eligible=True).

Called by the scheduler immediately after prediction_observer completes.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.data.ccxt_provider import get_market_data_provider
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    insert_opportunity, list_strategies,
)
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy

logger = logging.getLogger("aletheia.divergence_scanner")

# Keyword → tradeable symbol mapping.
# Each entry: keyword found in Polymarket question → canonical symbol
_KEYWORD_SYMBOL_MAP: dict[str, str] = {
    "bitcoin": "BTC/USDT",
    "btc": "BTC/USDT",
    "ethereum": "ETH/USDT",
    "eth": "ETH/USDT",
    "solana": "SOL/USDT",
    "sol": "SOL/USDT",
    "xrp": "XRP/USDT",
    "ripple": "XRP/USDT",
    "dogecoin": "DOGE/USDT",
    "doge": "DOGE/USDT",
    "cardano": "ADA/USDT",
    "ada": "ADA/USDT",
    "bnb": "BNB/USDT",
    "avalanche": "AVAX/USDT",
    "avax": "AVAX/USDT",
    "chainlink": "LINK/USDT",
    "link": "LINK/USDT",
    "polygon": "POL/USDT",
    "matic": "POL/USDT",
}

# Broad crypto keywords that map to BTC as a proxy
_BROAD_CRYPTO_KEYWORDS = {"crypto", "cryptocurrency", "digital asset", "defi"}

# Thresholds
_MIN_PROB_CHANGE = 0.10       # |Δ24h| must exceed 10pp
_MIN_VOLUME_USD = 50_000.0    # market must have >$50k volume
_MAX_PRICE_MOVE = 3.0         # if price already moved >3%, no divergence
_MAX_CONFIDENCE_SCORE = 70    # cap — prediction markets are noisy
_MIN_CONFIDENCE_SCORE = 40


def _map_question_to_symbol(question: str) -> str | None:
    """Extract the most specific tradeable symbol from a Polymarket question."""
    q = question.lower()
    # Check specific coins first (longer keywords first for priority)
    for keyword, symbol in sorted(_KEYWORD_SYMBOL_MAP.items(), key=lambda x: -len(x[0])):
        if keyword in q:
            return symbol
    # Broad crypto → BTC proxy
    if any(kw in q for kw in _BROAD_CRYPTO_KEYWORDS):
        return "BTC/USDT"
    return None


def _compute_divergence_score(
    prob_change: float,
    price_change_pct: float,
    volume_usd: float,
    liquidity: float,
) -> int:
    """Score 0-100: how strong is the divergence signal?

    Higher when: probability moved a lot + price didn't + market is liquid.
    """
    # Base: proportional to probability magnitude
    base = min(40, int(abs(prob_change) * 200))  # 10pp=20, 20pp=40

    # Divergence bonus: price didn't follow
    price_ratio = abs(price_change_pct) / max(1.0, abs(prob_change) * 100)
    divergence_bonus = max(0, int(20 * (1.0 - price_ratio)))

    # Liquidity bonus: more liquid = more trustworthy signal
    liq_bonus = 0
    if liquidity > 100_000:
        liq_bonus = 10
    elif liquidity > 50_000:
        liq_bonus = 5

    raw = base + divergence_bonus + liq_bonus
    return max(_MIN_CONFIDENCE_SCORE, min(_MAX_CONFIDENCE_SCORE, raw))


@register_agent("divergence_scanner")
async def run_divergence_scanner(
    permission_mode: str = "Observe",
    run_id: str | None = None,
    **kwargs,
) -> dict:
    """Scan recent prediction markets for crypto price divergences."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("get_market_snapshot", permission_mode):
        errors.append("get_market_snapshot not permitted")
        return {"outputs": outputs, "errors": errors}

    # Step 1: Fetch recent prediction markets with high probability moves
    from aletheia.storage.repositories import _select_all
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    rows = _select_all(
        "prediction_markets",
        where="abs(probability_change_24h) >= ? AND volume_24h >= ? AND updated_at >= ?",
        params=(_MIN_PROB_CHANGE, _MIN_VOLUME_USD, cutoff),
        order="abs(probability_change_24h) DESC",
        limit=20,
    )

    if not rows:
        outputs.append("no_qualifying_markets")
        return {"outputs": outputs, "errors": errors}

    record_step(run_id, "divergence_scan_input",
                input_data=f"qualifying_markets={len(rows)}", output_data=None)

    # Step 2: For each qualifying market, check crypto price divergence
    linked_strategy_id = None
    for s in list_strategies(limit=50):
        if is_active_paper_strategy(s):
            linked_strategy_id = s["id"]
            break

    provider = get_market_data_provider()
    opps_created = 0

    for row in rows:
        question = row.get("question", "")
        prob_change = float(row.get("probability_change_24h", 0))
        volume = float(row.get("volume_24h", 0))
        liquidity = float(row.get("liquidity", 0))
        yes_prob = float(row.get("yes_probability", 0))

        # Map to tradeable symbol
        symbol = _map_question_to_symbol(question)
        if not symbol:
            continue

        # Fetch current ticker for the crypto asset
        try:
            ticker = await provider.get_ticker(symbol)
        except Exception as e:
            logger.debug("ticker fetch failed for %s: %s", symbol, e)
            continue

        price_change_pct = abs(ticker.change_pct_24h)

        # Divergence check: probability moved significantly but price didn't follow
        if price_change_pct > _MAX_PRICE_MOVE:
            continue  # Price already moved — no divergence

        # Direction: probability rising = bullish for crypto, falling = bearish
        direction = "long" if prob_change > 0 else "short"

        score = _compute_divergence_score(prob_change, ticker.change_pct_24h, volume, liquidity)

        confidence = "medium" if score >= 55 else "speculative"

        thesis = (
            f"Polymarket divergence: '{question[:80]}' YES prob "
            f"{yes_prob*100:.0f}% (Δ24h {prob_change*100:+.1f}pp, vol ${volume:,.0f}) "
            f"but {symbol} only moved {ticker.change_pct_24h:+.1f}% — market hasn't priced in."
        )

        if not is_tool_permitted("create_opportunity", permission_mode):
            continue

        opp = Opportunity(
            symbol=symbol,
            market="crypto",
            direction_bias=direction,
            thesis=thesis,
            confidence=confidence,
            confidence_score=score,
            source_agent="divergence_scanner",
            linked_strategy_id=linked_strategy_id,
            asset_class="crypto",
            execution_eligible=True,
            metadata=json.dumps({
                "trigger": "prediction_divergence",
                "prediction_question": question[:120],
                "yes_probability": yes_prob,
                "prob_change_24h": prob_change,
                "prediction_volume_usd": volume,
                "prediction_liquidity": liquidity,
                "crypto_price_change_pct": ticker.change_pct_24h,
                "crypto_last_price": ticker.last_price,
            }),
        )
        insert_opportunity(opp)
        log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
        outputs.append(f"opportunity:{opp.id}")
        opps_created += 1
        logger.info(
            "divergence signal: %s %s (score=%d) from '%s'",
            direction, symbol, score, question[:60],
        )

    record_step(run_id, "divergence_scan_output",
                input_data=None, output_data=f"opps_created={opps_created}")

    return {"outputs": outputs, "errors": errors}
