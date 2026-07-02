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
from aletheia.analytics.strategy_evidence import _classify_evidence_status
from aletheia.config.loader import load_config
from aletheia.data.symbols import to_canonical
from aletheia.storage.repositories import (
    get_ohlcv_candles,
    get_strategy,
    get_universe_member,
    list_opportunities,
    list_strategies,
)
from aletheia.knowledge.guards import evaluate_guards, load_trading_guards, GuardEvaluation
from aletheia.regime.detector import get_current_regime, MarketRegime
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy, is_paper_trading_paused

logger = logging.getLogger("aletheia.agents.paper_trader_ranker")

_CONFIDENCE_RANK = {"speculative": 0, "medium": 1, "high": 2}
_CONFIDENCE_SCORE_DEFAULT = {"speculative": 20, "medium": 50, "high": 75}

# Evidence-based strategy gates (not_executable is NOT a block — strategies
# without a RuleSet can still trade from LLM signals; the entry validation
# handles the no-ruleset case independently)
_EVIDENCE_BLOCK_STATUSES = {"backtest_killed"}
_EVIDENCE_PENALTY_STATUSES = {"paper_failing": -30, "review_needed": -10, "needs_backtest": -5}
_EVIDENCE_BONUS_STATUSES = {"paper_promising": 15}


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

    global_venue = cfg.exchange.paper_execution_mode

    min_conf = cfg.trading.min_confidence_to_trade

    candidates: list[dict] = []
    opportunities = list_opportunities(limit=limit)

    for opp in opportunities:
        candidate = _evaluate_candidate(opp, cfg, now_iso, _symbol_allowed, min_conf, global_venue)
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
    global_venue: str = "bitget_demo",
) -> dict | None:
    """Evaluate a single opportunity as a paper trade candidate.

    Returns a candidate dict with trade_score and optional skip_reason,
    or None if the opportunity should be completely excluded (wrong asset class, etc).
    """
    # Hard filter: asset class
    ac = opp.get("asset_class") or "crypto"
    if ac == "equity":
        # Exclude closed/non-open equity opps entirely (same guard as crypto path below).
        # Without this, closed equity opps stay status='open' indefinitely (equity_shadow_track
        # is a permanent skip, so paper_trader marks them reviewed — but only after they reach
        # build_paper_candidates at least once). Pre-filtering here avoids accumulation.
        if opp.get("status") != "open":
            return None
        # Shadow track: score but never execute. Run through direction + confidence to
        # produce a meaningful score for monitoring; skip crypto-specific pipeline steps.
        bias = (opp.get("direction_bias") or "neutral").lower()
        direction = "long" if bias in ("long", "bullish") else ("short" if bias in ("short", "bearish") else None)
        score = float(opp.get("confidence_score") or 0)
        return _make_candidate(opp, skip_reason="equity_shadow_track", direction=direction, trade_score=score)
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

    # Strategy resolution — must match opportunity symbol or be generic (no symbol field)
    strategy_id = opp.get("linked_strategy_id")
    strategy = None
    if strategy_id:
        strategy = _safe_get(get_strategy, strategy_id)
        if strategy:
            if is_paper_trading_paused(strategy):
                return _make_candidate(opp, skip_reason=f"paused_strategy:{strategy_id}")
            # Verify linked strategy actually targets this symbol (or is generic)
            params_raw = strategy.get("parameters") or "{}"
            try:
                _p = _json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            except (ValueError, TypeError):
                _p = {}
            _strat_sym = _p.get("symbol")
            if _strat_sym and to_canonical(_strat_sym) != to_canonical(symbol):
                strategy_id = None
                strategy = None
    if not strategy_id:
        for s in list_strategies(limit=50):
            if not is_active_paper_strategy(s):
                continue
            s_params_raw = s.get("parameters") or "{}"
            try:
                s_params = _json.loads(s_params_raw) if isinstance(s_params_raw, str) else s_params_raw
            except (ValueError, TypeError):
                s_params = {}
            s_sym = s_params.get("symbol")
            if not s_sym or to_canonical(s_sym) == to_canonical(symbol):
                strategy_id = s["id"]
                strategy = s
                break

    # Venue-aware symbol filter: apply OKX demo filter when resolved venue is okx_demo.
    # Use opp market_type so swap-only symbols don't pass a perp candidate.
    resolved_venue = (strategy.get("execution_venue") if strategy else None) or global_venue
    if resolved_venue == "okx_demo":
        try:
            from aletheia.execution.okx_broker import _get_demo_symbols
            _opp_market_type = (opp.get("market") or "usdt_futures").lower()
            _okx_supported = _get_demo_symbols(_opp_market_type)
            if _okx_supported is not None and to_canonical(symbol) not in _okx_supported:
                return _make_candidate(opp, skip_reason="okx_demo_symbol_not_supported",
                                       direction=direction, strategy_id=strategy_id)
        except Exception:
            pass

    # Regime-aware strategy filtering (P0: direction_affinity + applicable_regimes)
    regime = get_current_regime()
    if strategy:
        regime_skip = _check_strategy_regime_fit(strategy, direction, regime)
        if regime_skip:
            return _make_candidate(
                opp, skip_reason=regime_skip,
                direction=direction, strategy_id=strategy_id,
                universe_member=universe_member,
            )

        # Market-type affinity (Round 2 perp enablement): a spot-only strategy must
        # not take a perp opportunity, nor a futures-only strategy a spot one. "both"
        # (default) matches either. opp.market is "spot" | "usdt_futures".
        mta = (strategy.get("market_type_affinity") or "both").lower()
        opp_market = (opp.get("market") or "spot").lower()
        if mta not in ("both", "") and mta != opp_market:
            return _make_candidate(
                opp, skip_reason=f"market_type_mismatch:{mta}!={opp_market}",
                direction=direction, strategy_id=strategy_id,
                universe_member=universe_member,
            )

    # Strategy evidence gate (backtest + paper PnL)
    strategy_evidence = None
    if strategy:
        strategy_evidence = _lookup_strategy_evidence(strategy)
        ev_status = strategy_evidence.get("evidence_status")
        if ev_status in _EVIDENCE_BLOCK_STATUSES:
            return _make_candidate(
                opp, skip_reason=f"strategy_evidence:{ev_status}",
                direction=direction, strategy_id=strategy_id,
                universe_member=universe_member,
                strategy_evidence=strategy_evidence,
            )

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
        # Symbol-specific strategies: only apply their RuleSet to the target symbol.
        # A strategy designed for DEXE/USDT must not block ADA/USDT opportunities.
        strategy_target = params.get("symbol")
        ruleset_applies = not strategy_target or to_canonical(strategy_target) == to_canonical(symbol)
        if ruleset_json and ruleset_applies:
            strategy_has_ruleset = True
            ruleset_entry_result = _check_ruleset_entry(symbol, ruleset_json, params)
            if ruleset_entry_result is not True:
                return _make_candidate(
                    opp, skip_reason=f"ruleset_entry_not_met:{ruleset_entry_result}",
                    direction=direction, strategy_id=strategy_id,
                    strategy_has_ruleset=True, universe_member=universe_member,
                    strategy_evidence=strategy_evidence,
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

    # P1-4: hard_blocked is a hard reject — high confidence cannot override a confirmed loser
    if factor_signal.get("status") == "hard_blocked":
        return _make_candidate(
            opp, skip_reason="factor_hard_blocked",
            direction=direction, strategy_id=strategy_id,
            factor_signal=factor_signal,
            universe_member=universe_member,
        )

    # Guard evaluation (deterministic, no LLM)
    guard_eval = _evaluate_guards_safe(
        opp, universe_member, factor_signal, direction, confidence,
        strategy_has_ruleset, ruleset_entry_result, strategy_evidence,
    )
    if guard_eval and guard_eval.blocked:
        blocked_ids = ",".join(h.guard_id for h in guard_eval.hits if h.block_candidate)
        return _make_candidate(
            opp, skip_reason=f"guard_blocked:{blocked_ids}",
            direction=direction, strategy_id=strategy_id,
            strategy_has_ruleset=strategy_has_ruleset,
            universe_member=universe_member, factor_signal=factor_signal,
            strategy_evidence=strategy_evidence, guard_eval=guard_eval,
        )

    # Compute trade score
    trade_score = _compute_trade_score(
        opp, universe_member, factor_signal, confidence, strategy_evidence,
        strategy_has_ruleset=strategy_has_ruleset, direction=direction,
    )

    # Apply guard penalty
    if guard_eval:
        trade_score -= guard_eval.total_penalty

    return _make_candidate(
        opp,
        direction=direction,
        strategy_id=strategy_id,
        strategy_has_ruleset=strategy_has_ruleset,
        ruleset_entry_result=ruleset_entry_result,
        universe_member=universe_member,
        factor_signal=factor_signal,
        trade_score=trade_score,
        strategy_evidence=strategy_evidence,
        guard_eval=guard_eval,
    )


_SCANNER_REGIME_SCORING_DEFAULTS = {
    "bear_short": 20.0, "bear_long": -25.0,
    "bull_long": 20.0, "bull_short": -20.0,
    "volatile_penalty": 10.0,
    "sideways_trend_penalty": 25.0, "sideways_reversion_bonus": 15.0,
}


def _scanner_regime_scoring() -> dict:
    """Config-driven scanner-family regime score deltas (P1b).

    Reads cfg.universe.scanner_regime_scoring; falls back to the hardcoded
    defaults so a missing/partial config never changes the prior behavior.
    Merged over defaults so a partial override only touches the keys it sets.
    """
    try:
        from aletheia.config.loader import load_config
        cfg_vals = getattr(load_config().universe, "scanner_regime_scoring", None) or {}
    except Exception:
        cfg_vals = {}
    return {**_SCANNER_REGIME_SCORING_DEFAULTS, **cfg_vals}


def _compute_trade_score(
    opp: dict,
    universe_member: dict | None,
    factor_signal: dict,
    confidence: str,
    strategy_evidence: dict | None = None,
    *,
    strategy_has_ruleset: bool = False,
    direction: str | None = None,
) -> float:
    """Compute deterministic trade quality score."""
    score = 0.0

    # P0-1: Confidence contribution (clip-normalized: max 50 pts, was 100)
    # Prevents raw 0-100 score from dominating regime/factor signals (Round 1 direction bias).
    _clip_norm = lambda v, lo, hi: (min(max(float(v or 0), lo), hi) - lo) / (hi - lo + 1e-8)

    conf_score = opp.get("confidence_score")
    if conf_score is not None:
        score += 50 * _clip_norm(conf_score, 0, 100)
    else:
        score += _CONFIDENCE_SCORE_DEFAULT.get(confidence, 20)

    # Universe metadata (P0-1: clip-normalize universe scores)
    if universe_member:
        score += 25 * _clip_norm(universe_member.get("total_score") or 0, 0, 100)
        score += 10 * _clip_norm(universe_member.get("tradability_score") or 0, 0, 100)
        score -= float(universe_member.get("late_entry_penalty") or 0)
        score -= float(universe_member.get("crowding_risk") or 0)

    # P0-3: Factor feedback — IC-proxy continuous weighting (replaces discrete ±5/±20 jumps)
    factor_status = factor_signal.get("status", "watch")
    factor_win_rate = factor_signal.get("win_rate")
    factor_trade_count = factor_signal.get("trade_count", 0)

    if factor_status == "blocked_candidate":
        score -= 50
    else:
        # IC proxy: (win_rate - 0.5) * 2, range [-1, +1]; max ±20 pts
        # Sample weight: ramps to full weight at 10 trades (avoids noise from 3-trade factors)
        if factor_win_rate is not None and factor_trade_count >= 3:
            ic_proxy = (factor_win_rate - 0.5) * 2
            sample_weight = min(1.0, factor_trade_count / 10.0)
            score += 20 * ic_proxy * sample_weight
        else:
            score -= 5  # no history: mild penalty (was 0 for watch)

    # Strategy evidence bonus/penalty (backtest + paper PnL attribution)
    if strategy_evidence:
        ev_status = strategy_evidence.get("evidence_status")
        if ev_status in _EVIDENCE_PENALTY_STATUSES:
            score += _EVIDENCE_PENALTY_STATUSES[ev_status]
        elif ev_status in _EVIDENCE_BONUS_STATUSES:
            score += _EVIDENCE_BONUS_STATUSES[ev_status]

        # Paper drift penalty: underperforming = extra penalty
        drift_status = strategy_evidence.get("drift_status")
        if drift_status == "paper_underperforming":
            score -= 15
        elif drift_status == "paper_confirming":
            score += 10

    # P0-5: Regime-aware scoring with smooth confidence weighting (no 0.5 cliff).
    # P0-2: Sideways branch added — trend-family scanners penalised in sideways (CTA lit.).
    # P1b (2026-07-02): deltas are config-driven (cfg.universe.scanner_regime_scoring),
    # defaults reproduce the prior hardcoded values EXACTLY. This is a SOFT, scanner-
    # family-level signal applied to ALL candidates including strategy-less ones — NOT a
    # hard block (owner directive: no low-dim hard interception). Locked by SP-S06/SP-S07.
    _srs = _scanner_regime_scoring()
    _BASE_REGIME_DELTA = {
        ("bear", "short"): _srs["bear_short"],
        ("bear", "long"):  _srs["bear_long"],
        ("bull", "long"):  _srs["bull_long"],
        ("bull", "short"): _srs["bull_short"],
    }
    _TREND_FAMILIES = {"breakout", "momentum", "ma_crossover", "trend"}

    regime = get_current_regime()
    if regime.state in ("bear", "bull"):
        delta = _BASE_REGIME_DELTA.get((regime.state, direction), 0)
        weight = regime.confidence ** 1.5  # smooth: avoids hard cliff at 0.5
        score += delta * weight
    elif regime.state == "volatile":
        score -= _srs["volatile_penalty"] * (regime.confidence ** 1.5)
    elif regime.state == "sideways" and regime.confidence > 0.6:
        scanner = (opp.get("scanner_family") or "").lower()
        if any(t in scanner for t in _TREND_FAMILIES):
            score -= _srs["sideways_trend_penalty"]  # trend-following worst in sideways
        elif "reversion" in scanner or "arb" in scanner:
            score += _srs["sideways_reversion_bonus"]  # mean-reversion benefits from sideways

    # P2: Executable RuleSet bonus — verified strategies get preference
    if strategy_has_ruleset:
        score += 15

    # P1: Lesson-aware scoring — penalty for symbol+direction with negative experience
    score += _lesson_penalty(opp.get("symbol"), direction, opp.get("scanner_family"))

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

    # Trading Memo scoring — historical performance insights as soft preference
    score += _memo_scoring(opp.get("symbol"), direction, regime)

    return round(score, 2)


def _lesson_penalty(symbol: str | None, direction: str | None, scanner_family: str | None) -> float:
    """Query negative lessons for this symbol+direction. Returns penalty (negative or zero)."""
    if not symbol:
        return 0.0
    try:
        from aletheia.knowledge.retrieval import retrieve_lessons
        query_parts = [symbol]
        if direction:
            query_parts.append(direction)
        if scanner_family:
            query_parts.append(scanner_family)
        query = " ".join(query_parts)
        # B1 (LEARNING_LOOP_AUDIT §R3/B1): only score against trade_case_study —
        # real closed-trade cases with attributed outcome. post_trade_review was
        # the hallucination source (auto-upgraded to high_confidence on any fill,
        # 1900+ rows of templated text) that flooded this retrieval and produced
        # uncontrolled false penalties via section-title keyword hits. Removed.
        lessons = retrieve_lessons(query, k=3, item_types=["trade_case_study"])
        if not lessons:
            return 0.0
        penalty = 0.0
        for lesson in lessons:
            snippet_lower = lesson.snippet.lower()
            if any(neg in snippet_lower for neg in ("loss", "失败", "failed", "stopped", "drawdown")):
                penalty -= 8.0 * lesson.score
        return max(-25.0, penalty)
    except Exception:
        return 0.0


def _memo_scoring(symbol: str | None, direction: str | None, regime: MarketRegime) -> float:
    """Apply TradingMemo insights as soft scoring adjustments (±45 max)."""
    try:
        from aletheia.analytics.insight_synthesizer import get_latest_memo
        memo = get_latest_memo()
        if not memo:
            return 0.0

        adj = 0.0

        # Regime × direction alignment (±12)
        for rule in memo.get("regime_playbook", []):
            if rule["regime"] == regime.state and rule["direction"] == direction:
                if rule["edge"] == "strong":
                    adj += 12
                elif rule["edge"] == "weak":
                    adj -= 12
                elif rule["edge"] == "contra":
                    adj -= 20
                break

        # Symbol affinity (±8)
        if symbol:
            for sr in memo.get("symbol_affinity", []):
                if sr["symbol"] == symbol:
                    if sr["status"] == "hot":
                        adj += 8
                    elif sr["status"] == "cold":
                        adj -= 8
                    break

        # Anti-pattern penalty (-15)
        if direction and regime.state:
            pattern_key = f"{direction} in {regime.state}"
            for ap in memo.get("anti_patterns", []):
                if ap["pattern"] == pattern_key:
                    adj -= 15
                    break

        return max(-45.0, min(45.0, adj))
    except Exception:
        return 0.0


def _check_strategy_regime_fit(
    strategy: dict, direction: str | None, regime: MarketRegime
) -> str | None:
    """Check if strategy is compatible with current regime. Returns skip reason or None."""
    params_raw = strategy.get("parameters") or "{}"
    try:
        params = _json.loads(params_raw) if isinstance(params_raw, str) else params_raw
    except (ValueError, TypeError):
        params = {}

    # Check direction_affinity vs regime
    affinity = strategy.get("direction_affinity") or params.get("direction_affinity") or "both"
    if regime.confidence > 0.6:
        if regime.state == "bear" and affinity == "long":
            return f"regime_mismatch:bear_vs_long_only(conf={regime.confidence:.2f})"
        if regime.state == "bull" and affinity == "short":
            return f"regime_mismatch:bull_vs_short_only(conf={regime.confidence:.2f})"

    # Check applicable_regimes
    applicable = strategy.get("applicable_regimes") or params.get("applicable_regimes") or "all"
    if applicable != "all":
        try:
            regime_list = _json.loads(applicable) if isinstance(applicable, str) else applicable
        except (ValueError, TypeError):
            regime_list = None
        if regime_list and isinstance(regime_list, list):
            if regime.state not in regime_list:
                return f"regime_not_applicable:{regime.state} not in {regime_list}"

    return None


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


def _evaluate_guards_safe(
    opp: dict,
    universe_member: dict | None,
    factor_signal: dict | None,
    direction: str | None,
    confidence: str | None,
    strategy_has_ruleset: bool,
    ruleset_entry_result,
    strategy_evidence: dict | None,
) -> GuardEvaluation | None:
    """Build guard context and evaluate. Returns None on failure (never crashes ranker)."""
    try:
        cfg = load_config()
        guard_cfg = getattr(cfg, "guard", None)
        if guard_cfg and (not guard_cfg.enabled or not guard_cfg.enforce_in_paper_ranker):
            return None

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

        ctx = {
            "symbol": opp.get("symbol"),
            "asset_class": "crypto",
            "direction": direction,
            "scanner_family": opp.get("scanner_family"),
            "confidence": confidence,
            "confidence_score": opp.get("confidence_score"),
            "confidence_bucket": confidence,
            "late_entry_penalty": universe_member.get("late_entry_penalty") if universe_member else None,
            "late_entry_bucket": universe_member.get("late_entry_bucket") if universe_member else None,
            "tradability_score": universe_member.get("tradability_score") if universe_member else None,
            "universe_total_score": universe_member.get("total_score") if universe_member else None,
            "crowding_risk": universe_member.get("crowding_risk") if universe_member else None,
            "risk_flags": risk_flags,
            "factor_status": factor_signal.get("status") if factor_signal else None,
            "factor_expectancy": factor_signal.get("expectancy") if factor_signal else None,
            "factor_trade_count": factor_signal.get("trade_count") if factor_signal else None,
            "strategy_has_ruleset": strategy_has_ruleset,
            "ruleset_entry_result": ruleset_entry_result,
            "strategy_drift_status": strategy_evidence.get("drift_status") if strategy_evidence else None,
            "paper_expectancy": strategy_evidence.get("paper_expectancy") if strategy_evidence else None,
            "backtest_expectancy": strategy_evidence.get("backtest_expectancy") if strategy_evidence else None,
        }

        max_penalty = guard_cfg.max_score_penalty if guard_cfg else 40.0
        result = evaluate_guards(ctx, scope="paper_entry")

        if result.total_penalty > max_penalty:
            result.total_penalty = max_penalty

        return result
    except Exception as e:
        logger.warning("Guard evaluation failed (non-fatal): %s", e)
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
    strategy_evidence: dict | None = None,
    guard_eval=None,
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
        "atr_pct": universe_member.get("atr_pct") if universe_member else None,
        "risk_flags": _parse_risk_flags(universe_member),
        # Factor feedback
        "factor_key": factor_signal.get("factor_key") if factor_signal else None,
        "factor_status": factor_signal.get("status") if factor_signal else None,
        "factor_expectancy": factor_signal.get("expectancy") if factor_signal else None,
        "factor_trade_count": factor_signal.get("trade_count") if factor_signal else None,
        # Strategy evidence (backtest + paper PnL attribution)
        "evidence_status": strategy_evidence.get("evidence_status") if strategy_evidence else None,
        "backtest_expectancy": strategy_evidence.get("backtest_expectancy") if strategy_evidence else None,
        "paper_expectancy": strategy_evidence.get("paper_expectancy") if strategy_evidence else None,
        "paper_trade_count": strategy_evidence.get("paper_trade_count") if strategy_evidence else None,
        "drift_status": strategy_evidence.get("drift_status") if strategy_evidence else None,
        # Guard metadata
        "guard_blocked": guard_eval.blocked if guard_eval else False,
        "guard_penalty": guard_eval.total_penalty if guard_eval else 0.0,
        "matched_guard_ids": [h.guard_id for h in guard_eval.hits] if guard_eval else [],
        "guard_reasons": [h.reason for h in guard_eval.hits] if guard_eval else [],
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


def _lookup_strategy_evidence(strategy: dict) -> dict:
    """Lightweight per-strategy evidence lookup for ranker scoring.

    Returns {evidence_status, backtest_expectancy, paper_expectancy, drift_status}.
    Avoids the full build_strategy_evidence_snapshot() cost by doing minimal queries.
    """
    result = {
        "evidence_status": None,
        "backtest_expectancy": None,
        "backtest_profit_factor": None,
        "paper_expectancy": None,
        "paper_trade_count": 0,
        "drift_status": None,
    }
    try:
        params_raw = strategy.get("parameters") or "{}"
        params = _json.loads(params_raw) if isinstance(params_raw, str) else params_raw
        has_ruleset = bool(params.get("ruleset"))
        compile_status = "ok" if has_ruleset else "no_ruleset"
        if has_ruleset:
            try:
                from aletheia.strategy.dsl import RuleSet
                RuleSet.from_json(params["ruleset"])
            except Exception:
                compile_status = "compile_error"

        # Fetch latest backtest for this strategy
        latest_backtest = None
        try:
            from aletheia.storage.sqlite import get_db
            db = get_db()
            bt_row = db.execute(
                "SELECT expectancy, profit_factor, trade_count "
                "FROM backtest_runs WHERE strategy_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (strategy["id"],),
            ).fetchone()
            if bt_row:
                latest_backtest = {
                    "expectancy": bt_row[0],
                    "profit_factor": bt_row[1],
                    "trade_count": bt_row[2],
                }
                result["backtest_expectancy"] = bt_row[0]
                result["backtest_profit_factor"] = bt_row[1]
        except Exception:
            pass

        # Fetch paper PnL summary from positions linked to this strategy
        paper_summary = None
        try:
            from aletheia.storage.sqlite import get_db
            db = get_db()
            paper_row = db.execute(
                "SELECT count(*) as cnt, "
                "avg(realized_pnl_pct) as avg_pnl, "
                "sum(CASE WHEN realized_pnl_pct > 0 THEN 1 ELSE 0 END) as wins "
                # A6 (LEARNING_LOOP_AUDIT §13.1): paper-stage executions include
                # bitget_demo/okx_demo/etc., not just paper_internal. Hard-coding
                # 'paper_internal' dropped those closed positions from the strategy's
                # paper evidence count. Match all non-live (paper/demo) modes.
                "FROM positions WHERE strategy_id = ? AND status = 'closed' "
                "AND execution_mode NOT LIKE '%live%'",
                (strategy["id"],),
            ).fetchone()
            if paper_row and paper_row[0] > 0:
                cnt = paper_row[0]
                paper_summary = {
                    "trade_count": cnt,
                    "expectancy": paper_row[1],
                }
                result["paper_expectancy"] = paper_row[1]
                result["paper_trade_count"] = cnt
        except Exception:
            pass

        # Drift status
        if latest_backtest and paper_summary and paper_summary["trade_count"] >= 3:
            bt_exp = latest_backtest.get("expectancy") or 0
            paper_exp = paper_summary.get("expectancy") or 0
            ratio = paper_exp / bt_exp if bt_exp != 0 else None
            if bt_exp > 0 and paper_exp < 0:
                result["drift_status"] = "backtest_false_positive"
            elif bt_exp > 0 and ratio is not None and ratio >= 0.5:
                result["drift_status"] = "paper_confirming"
            elif bt_exp > 0 and ratio is not None and ratio < 0.5:
                result["drift_status"] = "paper_underperforming"
            else:
                result["drift_status"] = "no_clear_drift"

        # Classify evidence
        drift_for_classify = {"drift_status": result["drift_status"]} if result["drift_status"] else None
        result["evidence_status"] = _classify_evidence_status(
            has_ruleset, compile_status, latest_backtest, paper_summary, drift_for_classify,
        )
    except Exception as e:
        logger.debug("Strategy evidence lookup failed for %s: %s", strategy.get("id"), e)

    return result


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
