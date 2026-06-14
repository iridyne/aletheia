"""Paper trader candidate ranker — ranked selection for paper trades.

Reproduces all existing paper_trader filters but returns ALL candidates with
a deterministic trade_score, instead of stopping at the first valid one.

Does not submit orders. Does not call Risk Engine. Does not use LLM.
Purely deterministic and testable.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone

from aletheia.analytics.factor_feedback import lookup_factor_signal
from aletheia.analytics.trade_attribution import (
    confidence_bucket,
    late_entry_bucket,
    factor_key as compute_factor_key,
)
from aletheia.config.loader import load_config
from aletheia.data.symbols import to_canonical
from aletheia.storage.repositories import (
    get_ohlcv_candles,
    get_strategy,
    get_universe_member,
    list_opportunities,
    list_strategies,
)
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy, is_paper_trading_paused

logger = logging.getLogger("aletheia.agents.paper_trader_ranker")

_CONFIDENCE_RANK = {"speculative": 0, "medium": 1, "high": 2}
_CONFIDENCE_SCORE_DEFAULT = {"speculative": 20, "medium": 50, "high": 75}


def build_paper_candidates(limit: int = 50) -> list[dict]:
    """Build and score all eligible paper trade candidates.

    Reproduces existing paper_trader gates but evaluates ALL opportunities,
    not just the first valid one. Each candidate gets a trade_score and
    optional skip_reason.
    """
    cfg = load_config()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Symbol filter setup
    filter_mode = cfg.risk.symbol_filter_mode
    filter_set = {to_canonical(s) for s in tradable_symbols_from_config(cfg)}
    if filter_mode == "blacklist":
        def _symbol_allowed(sym: str) -> bool:
            return to_canonical(sym) not in filter_set
    else:
        def _symbol_allowed(sym: str) -> bool:
            return not filter_set or to_canonical(sym) in filter_set

    min_conf = cfg.trading.min_confidence_to_trade

    candidates: list[dict] = []
    opportunities = list_opportunities(limit=limit)

    for opp in opportunities:
        candidate = _evaluate_candidate(opp, cfg, now_iso, _symbol_allowed, min_conf)
        if candidate is not None:
            candidates.append(candidate)

    # Sort by trade_score descending
    candidates.sort(key=lambda c: c.get("trade_score", -999), reverse=True)
    return candidates


def select_best_candidate(candidates: list[dict]) -> dict | None:
    """Select the highest-scoring candidate with no skip_reason."""
    for c in candidates:
        if not c.get("skip_reason"):
            return c
    return None


def _evaluate_candidate(
    opp: dict,
    cfg,
    now_iso: str,
    symbol_allowed,
    min_conf: str,
) -> dict | None:
    """Evaluate a single opportunity as a paper trade candidate.

    Returns a candidate dict with trade_score and optional skip_reason,
    or None if the opportunity should be completely excluded (wrong asset class, etc).
    """
    # Hard filter: asset class
    ac = opp.get("asset_class") or "crypto"
    if ac != "crypto":
        return None

    symbol = opp.get("symbol", "")

    # Hard filter: must be open
    if opp.get("status") != "open":
        return None

    # Hard filter: execution eligible
    if not opp.get("execution_eligible", True):
        return _make_candidate(opp, skip_reason="execution_eligible=False")

    # Hard filter: symbol allowed
    if not symbol_allowed(symbol):
        return None

    # Direction gate
    bias = (opp.get("direction_bias") or "neutral").lower()
    direction = "long" if bias in ("long", "bullish") else ("short" if bias in ("short", "bearish") else None)
    if direction is None:
        return _make_candidate(opp, skip_reason="neutral_bias")

    # Confidence gate
    confidence = (opp.get("confidence") or "speculative").lower()
    conf_ok = _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK.get(min_conf, 1)
    if not conf_ok:
        return _make_candidate(opp, skip_reason=f"confidence_below_{min_conf}:{confidence}")

    # Universe TTL gate
    universe_member = None
    if opp.get("universe_member_id"):
        universe_member = _safe_get_universe_member(opp["universe_member_id"])
        if universe_member:
            if not universe_member.get("execution_eligible", True):
                return _make_candidate(opp, skip_reason="universe_not_eligible")
            if universe_member.get("expires_at") and universe_member["expires_at"] < now_iso:
                return _make_candidate(opp, skip_reason="ttl_expired")

    # Strategy resolution
    strategy_id = opp.get("linked_strategy_id")
    strategy = None
    if strategy_id:
        strategy = _safe_get(get_strategy, strategy_id)
        if strategy and is_paper_trading_paused(strategy):
            return _make_candidate(opp, skip_reason=f"paused_strategy:{strategy_id}")
    if not strategy_id:
        for s in list_strategies(limit=50):
            if is_active_paper_strategy(s):
                strategy_id = s["id"]
                strategy = s
                break

    # RuleSet entry validation
    ruleset_entry_result = None
    strategy_has_ruleset = False
    if strategy:
        params_raw = strategy.get("parameters") or "{}"
        try:
            params = _json.loads(params_raw) if isinstance(params_raw, str) else params_raw
        except (ValueError, TypeError):
            params = {}
        ruleset_json = params.get("ruleset")
        if ruleset_json:
            strategy_has_ruleset = True
            ruleset_entry_result = _check_ruleset_entry(symbol, ruleset_json, params)
            if ruleset_entry_result is not True:
                return _make_candidate(
                    opp, skip_reason=f"ruleset_entry_not_met:{ruleset_entry_result}",
                    direction=direction, strategy_id=strategy_id,
                    strategy_has_ruleset=True, universe_member=universe_member,
                )

    # Factor feedback lookup
    candidate_for_factor = {
        "scanner_family": opp.get("scanner_family"),
        "symbol": symbol,
        "direction": direction,
        "confidence_score": opp.get("confidence_score"),
        "confidence": confidence,
        "late_entry_penalty": universe_member.get("late_entry_penalty") if universe_member else None,
    }
    factor_signal = lookup_factor_signal(candidate_for_factor)

    if factor_signal["status"] == "blocked_candidate":
        return _make_candidate(
            opp, skip_reason=f"blocked_factor:{factor_signal['reason']}",
            direction=direction, strategy_id=strategy_id,
            strategy_has_ruleset=strategy_has_ruleset,
            universe_member=universe_member, factor_signal=factor_signal,
        )

    # Compute trade score
    trade_score = _compute_trade_score(opp, universe_member, factor_signal, confidence)

    return _make_candidate(
        opp,
        direction=direction,
        strategy_id=strategy_id,
        strategy_has_ruleset=strategy_has_ruleset,
        ruleset_entry_result=ruleset_entry_result,
        universe_member=universe_member,
        factor_signal=factor_signal,
        trade_score=trade_score,
    )


def _compute_trade_score(
    opp: dict,
    universe_member: dict | None,
    factor_signal: dict,
    confidence: str,
) -> float:
    """Compute deterministic trade quality score."""
    score = 0.0

    # Confidence contribution
    conf_score = opp.get("confidence_score")
    if conf_score is not None:
        score += float(conf_score)
    else:
        score += _CONFIDENCE_SCORE_DEFAULT.get(confidence, 20)

    # Universe metadata
    if universe_member:
        score += 0.5 * float(universe_member.get("total_score") or 0)
        score += 0.5 * float(universe_member.get("tradability_score") or 0)
        score -= float(universe_member.get("late_entry_penalty") or 0)
        score -= float(universe_member.get("crowding_risk") or 0)

    # Factor feedback bonus/penalty
    factor_status = factor_signal.get("status", "watch")
    if factor_status == "preferred":
        score += 20
    elif factor_status == "active":
        score += 5
    elif factor_status == "watch":
        score -= 5

    # Risk flag penalties
    risk_flags = []
    if universe_member:
        rf = universe_member.get("risk_flags")
        if isinstance(rf, str):
            try:
                risk_flags = _json.loads(rf)
            except (ValueError, TypeError):
                pass
        elif isinstance(rf, list):
            risk_flags = rf

    for flag in risk_flags:
        if flag == "late_entry":
            score -= 15
        elif flag == "crowded":
            score -= 10
        elif flag == "wide_spread":
            score -= 10
        else:
            score -= 5

    return round(score, 2)


def _check_ruleset_entry(symbol: str, ruleset_json, params: dict) -> bool | None:
    """Evaluate RuleSet entry signal. Returns True/False/None."""
    try:
        from aletheia.strategy.dsl import RuleSet
        from aletheia.strategy.evaluator import BarData, evaluate_at_bar

        ruleset = RuleSet.from_json(ruleset_json)
        tf = params.get("timeframe") or "1h"
        candles = get_ohlcv_candles(symbol, tf, limit=200)
        if not candles:
            return None
        bars = [
            BarData(
                ts=int(c["ts"]), open=float(c["open"]),
                high=float(c["high"]), low=float(c["low"]),
                close=float(c["close"]), volume=float(c["volume"]),
            )
            for c in candles
        ]
        sig = evaluate_at_bar(ruleset, bars, len(bars) - 1)
        return sig.get("entry")
    except Exception as e:
        logger.debug("RuleSet check failed for %s: %s", symbol, e)
        return None


def _make_candidate(
    opp: dict,
    skip_reason: str | None = None,
    direction: str | None = None,
    strategy_id: str | None = None,
    strategy_has_ruleset: bool = False,
    ruleset_entry_result=None,
    universe_member: dict | None = None,
    factor_signal: dict | None = None,
    trade_score: float = -999.0,
) -> dict:
    """Assemble a candidate dict."""
    return {
        # Opportunity fields
        "opportunity_id": opp.get("id"),
        "symbol": opp.get("symbol"),
        "direction_bias": opp.get("direction_bias"),
        "confidence": opp.get("confidence"),
        "confidence_score": opp.get("confidence_score"),
        "source_agent": opp.get("source_agent"),
        "scanner_family": opp.get("scanner_family"),
        "universe_member_id": opp.get("universe_member_id"),
        "linked_strategy_id": opp.get("linked_strategy_id"),
        # Resolved fields
        "direction": direction,
        "strategy_id": strategy_id,
        "strategy_has_ruleset": strategy_has_ruleset,
        "ruleset_entry_result": ruleset_entry_result,
        # Universe metadata
        "universe_rank": universe_member.get("rank") if universe_member else None,
        "universe_total_score": universe_member.get("total_score") if universe_member else None,
        "tradability_score": universe_member.get("tradability_score") if universe_member else None,
        "late_entry_penalty": universe_member.get("late_entry_penalty") if universe_member else None,
        "crowding_risk": universe_member.get("crowding_risk") if universe_member else None,
        "risk_flags": _parse_risk_flags(universe_member),
        # Factor feedback
        "factor_key": factor_signal.get("factor_key") if factor_signal else None,
        "factor_status": factor_signal.get("status") if factor_signal else None,
        "factor_expectancy": factor_signal.get("expectancy") if factor_signal else None,
        "factor_trade_count": factor_signal.get("trade_count") if factor_signal else None,
        # Scoring
        "trade_score": trade_score,
        "skip_reason": skip_reason,
    }


def _parse_risk_flags(universe_member: dict | None) -> list:
    if not universe_member:
        return []
    rf = universe_member.get("risk_flags")
    if isinstance(rf, list):
        return rf
    if isinstance(rf, str):
        try:
            return _json.loads(rf)
        except (ValueError, TypeError):
            return []
    return []


def _safe_get(fn, key: str | None):
    if not key:
        return None
    try:
        return fn(key)
    except Exception:
        return None


def _safe_get_universe_member(member_id: str) -> dict | None:
    try:
        return get_universe_member(member_id)
    except Exception:
        return None
