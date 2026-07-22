"""Divergence Scanner — detects Polymarket/crypto price divergences.

When a prediction market probability moves sharply but the correlated crypto
asset's price has NOT moved proportionally, this module generates a tradeable
crypto-class Opportunity (execution_eligible=True).

Called by the scheduler immediately after prediction_observer completes.
"""

import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone, timedelta

from aletheia.agents import register_agent
from aletheia.agents.helpers import log_tool, record_step
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.data.ccxt_provider import get_market_data_provider
from aletheia.data.symbols import MARKET_SPOT
from aletheia.learning.opportunity_information_policy import POLICY_EPOCH_LABEL
from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    find_divergence_opportunity_by_source_version,
    insert_divergence_opportunity_once,
    list_strategies,
    transaction,
)
from aletheia.trading.instruments import instrument_market_to_db_market
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

# Thresholds
_MIN_PROB_CHANGE = 0.10       # |Δ24h| must exceed 10pp
_MIN_VOLUME_USD = 50_000.0    # market must have >$50k volume
_MAX_PRICE_MOVE = 3.0         # if price already moved >3%, no divergence
_MAX_CONFIDENCE_SCORE = 70    # cap — prediction markets are noisy
_MIN_CONFIDENCE_SCORE = 40
_PREDICTION_POLARITY_VERSION = "polarity-v1"

_POSITIVE_POLARITY_PATTERNS = (
    r"\b(?:rise|rally|surge|climb|gain)\b",
    r"\b(?:above|exceed|reach|hit)\b",
    r"\bbreak(?!\s+(?:below|under))\b",
)
_NEGATIVE_POLARITY_PATTERNS = (
    r"\b(?:fall|drop|decline|crash|plunge)\b",
    r"\b(?:below|under)\b",
)
_AMBIGUOUS_POLARITY_PATTERNS = (
    r"\b(?:not|never|without)\b",
    r"\b(?:won't|cannot|can't|isn't|aren't|doesn't|didn't)\b",
    r"\b(?:fail|fails|failed|failing|failure)\s+to\b",
    r"\b(?:avoid|avoids|avoided|avoiding)\b",
)
_PRICE_THRESHOLD_PATTERN = re.compile(
    r"\b(?:above|exceed|reach|hit|break|below|under)\s+"
    r"(?:a\s+price\s+of\s+)?\$?\d[\d,]*(?:\.\d+)?\s*[kmbt]?\b",
    re.IGNORECASE,
)


class _DivergenceEvidenceError(RuntimeError):
    """Producer evidence failed inside the Opportunity creation transaction."""


def _source_version(row: dict, symbol: str, market: str) -> str:
    """Stable identity for one prediction observation and one proven instrument."""
    payload = {
        "prediction_market_id": row.get("id"),
        "prediction_external_market_id": row.get("external_market_id"),
        "prediction_updated_at": row.get("updated_at"),
        "prediction_polarity_version": _PREDICTION_POLARITY_VERSION,
        "symbol": symbol,
        "market": market,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"prediction_divergence:{_PREDICTION_POLARITY_VERSION}:{digest}"


def _prediction_candidate_key(row: dict) -> str:
    """Stable identity for one prediction observation, including unmapped rows."""
    payload = {
        "prediction_market_id": row.get("id"),
        "prediction_external_market_id": row.get("external_market_id"),
        "prediction_updated_at": row.get("updated_at"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"prediction_observation:{digest}"


def _record_divergence_decision(
    *,
    row: dict,
    run_id: str | None,
    symbol: str | None,
    market_type: str,
    direction: str | None,
    decision: str,
    reason_code: str,
    opportunity_id: str | None = None,
    price_change_pct: float | None = None,
) -> str | None:
    """Persist one terminal scanner fact; return a stable retryable error on failure."""
    candidate_key = _prediction_candidate_key(row)
    try:
        from aletheia.analytics.filter_evidence import record_filter_decision

        evidence_id = record_filter_decision(
            candidate_key=candidate_key,
            candidate_kind="prediction_observation",
            parent_candidate_key=(
                f"prediction_market:{row.get('id')}" if row.get("id") else None
            ),
            run_id=run_id,
            opportunity_id=opportunity_id,
            symbol=symbol or "[unmapped]",
            market_type=market_type,
            direction=direction,
            filter_name="prediction_divergence",
            stage="divergence_scanner",
            sequence_no=0,
            decision=decision,
            reason_code=reason_code,
            inputs={
                "prediction_market_id": row.get("id"),
                "prediction_external_market_id": row.get("external_market_id"),
                "prediction_updated_at": row.get("updated_at"),
                "prediction_question": row.get("question"),
                "prediction_polarity": _prediction_polarity(
                    str(row.get("question") or "")
                ),
                "prediction_polarity_version": _PREDICTION_POLARITY_VERSION,
                "probability_change_24h": row.get("probability_change_24h"),
                "volume_24h": row.get("volume_24h"),
                "liquidity": row.get("liquidity"),
                "crypto_price_change_pct": price_change_pct,
            },
            parameters={
                "min_probability_change": _MIN_PROB_CHANGE,
                "min_volume_usd": _MIN_VOLUME_USD,
                "max_price_move": _MAX_PRICE_MOVE,
                "prediction_polarity_version": _PREDICTION_POLARITY_VERSION,
            },
            raise_on_error=True,
        )
        from aletheia.storage.sqlite import get_db

        if get_db().execute(
            "SELECT 1 FROM filter_decisions WHERE id = ?", (evidence_id,)
        ).fetchone() is None:
            raise RuntimeError("divergence_evidence_not_persisted")
        return None
    except Exception as exc:
        error_code = f"divergence_evidence_failed:{candidate_key}"
        logger.error("%s (%s)", error_code, type(exc).__name__)
        return error_code


def _source_valid_until(updated_at: str) -> str | None:
    """Expire with the source fact's two-hour freshness window, never from replay time."""
    try:
        parsed = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(hours=2)).isoformat()


def _map_question_to_symbol(question: str) -> str | None:
    """Return one exactly named asset; proxy, substring, and multi-asset reads fail."""
    matches = {
        symbol
        for keyword, symbol in _KEYWORD_SYMBOL_MAP.items()
        if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])",
            question,
            re.IGNORECASE,
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _prediction_polarity(question: str) -> int | None:
    """Map YES-probability movement to asset-price polarity, or fail closed."""
    normalized = question.lower()
    if _PRICE_THRESHOLD_PATTERN.search(normalized) is None:
        return None
    if any(re.search(pattern, normalized) for pattern in _AMBIGUOUS_POLARITY_PATTERNS):
        return None
    positive = any(re.search(pattern, normalized) for pattern in _POSITIVE_POLARITY_PATTERNS)
    negative = any(re.search(pattern, normalized) for pattern in _NEGATIVE_POLARITY_PATTERNS)
    if positive == negative:
        return None
    return 1 if positive else -1


def _compute_divergence_score(
    prob_change: float,
    price_change_pct: float,
    volume_usd: float,
    liquidity: float,
) -> int:
    """Score 0-100: how strong is the divergence signal?

    Higher when: probability moved a lot + price didn't + market is liquid.
    """
    if not all(math.isfinite(value) for value in (
        prob_change, price_change_pct, volume_usd, liquidity,
    )):
        raise ValueError("divergence evidence must be finite")
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
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
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

    try:
        for row in rows:
            question = row.get("question", "")
            symbol = _map_question_to_symbol(question)
            try:
                prob_change = float(row.get("probability_change_24h"))
                volume = float(row.get("volume_24h"))
                liquidity = float(row.get("liquidity"))
                yes_prob = float(row.get("yes_probability"))
            except (TypeError, ValueError):
                prob_change = volume = liquidity = yes_prob = math.nan
            if not all(math.isfinite(value) for value in (
                prob_change, volume, liquidity, yes_prob,
            )):
                reason = "invalid_numeric_evidence"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=MARKET_SPOT,
                    direction=None,
                    decision="rejected",
                    reason_code=reason,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip:{symbol or '[unmapped]'}:{reason}")
                continue

            # Map to tradeable symbol
            polarity = _prediction_polarity(question)
            direction = None
            if polarity is not None:
                direction = "long" if prob_change * polarity > 0 else "short"
            if not symbol:
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=None,
                    market_type=MARKET_SPOT,
                    direction=direction,
                    decision="rejected",
                    reason_code="symbol_unmapped",
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(
                        f"skip:{_prediction_candidate_key(row)}:symbol_unmapped"
                    )
                continue

            if polarity is None:
                reason = "prediction_polarity_ambiguous"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=MARKET_SPOT,
                    direction=None,
                    decision="rejected",
                    reason_code=reason,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip:{symbol}:{reason}")
                continue

            # Direction comes from the prediction fact. The current producer fetches
            # a bare-symbol ticker, whose provider contract resolves to spot. A bearish
            # view therefore has no proven shortable instrument and must not be guessed
            # into a futures market downstream.
            if direction == "short":
                reason = "short_requires_perp_instrument"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=MARKET_SPOT,
                    direction=direction,
                    decision="rejected",
                    reason_code=reason,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip:{symbol}:{reason}")
                    record_step(
                        run_id,
                        f"divergence_skip:{row.get('id') or symbol}",
                        input_data=f"symbol={symbol} direction={direction}",
                        output_data=reason,
                    )
                continue

            instrument_market = MARKET_SPOT
            db_market = instrument_market_to_db_market(instrument_market)
            source_version = _source_version(row, symbol, db_market)
            existing = find_divergence_opportunity_by_source_version(source_version)
            if existing:
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="passed",
                    reason_code="existing_opportunity_reused",
                    opportunity_id=existing.get("id"),
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip_duplicate_source_version:{source_version}")
                continue

            valid_until = _source_valid_until(row.get("updated_at"))
            if valid_until is None:
                reason = "invalid_source_updated_at"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="rejected",
                    reason_code=reason,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip:{symbol}:{reason}")
                continue

            # A bare canonical symbol is explicitly a spot ticker in CCXTProvider.
            try:
                ticker = await provider.get_ticker(symbol)
            except Exception as e:
                reason = "ticker_fetch_failed"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="error",
                    reason_code=reason,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    errors.append(
                        f"divergence_ticker_failed:{_prediction_candidate_key(row)}:"
                        f"{type(e).__name__}"
                    )
                logger.warning("ticker fetch failed for %s: %s", symbol, type(e).__name__)
                continue

            try:
                last_price = float(ticker.last_price)
                ticker_volume = float(ticker.volume_24h)
                ticker_change_pct = float(ticker.change_pct_24h)
            except (TypeError, ValueError):
                last_price = ticker_volume = ticker_change_pct = math.nan
            if not all(math.isfinite(value) for value in (
                last_price, ticker_volume, ticker_change_pct,
            )) or last_price <= 0 or ticker_volume < 0:
                reason = "invalid_numeric_evidence"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="rejected",
                    reason_code=reason,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip:{symbol}:{reason}")
                continue

            price_change_pct = abs(ticker_change_pct)

            # Divergence check: probability moved significantly but price didn't follow
            if price_change_pct > _MAX_PRICE_MOVE:
                reason = "price_already_moved"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="rejected",
                    reason_code=reason,
                    price_change_pct=ticker_change_pct,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip:{symbol}:{reason}")
                continue

            score = _compute_divergence_score(
                prob_change, ticker_change_pct, volume, liquidity,
            )

            confidence = "medium" if score >= 55 else "speculative"

            thesis = (
                f"Polymarket divergence: '{question[:80]}' YES prob "
                f"{yes_prob*100:.0f}% (Δ24h {prob_change*100:+.1f}pp, vol ${volume:,.0f}) "
                f"but {symbol} only moved {ticker_change_pct:+.1f}% — market hasn't priced in."
            )

            if not is_tool_permitted("create_opportunity", permission_mode):
                reason = "create_opportunity_not_permitted"
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="error",
                    reason_code=reason,
                    price_change_pct=ticker_change_pct,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    errors.append(
                        f"{reason}:{_prediction_candidate_key(row)}"
                    )
                continue

            opp = Opportunity(
                symbol=symbol,
                market=db_market,
                direction_bias=direction,
                thesis=thesis,
                confidence=confidence,
                confidence_score=score,
                source_agent="divergence_scanner",
                scanner_family="reversion",
                linked_strategy_id=linked_strategy_id,
                asset_class="crypto",
                execution_eligible=True,
                valid_until=valid_until,
                metadata=json.dumps({
                    "trigger": "prediction_divergence",
                    "source_version": source_version,
                    "prediction_market_id": row.get("id"),
                    "prediction_external_market_id": row.get("external_market_id"),
                    "prediction_updated_at": row.get("updated_at"),
                    "prediction_polarity": polarity,
                    "prediction_polarity_version": _PREDICTION_POLARITY_VERSION,
                    "instrument_market": instrument_market,
                    "prediction_question": question[:120],
                    "yes_probability": yes_prob,
                    "prob_change_24h": prob_change,
                    "prediction_volume_usd": volume,
                    "prediction_liquidity": liquidity,
                    "crypto_price_change_pct": ticker_change_pct,
                    "crypto_last_price": last_price,
                    "evidence_manifest": {
                        "version": "opportunity-information-integrity-v1",
                        "policy_epoch": POLICY_EPOCH_LABEL,
                        "t0": {
                            "status": "available",
                            "trigger_type": "prediction_divergence",
                            "run_id": run_id,
                            "source_version": source_version,
                            "prediction_market_id": row.get("id"),
                            "prediction_external_market_id": row.get("external_market_id"),
                            "prediction_updated_at": row.get("updated_at"),
                            "prediction_polarity": polarity,
                            "prediction_polarity_version": _PREDICTION_POLARITY_VERSION,
                            "prediction_question": question[:160],
                            "symbol": symbol,
                            "market_type": instrument_market,
                            "direction": direction,
                            "yes_probability": yes_prob,
                            "probability_change_24h": prob_change,
                            "crypto_last_price": last_price,
                            "crypto_price_change_24h_pct": ticker_change_pct,
                        },
                        "t1": {"status": "unavailable"},
                    },
                }),
            )
            try:
                # A created Opportunity and the producer's terminal fact are one
                # causal write.  An outer transaction makes the repository helper's
                # nested transaction join this unit, so an evidence outage leaves the
                # prediction observation retryable instead of releasing an unexplained
                # Opportunity downstream.
                with transaction(immediate=True):
                    # ``transaction`` intentionally joins an existing caller
                    # transaction.  A SAVEPOINT still gives this adjacent producer
                    # boundary its own rollback unit in that case.
                    from aletheia.storage.sqlite import get_db

                    db = get_db()
                    db.execute("SAVEPOINT divergence_opportunity_evidence")
                    try:
                        owner, owner_created = insert_divergence_opportunity_once(
                            opp, source_version=source_version
                        )
                        if owner_created:
                            evidence_error = _record_divergence_decision(
                                row=row,
                                run_id=run_id,
                                symbol=symbol,
                                market_type=db_market,
                                direction=direction,
                                decision="passed",
                                reason_code="opportunity_emitted",
                                opportunity_id=opp.id,
                                price_change_pct=ticker_change_pct,
                            )
                            if evidence_error:
                                raise _DivergenceEvidenceError(evidence_error)
                        db.execute("RELEASE SAVEPOINT divergence_opportunity_evidence")
                    except Exception:
                        db.execute("ROLLBACK TO SAVEPOINT divergence_opportunity_evidence")
                        db.execute("RELEASE SAVEPOINT divergence_opportunity_evidence")
                        raise
            except _DivergenceEvidenceError as exc:
                errors.append(str(exc))
                continue
            except Exception as exc:
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="error",
                    reason_code="opportunity_insert_failed",
                    opportunity_id=opp.id,
                    price_change_pct=ticker_change_pct,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    errors.append(
                        f"divergence_opportunity_insert_failed:"
                        f"{_prediction_candidate_key(row)}:{type(exc).__name__}"
                    )
                continue
            if not owner_created:
                evidence_error = _record_divergence_decision(
                    row=row,
                    run_id=run_id,
                    symbol=symbol,
                    market_type=db_market,
                    direction=direction,
                    decision="passed",
                    reason_code="existing_opportunity_reused",
                    opportunity_id=owner.get("id"),
                    price_change_pct=ticker_change_pct,
                )
                if evidence_error:
                    errors.append(evidence_error)
                else:
                    outputs.append(f"skip_duplicate_source_version:{source_version}")
                continue
            try:
                from aletheia.agent_runtime.scheduler import notify_paper_trader

                notify_paper_trader(source="divergence_scanner_opportunity")
            except Exception:
                logger.debug(
                    "Failed to notify Ranker after divergence Opportunity commit",
                    exc_info=True,
                )
            log_tool(run_id, "create_opportunity", "observe", True, output=opp.id)
            outputs.append(f"opportunity:{opp.id}")
            opps_created += 1
            logger.info(
                "divergence signal: %s %s (score=%d) from '%s'",
                direction, symbol, score, question[:60],
            )

        record_step(run_id, "divergence_scan_output",
                    input_data=None, output_data=f"opps_created={opps_created}")

    finally:
        await provider.close()  # #255: always close per-call provider

    return {"outputs": outputs, "errors": errors}
