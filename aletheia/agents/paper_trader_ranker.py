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
    get_universe_member,
    list_opportunities,
)
from aletheia.knowledge.guards import evaluate_guards, load_trading_guards, GuardEvaluation
from aletheia.regime.detector import get_current_regime, MarketRegime
from aletheia.trading.instruments import tradable_symbols_from_config
# Position C (2026-07-02): strategy rules-engine deleted. The ranker no longer
# resolves a StrategyRecord, evaluates a RuleSet, or reads backtest evidence.
# linked_strategy_id is passed through as a pure attribution label; regime
# affinity is now a scanner-family scoring signal (see _scanner_regime_scoring).

logger = logging.getLogger("aletheia.agents.paper_trader_ranker")

_CONFIDENCE_RANK = {"speculative": 0, "medium": 1, "high": 2}
_CONFIDENCE_SCORE_DEFAULT = {"speculative": 20, "medium": 50, "high": 75}
_DEFAULT_CANDIDATE_LIMIT = 50
_MAX_OPPORTUNITY_SCAN_LIMIT = 500

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

    requested_limit = max(1, int(limit or _DEFAULT_CANDIDATE_LIMIT))
    scan_limit = min(_MAX_OPPORTUNITY_SCAN_LIMIT, max(requested_limit, requested_limit * 5))

    candidates: list[dict] = []
    opportunities = list_opportunities(limit=scan_limit)

    for opp in opportunities:
        candidate = _evaluate_candidate(opp, cfg, now_iso, _symbol_allowed, min_conf, global_venue)
        if candidate is not None:
            candidates.append(candidate)

    # Sort by trade_score descending
    candidates.sort(key=lambda c: c.get("trade_score", -999), reverse=True)
    return candidates[:requested_limit]


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
    filter_trace: list[dict] = []

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
        _filter_step(
            filter_trace, "execution_capability", "rejected", "equity_shadow_track",
            inputs={"asset_class": ac}, parameters={"executable_asset_class": "crypto"},
        )
        return _make_candidate(
            opp, skip_reason="equity_shadow_track", direction=direction,
            trade_score=score, filter_trace=filter_trace,
        )
    if ac != "crypto":
        return None

    symbol = opp.get("symbol", "")

    # Hard filter: must be open
    if opp.get("status") != "open":
        return None

    bias = (opp.get("direction_bias") or "neutral").lower()
    direction = "long" if bias in ("long", "bullish") else ("short" if bias in ("short", "bearish") else None)

    # Hard filter: execution eligible
    if not opp.get("execution_eligible", True):
        _filter_step(
            filter_trace, "execution_eligibility", "rejected", "execution_eligible_false",
            inputs={"execution_eligible": False}, parameters={"required": True},
        )
        return _make_candidate(
            opp, skip_reason="execution_eligible=False", direction=direction,
            filter_trace=filter_trace,
        )
    _filter_step(
        filter_trace, "execution_eligibility", "passed", "execution_eligible_true",
        inputs={"execution_eligible": True}, parameters={"required": True},
    )

    # Hard filter: symbol allowed
    if not symbol_allowed(symbol):
        return None

    # Direction gate
    if direction is None:
        _filter_step(
            filter_trace, "direction_present", "rejected", "neutral_or_missing_direction",
            inputs={"direction_bias": bias}, parameters={"allowed": ["long", "short"]},
        )
        return _make_candidate(
            opp, skip_reason="neutral_bias", direction=direction,
            filter_trace=filter_trace,
        )
    _filter_step(
        filter_trace, "direction_present", "passed", "direction_available",
        inputs={"direction": direction}, parameters={"allowed": ["long", "short"]},
    )

    # Confidence gate
    confidence = (opp.get("confidence") or "speculative").lower()
    conf_ok = _CONFIDENCE_RANK.get(confidence, 0) >= _CONFIDENCE_RANK.get(min_conf, 1)
    if not conf_ok:
        _filter_step(
            filter_trace, "ranker_confidence_gate", "rejected", "confidence_below",
            inputs={"confidence": confidence}, parameters={"minimum": min_conf},
        )
        return _make_candidate(
            opp, skip_reason=f"confidence_below_{min_conf}:{confidence}",
            direction=direction, filter_trace=filter_trace,
        )
    _filter_step(
        filter_trace, "ranker_confidence_gate", "passed", "confidence_at_or_above",
        inputs={"confidence": confidence}, parameters={"minimum": min_conf},
    )

    # Universe TTL gate
    universe_member = None
    if opp.get("universe_member_id"):
        universe_member = _safe_get_universe_member(opp["universe_member_id"])
        if universe_member:
            if not universe_member.get("execution_eligible", True):
                _filter_step(
                    filter_trace, "universe_execution_eligibility", "rejected",
                    "universe_not_eligible",
                    inputs={"execution_eligible": False}, parameters={"required": True},
                )
                return _make_candidate(
                    opp, skip_reason="universe_not_eligible", direction=direction,
                    universe_member=universe_member, filter_trace=filter_trace,
                )
            _filter_step(
                filter_trace, "universe_execution_eligibility", "passed",
                "universe_eligible",
                inputs={"execution_eligible": True}, parameters={"required": True},
            )
            if universe_member.get("expires_at") and universe_member["expires_at"] < now_iso:
                _filter_step(
                    filter_trace, "family_ttl", "rejected", "universe_member_expired",
                    inputs={"expires_at": universe_member.get("expires_at"), "now": now_iso},
                    parameters={"ttl_sec": universe_member.get("ttl_sec")},
                )
                return _make_candidate(
                    opp, skip_reason="ttl_expired", direction=direction,
                    universe_member=universe_member, filter_trace=filter_trace,
                )
            _filter_step(
                filter_trace, "family_ttl", "passed", "universe_member_fresh",
                inputs={"expires_at": universe_member.get("expires_at"), "now": now_iso},
                parameters={"ttl_sec": universe_member.get("ttl_sec")},
            )

    # Strategy attribution passthrough (Position C 2026-07-02): the rules engine is
    # gone. We no longer resolve a StrategyRecord, check pause/regime/market-type
    # affinity, read backtest evidence, or evaluate a RuleSet entry gate. The opp's
    # linked_strategy_id (mostly NULL now) rides through purely as an attribution
    # label. Regime affinity is applied as a scanner-family scoring signal below.
    strategy_id = opp.get("linked_strategy_id")
    strategy_evidence = None
    strategy_has_ruleset = False
    ruleset_entry_result = None

    # Venue-aware symbol filter: apply OKX demo filter when the global venue is
    # okx_demo. Use opp market_type so swap-only symbols don't pass a perp candidate.
    if global_venue == "okx_demo":
        try:
            from aletheia.execution.okx_broker import _get_demo_symbols
            _opp_market_type = (opp.get("market") or "usdt_futures").lower()
            _okx_supported = _get_demo_symbols(_opp_market_type)
            if _okx_supported is not None and to_canonical(symbol) not in _okx_supported:
                _filter_step(
                    filter_trace, "venue_symbol_capability", "rejected",
                    "okx_demo_symbol_not_supported",
                    inputs={"symbol": to_canonical(symbol), "market_type": _opp_market_type},
                    parameters={"execution_mode": global_venue},
                )
                return _make_candidate(opp, skip_reason="okx_demo_symbol_not_supported",
                                       direction=direction, strategy_id=strategy_id,
                                       universe_member=universe_member,
                                       filter_trace=filter_trace)
            _filter_step(
                filter_trace, "venue_symbol_capability", "passed", "venue_symbol_supported",
                inputs={"symbol": to_canonical(symbol), "market_type": _opp_market_type},
                parameters={"execution_mode": global_venue},
            )
        except Exception:
            pass

    # Factor feedback lookup
    try:
        _opportunity_meta = _json.loads(opp.get("metadata") or "{}")
    except (TypeError, ValueError):
        _opportunity_meta = {}
    candidate_for_factor = {
        "opportunity_id": opp.get("id"),
        "universe_member_id": opp.get("universe_member_id"),
        "learning_candidate_key": _opportunity_meta.get("learning_candidate_key"),
        "scanner_family": opp.get("scanner_family"),
        "symbol": symbol,
        "direction": direction,
        "confidence_score": opp.get("confidence_score"),
        "confidence": confidence,
        "late_entry_penalty": universe_member.get("late_entry_penalty") if universe_member else None,
    }
    factor_signal = lookup_factor_signal(candidate_for_factor)

    # P1-4: hard_blocked is a hard reject — high confidence cannot override a confirmed loser
    if factor_signal.get("authority_applied") and factor_signal.get("status") == "hard_blocked":
        _filter_step(
            filter_trace, "factor_hard_block", "rejected", "factor_hard_blocked",
            inputs={"factor_key": factor_signal.get("factor_key"),
                    "factor_status": factor_signal.get("status")},
            parameters={"blocked_status": "hard_blocked"},
        )
        return _make_candidate(
            opp, skip_reason="factor_hard_blocked",
            direction=direction, strategy_id=strategy_id,
            factor_signal=factor_signal,
            universe_member=universe_member,
            filter_trace=filter_trace,
        )
    _filter_step(
        filter_trace, "factor_hard_block", "passed", "factor_not_hard_blocked",
        inputs={"factor_key": factor_signal.get("factor_key"),
                "factor_status": factor_signal.get("status")},
        parameters={"blocked_status": "hard_blocked"},
    )

    # Guard evaluation (deterministic, no LLM)
    guard_eval = _evaluate_guards_safe(
        opp, universe_member, factor_signal, direction, confidence,
        strategy_has_ruleset, ruleset_entry_result, strategy_evidence,
    )
    if guard_eval and guard_eval.blocked:
        blocked_ids = ",".join(h.guard_id for h in guard_eval.hits if h.block_candidate)
        _filter_step(
            filter_trace, "guard_candidate_block", "rejected", "guard_blocked",
            inputs={"guard_ids": [h.guard_id for h in guard_eval.hits if h.block_candidate]},
            parameters={"scope": "paper_entry"},
        )
        return _make_candidate(
            opp, skip_reason=f"guard_blocked:{blocked_ids}",
            direction=direction, strategy_id=strategy_id,
            strategy_has_ruleset=strategy_has_ruleset,
            universe_member=universe_member, factor_signal=factor_signal,
            strategy_evidence=strategy_evidence, guard_eval=guard_eval,
            filter_trace=filter_trace,
        )
    _filter_step(
        filter_trace, "guard_candidate_block", "passed", "guard_not_blocking",
        inputs={"guard_ids": [h.guard_id for h in guard_eval.hits] if guard_eval else []},
        parameters={"scope": "paper_entry"},
    )

    # Compute trade score
    learning_trace: list[dict] = []
    trade_score = _compute_trade_score(
        opp, universe_member, factor_signal, confidence, strategy_evidence,
        strategy_has_ruleset=strategy_has_ruleset, direction=direction,
        learning_trace=learning_trace,
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
        filter_trace=filter_trace,
        learning_trace=learning_trace,
    )


def _filter_step(
    trace: list[dict],
    filter_name: str,
    decision: str,
    reason_code: str,
    *,
    inputs: dict | None = None,
    parameters: dict | None = None,
) -> None:
    trace.append({
        "filter_name": filter_name,
        "stage": "paper_ranker",
        "decision": decision,
        "reason_code": reason_code,
        "inputs": inputs or {},
        "parameters": parameters or {},
    })


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
    learning_trace: list[dict] | None = None,
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
    # Retrospective statistics are diagnostic. Only the prospective experiment
    # adapter may expose a bounded score delta after treatment/control evidence.
    if factor_signal.get("authority_applied"):
        score += float(factor_signal.get("score_delta") or 0.0)

    # Position C (2026-07-02): the strategy-evidence bonus/penalty (backtest +
    # paper-drift scoring) was removed with the rules engine. Per-family live edge is
    # surfaced to the PI via family_direction_edge (P1a); the deterministic ranker no
    # longer scores on per-strategy backtest evidence.

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

    # Position C (2026-07-02): the "+15 executable RuleSet bonus" was removed with
    # the rules engine — there are no RuleSets to prefer.

    # Historical case/memo statistics are proposed treatments, not authority. They
    # remain at the static zero-delta baseline until a prospective paper experiment
    # has enough causally linked treatment/control outcomes and a positive verdict.
    case_delta = _lesson_penalty(opp.get("symbol"), direction, opp.get("scanner_family"))
    score += _authorized_ranker_delta(
        path_id="ranker_case_score",
        proposed_delta=case_delta,
        opp=opp,
        direction=direction,
        scope={
            "symbol": opp.get("symbol"),
            "direction": direction,
            "scanner_family": opp.get("scanner_family"),
        },
        source_cohort="trade_case_study:v1",
        learning_trace=learning_trace,
    )

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

    memo_delta = _memo_scoring(opp.get("symbol"), direction, regime)
    score += _authorized_ranker_delta(
        path_id="ranker_memo_score",
        proposed_delta=memo_delta,
        opp=opp,
        direction=direction,
        scope={
            "symbol": opp.get("symbol"),
            "direction": direction,
            "regime": regime.state,
        },
        source_cohort="latest_trading_memo:v1",
        learning_trace=learning_trace,
    )

    return round(score, 2)


def _authorized_ranker_delta(
    *,
    path_id: str,
    proposed_delta: float,
    opp: dict,
    direction: str | None,
    scope: dict,
    source_cohort: str,
    learning_trace: list[dict] | None,
) -> float:
    """Return a prospective-authorized score delta, otherwise the static zero.

    A fixed-horizon key is mandatory for a writable/creditable exposure. Legacy or
    non-Universe opportunities therefore fail closed instead of creating an
    unresolvable experiment row.
    """
    try:
        meta = _json.loads(opp.get("metadata") or "{}")
    except (TypeError, ValueError):
        meta = {}
    learning_key = meta.get("learning_candidate_key")
    opportunity_id = opp.get("id")
    candidate_key = (
        f"opportunity:{opportunity_id}"
        if opportunity_id and learning_key else None
    )
    outcome_key = f"{learning_key}:4h" if learning_key else None

    from aletheia.learning.experiments import InfluenceDecision, apply_numeric_treatment

    if abs(float(proposed_delta)) < 1e-9:
        decision = InfluenceDecision(0.0, False, "baseline", "no_proposed_delta")
    else:
        try:
            decision = apply_numeric_treatment(
                path_id=path_id,
                scope={k: v for k, v in scope.items() if v is not None},
                action={"score_component": path_id, "method": "bounded_soft_delta"},
                source_cohort=source_cohort,
                target_horizon="4h",
                candidate_key=candidate_key,
                baseline=0.0,
                treatment=float(proposed_delta),
                consumer="paper_trader_ranker",
                consumer_stage="score",
                outcome_candidate_key=outcome_key,
                opportunity_id=opportunity_id,
                manifest={
                    "proposed_delta": float(proposed_delta),
                    "symbol": opp.get("symbol"),
                    "direction": direction,
                },
            )
        except Exception as exc:
            logger.warning("Ranker Learning adapter failed closed for %s: %s", path_id, exc)
            decision = InfluenceDecision(0.0, False, "unavailable", "adapter_error")
    if learning_trace is not None:
        learning_trace.append({
            "path_id": path_id,
            "baseline_delta": 0.0,
            "proposed_delta": float(proposed_delta),
            "actual_delta": float(decision.value),
            "authority_applied": decision.applied,
            "assignment": decision.assignment,
            "reason": decision.reason,
            "hypothesis_id": decision.hypothesis_id,
            "experiment_id": decision.experiment_id,
            "exposure_id": decision.exposure_id,
        })
    return float(decision.value)


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
        lessons = retrieve_lessons(
            query,
            k=3,
            item_types=["trade_case_study"],
            symbol=symbol,
            direction=direction,
            scanner_family=scanner_family,
        )
        if not lessons and scanner_family:
            # Historical case studies often predate reliable scanner_family
            # attribution. Fall back to same symbol+direction only, never to a
            # different symbol/direction, so the ranker can still learn from old
            # trades without pretending the scanner evidence exists.
            fallback_query = " ".join(p for p in (symbol, direction) if p)
            lessons = retrieve_lessons(
                fallback_query,
                k=3,
                item_types=["trade_case_study"],
                symbol=symbol,
                direction=direction,
            )
        if not lessons:
            return 0.0
        penalty = 0.0
        for lesson in lessons:
            # Phase 1a: use structured outcome field instead of keyword matching.
            # Keyword matching caused false positives (e.g. "stop_loss" in title).
            if lesson.outcome == "loss":
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

        # #131: fade the memo's influence by its age using the same 7-day half-life
        # the prompt renderer applies (memo_half_life_weight), so a nearly-decayed memo
        # no longer moves candidate ranking at full strength while the UI shows it faded.
        from aletheia.analytics.memo_effectiveness import memo_half_life_weight
        adj *= memo_half_life_weight(memo.get("generated_at"))
        return max(-45.0, min(45.0, adj))
    except Exception:
        return 0.0


# Position C (2026-07-02): _check_strategy_regime_fit and _check_ruleset_entry were
# removed with the strategy rules engine. Regime affinity is now a scanner-family
# scoring signal (_scanner_regime_scoring / _compute_trade_score); there is no
# RuleSet to evaluate. Deleting these also drops the last aletheia.strategy imports.


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
            "market_type": opp.get("market") or opp.get("market_type") or "spot",
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
    filter_trace: list[dict] | None = None,
    learning_trace: list[dict] | None = None,
) -> dict:
    """Assemble a candidate dict."""
    try:
        opportunity_meta = _json.loads(opp.get("metadata") or "{}")
    except (TypeError, ValueError):
        opportunity_meta = {}
    return {
        # Opportunity fields
        "opportunity_id": opp.get("id"),
        "symbol": opp.get("symbol"),
        "asset_class": opp.get("asset_class") or "crypto",
        # #9: carry the instrument type so trading_decision can distinguish the
        # same symbol on spot vs perp. opportunities store it under "market".
        "market_type": opp.get("market") or opp.get("market_type"),
        "direction_bias": opp.get("direction_bias"),
        "confidence": opp.get("confidence"),
        "confidence_score": opp.get("confidence_score"),
        "source_agent": opp.get("source_agent"),
        "scanner_family": opp.get("scanner_family"),
        "universe_member_id": opp.get("universe_member_id"),
        "universe_snapshot_id": opp.get("universe_snapshot_id"),
        "learning_candidate_key": opportunity_meta.get("learning_candidate_key"),
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
        "entry_price_usd": universe_member.get("entry_price_usd") if universe_member else None,
        "entry_spread_bps": universe_member.get("spread_bps") if universe_member else None,
        "funding_rate_at_entry": opp.get("funding_rate"),
        "regime_tag": opp.get("regime_tag") or (universe_member.get("regime_tag") if universe_member else None),
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
        "filter_trace": list(filter_trace or []),
        "learning_score_trace": list(learning_trace or []),
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


# Position C (2026-07-02): _lookup_strategy_evidence was removed with the strategy
# rules engine. It read backtest_runs + classified evidence via the deleted
# analytics.strategy_evidence module. Per-family live edge (the honest replacement)
# is surfaced to the PI via repositories.family_direction_edge (P1a), and the ranker
# no longer gates or scores on per-strategy backtest evidence.


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
