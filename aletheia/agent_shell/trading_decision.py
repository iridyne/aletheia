"""System Operator trading decision engine.

Provides evaluate_trading_opportunity() — the System Operator's ability to act as
Trading Decision Authority. Given a rich context snapshot, it uses structured
LLM output to decide whether to trade, then optionally runs a Bull/Bear debate
when conviction is uncertain, and finally computes a conviction-based position size.

This is intentionally a THIN layer over existing infrastructure:
  - Context comes from agent_shell/context.py (existing)
  - Proposals go through create_paper_proposal (existing, goes through Risk Engine)
  - Sizing respects existing notional caps from config/risk engine
  - No new DB tables; decisions are recorded as agent_shell audit events

Safety: System Operator can only submit paper proposals (PaperAuto permission gate).
Live trading is never reachable from this path.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.trading.entry_plan import (
    ENTRY_PLAN_MAX_RISK_REWARD,
    ENTRY_PLAN_MIN_RISK_REWARD,
    OPERATOR_RISK_REWARD_OUTSIDE_ENVELOPE,
    calculate_entry_plan_risk_reward,
    entry_plan_risk_reward_is_plausible,
)

logger = logging.getLogger("aletheia.agent_shell.trading_decision")


def _valid_protective_price(value: float | None) -> float | None:
    """Accept only finite, positive protective prices from an LLM decision."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None

# Max chars of the human-readable thesis stored in agent_rationale. The rationale
# is "<thesis>\n---\n<decision_meta JSON>"; ONLY the thesis is length-capped here.
# The JSON half must NEVER be truncated — a sliced JSON is unparseable and both
# the Proposals drawer (parseDecisionMeta) and analytics/reasoning_feedback.py
# silently lose all structured fields. The historical bug applied a second
# `[:500]` cap to the *combined* string, guillotining the ~1.1KB JSON mid-token.
# trade_proposals.agent_rationale is TEXT (no DB limit), so a generous thesis cap
# plus a whole JSON blob is safe. 600 fits every observed thesis (~60-400 chars).
_MAX_RATIONALE_TEXT = 600


class _DummySchema:
    """Shim so llm_structured() can call .model_json_schema() on a plain dict schema."""
    def __init__(self, schema: dict) -> None:
        self._schema = schema

    def model_json_schema(self) -> dict:
        return self._schema


_MIN_CONVICTION_TO_TRADE = 55       # below this, skip (even after debate)
_DEBATE_RANGE = (40, 80)            # conviction in this range triggers debate
_MAX_CONVICTION_MULTIPLIER = 2.0    # cap: 2× base notional at 100% conviction
_MIN_CONVICTION_MULTIPLIER = 0.5    # floor: 0.5× base notional at low conviction
_BIAS_BRAKE_WINDOW_HOURS = 24
_BIAS_BRAKE_MIN_PROPOSALS = 20
_BIAS_BRAKE_MAX_DIRECTION_PCT = 0.90
_BIAS_BRAKE_BYPASS_CONVICTION = 85

_ADVERSE_SKIP_REASON_CODES = {
    "signal_conflict",
    "regime_mismatch",
    "adverse_price_structure",
    "liquidity_or_execution_quality",
    "negative_same_cohort_outcomes",
    "invalid_risk_reward",
}


class DecisionUnavailable(RuntimeError):
    """A transient Operator input/preflight dependency is not authoritative yet.

    Callers must not turn this into a durable skip or trade claim.  The pending
    queue identity remains available so the daemon can retry after its cooldown.

    ``filter_trace`` optionally carries the in-progress evaluation trace so the
    caller can persist gate-level ``filter_decisions`` rows (DL-INV-7: filter
    facts must be recorded when they happen). Without this, non-terminal defers
    (Phase C conviction / debate gates) never leave a filter_decisions audit
    row and diagnostic aggregations undercount them.
    """

    def __init__(
        self,
        reason: str,
        *,
        stage: str = "operator_evaluation",
        filter_trace: list[dict] | None = None,
    ) -> None:
        self.reason = reason
        self.stage = stage
        self.filter_trace: list[dict] = list(filter_trace) if filter_trace else []
        super().__init__(reason)


@dataclass
class TradingDecision:
    action: str              # active model surface: "trade" | "skip"
    symbol: str | None
    direction: str | None    # "long" | "short"
    conviction: int          # 0-100
    reasoning: str
    risk_factors: list[str] = field(default_factory=list)
    supporting_signals: list[str] = field(default_factory=list)
    opposing_signals: list[str] = field(default_factory=list)
    # A direct skip is terminal only when it names candidate-specific adverse
    # evidence.  ``insufficient_evidence`` is intentionally neutral and cannot
    # consume a pending Opportunity by itself.
    skip_kind: str | None = None
    adverse_reason_codes: list[str] = field(default_factory=list)
    adverse_evidence: list[str] = field(default_factory=list)
    # Deterministic validator output. The model cannot populate this authority:
    # each terminal adverse code must resolve to exact selected-candidate facts.
    validated_adverse_claims: list[dict] = field(default_factory=list)
    # Original model prose is retained for audit, but terminal direct-skip
    # reasoning is replaced by deterministic fact rendering.
    model_reasoning: str | None = None
    position_size_usdt: float | None = None
    stop_price: float | None = None
    take_profit: float | None = None
    # Exit-intent plan M2: the Operator's intended holding horizon (hours). Drives the
    # per-position time-stop — hitting it wakes the Operator for a must-answer decision
    # rather than auto-closing; auto-close only at the far backstop (see position_manager
    # + risk.horizon_hard_backstop_mult). None → legacy school max_hold applies.
    expected_horizon_hours: float | None = None
    # Exit-intent plan M3.5 (D10): for a proactive `close`, the Operator chooses the
    # order type — "market" (exit now) or "limit" (rest an exit at limit_price for a
    # better fill). None → market. Only meaningful on close actions.
    order_type: str | None = None
    limit_price: float | None = None
    # Futures: which venue + leverage the PI chose. market_type defaults to
    # usdt_futures (the demo/perp venue) when the LLM omits it; leverage is the
    # PI's requested level, clamped to the per-tier cap at submit time.
    market_type: str | None = None
    leverage: int | None = None
    debate_ran: bool = False
    debate_initial_conviction: int | None = None
    debate_bull_confidence: int | None = None
    debate_bear_confidence: int | None = None
    debate_adjustment: int | None = None
    # R0 attribution: backfilled from the source opportunity by
    # _attach_opportunity_context so the proposal carries a real opportunity_id /
    # strategy_id and the decision_meta records scanner_family (LEARNING_LOOP_AUDIT
    # §R0 + BACKEND_ISSUES §1.1 causal-chain strategy_id).
    opportunity_id: str | None = None
    learning_candidate_key: str | None = None
    strategy_id: str | None = None
    scanner_family: str | None = None
    # Phase 2 (Serenity Option A): which table opportunity_id resolves against.
    # 'market_observer' → opportunities.id; 'serenity' → serenity_opportunities.id.
    # Stamped by _attach_opportunity_context (market_observer takes precedence).
    signal_source: str = "market_observer"
    # S4 / P2 (2026-06-28, AI Berkshire forced-verdict): explicit counter-argument.
    # System Operator must fill bear_case even when recommending a trade — forces acknowledgment
    # of what could go wrong. Empty string = not provided (legacy / pre-S4 decisions).
    bear_case: str = ""
    # P1 (2026-06-28, AI Berkshire belief decomposition): the critical assumptions
    # this decision depends on. At most 3 items, kept concise. Empty = not provided.
    key_assumptions: list[str] = field(default_factory=list)
    observed_facts: list[dict] = field(default_factory=list)
    interpretations: list[str] = field(default_factory=list)
    invalidation_conditions: list[str] = field(default_factory=list)
    uncertainty_reason: str = ""
    evidence_manifest: dict = field(default_factory=dict)
    trade_motivation: dict = field(default_factory=dict)
    # Append-only observation facts for the exact gates evaluated in this call.
    # Persisted only after the authoritative PiDecision row is written.
    filter_trace: list[dict] = field(default_factory=list)
    sizing_trace: dict = field(default_factory=dict)
    # One concise AI reason for each value that expresses an entry. The exact
    # entry mark and capped size/leverage remain deterministic, while a parsed
    # model trade must preserve its authored stop/take-profit prices. Empty or
    # missing reasons are explicitly absent evidence, never filled after the fact.
    parameter_rationale: dict[str, str] = field(default_factory=dict)
    # Set only for decisions parsed from the System Operator's LLM response.
    # Programmatic/historical callers retain compatibility, while an AI trade
    # cannot silently bypass the explanation requirement.
    parameter_rationale_required: bool = False


@dataclass
class DebateResult:
    bull_confidence: int
    bull_thesis: str
    bull_evidence: list[str]
    bear_confidence: int
    bear_thesis: str
    bear_evidence: list[str]


# ── schemas for structured LLM output ────────────────────────────────────────

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["trade", "skip"]},
        "symbol": {"type": "string"},
        "direction": {"type": "string", "enum": ["long", "short"]},
        "conviction": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning": {"type": "string"},
        # Forced counter-argument: even a trade recommendation must state the
        # strongest concrete failure case instead of presenting one-sided confidence.
        "bear_case": {"type": "string", "minLength": 1},
        # market_type: which venue expresses the view. "usdt_futures" (perp) supports
        # leverage and pays/earns funding; "spot" has no leverage and no funding cost.
        "market_type": {"type": "string", "enum": ["spot", "usdt_futures"]},
        # leverage: integer ≥1, only meaningful for usdt_futures. The risk engine
        # clamps this to the per-asset-tier cap, so a high request is safely reduced.
        "leverage": {"type": "integer", "minimum": 1, "maximum": 20},
        # Entry discipline: a trade must carry explicit Operator-authored stop and
        # take-profit prices. The exact fresh mark remains the authoritative entry;
        # reward:risk is derived from those three committed values. Missing or invalid
        # model values are repaired/retried, never silently replaced by config.
        "stop_price": {"type": "number"},
        "take_profit": {"type": "number"},
        # M2: Operator's intended holding horizon in hours (drives the time-stop /
        # must-answer horizon wake). Optional — omitted → legacy school max_hold.
        "expected_horizon_hours": {"type": "number"},
        # M3.5 (D10): proactive-close order type + optional resting limit price.
        "order_type": {"type": "string", "enum": ["market", "limit"]},
        "limit_price": {"type": "number"},
        # Required for trade by the prompt below. It remains optional in the
        # top-level schema because skip has no entry parameters to explain.
        "parameter_rationale": {
            "type": "object",
            "properties": {
                "entry_price": {"type": "string"},
                "stop_loss": {"type": "string"},
                "take_profit": {"type": "string"},
                "notional_usdt": {"type": "string"},
                "leverage": {"type": "string"},
            },
        },
        "risk_factors": {"type": "array", "items": {"type": "string"}},
        "supporting_signals": {"type": "array", "items": {"type": "string"}},
        "opposing_signals": {"type": "array", "items": {"type": "string"}},
        "skip_kind": {
            "type": ["string", "null"],
            "enum": [
                "adverse_candidate_evidence",
                "insufficient_evidence",
                None,
            ],
        },
        "adverse_reason_codes": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(_ADVERSE_SKIP_REASON_CODES)},
        },
        "adverse_evidence": {"type": "array", "items": {"type": "string"}},
        # P1: structured assumption list — at most 3, kept concise
        "key_assumptions": {"type": "array", "items": {"type": "string"}},
        "observed_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["fact", "evidence_refs"],
            },
        },
        "interpretations": {"type": "array", "items": {"type": "string"}},
        "invalidation_conditions": {"type": "array", "items": {"type": "string"}},
        "uncertainty_reason": {"type": "string"},
    },
    "required": ["action", "conviction", "reasoning", "bear_case"],
    # The lightweight structured-output validator supports allOf/oneOf/const.
    # Keep skip valid without invented entry fields, while making a trade
    # structurally incomplete until every model-authored plan field is present.
    "allOf": [{
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "trade"},
                    "parameter_rationale": {
                        "type": "object",
                        "properties": {
                            "entry_price": {"type": "string", "minLength": 1},
                            "stop_loss": {"type": "string", "minLength": 1},
                            "take_profit": {"type": "string", "minLength": 1},
                            "notional_usdt": {"type": "string", "minLength": 1},
                            "leverage": {"type": "string", "minLength": 1},
                        },
                        "required": [
                            "entry_price",
                            "stop_loss",
                            "take_profit",
                            "notional_usdt",
                            "leverage",
                        ],
                    },
                },
                "required": [
                    "action",
                    "symbol",
                    "direction",
                    "market_type",
                    "stop_price",
                    "take_profit",
                    "parameter_rationale",
                ],
            },
            {
                "type": "object",
                "properties": {"action": {"const": "skip"}},
                "required": ["action"],
            },
        ],
    }],
}

_BULL_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "thesis": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confidence", "thesis", "evidence"],
}

_BEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "thesis": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["confidence", "thesis", "evidence"],
}


# ── main entry point ──────────────────────────────────────────────────────────

def _reuse_recent_skip_validation(trading_context: dict, decision) -> dict | None:
    """Recent validated-skip cache for this setup at an UNCHANGED material fp.

    Returns the cached ``operator_skip_validated`` snapshot (carrying the prior
    validated adverse judgment) when the model again judges the same
    symbol+direction a skip and the material fingerprint is unchanged, else None.
    Lets the caller skip the second (counterfactual) LLM call. Gated by
    ``reuse_recent_skip_validation``; any error or miss returns None so the caller
    falls back to a live reassessment (fail open). (#321)
    """
    try:
        policy = trading_context.get("skip_policy") or {}
        if not policy.get("reuse_recent_skip_validation"):
            return None
        if not getattr(decision, "opportunity_id", None) or not getattr(
            decision, "symbol", None
        ):
            return None
        from aletheia.agent_shell.trading_context import (
            material_fingerprint_from_candidate_ctx,
            _normalize_direction,
            _normalize_market_type,
        )
        from aletheia.storage.repositories import latest_skip_validation_for_setup

        current_fp = material_fingerprint_from_candidate_ctx(
            trading_context, decision.opportunity_id
        )
        if not current_fp:
            return None
        window = int(policy.get("reuse_skip_validation_window_minutes") or 30)
        cached = latest_skip_validation_for_setup(
            decision.symbol,
            _normalize_direction(decision.direction),
            _normalize_market_type(decision.market_type),
            window_minutes=window,
        )
        if not cached or not cached.get("skip_kind"):
            return None
        if cached.get("material_fingerprint") != current_fp:
            return None
        return cached
    except Exception:
        return None


async def evaluate_trading_opportunity(trading_context: dict) -> TradingDecision:
    """System Operator trading decision loop.

    Steps:
    1. Assess opportunities using structured LLM with full context
    2. If conviction in uncertain range (40-80), run Bull/Bear debate
    3. Compute conviction-based position size
    4. Return TradingDecision (caller submits a proposal only for action="trade")
    """
    decision_raw = await _assess_opportunity(trading_context)
    if decision_raw is None:
        raise DecisionUnavailable("llm_assessment_unavailable")

    confirmation_attempts = 0
    initial_action = str(decision_raw.get("action") or "").strip().lower() or None
    try:
        decision = _parse_decision(decision_raw)
    except DecisionUnavailable as exc:
        # A malformed response is not a business decision. Give the model one
        # bounded correction opportunity, then preserve the pending carrier.
        confirmation_attempts = 1
        corrected = await _reassess_direct_skip(
            trading_context, decision_raw, exc.reason
        )
        try:
            decision = _parse_decision(corrected or {})
        except DecisionUnavailable as corrected_exc:
            plan_failure = any(
                reason.startswith("incomplete_operator_entry_plan:")
                for reason in (exc.reason, corrected_exc.reason)
            )
            raise DecisionUnavailable(
                f"operator_output_not_repaired:{corrected_exc.reason}",
                stage=(
                    "operator_entry_plan"
                    if plan_failure else "operator_skip_validation"
                ),
            ) from corrected_exc
    # R0: match the pick back to its source opportunity (id + scanner_family)
    # before any return path, so skip records carry attribution.
    _attach_opportunity_context(decision, trading_context)

    def _entry_plan_preflight(current: TradingDecision) -> str | None:
        entry_mark = (trading_context.get("layer2") or {}).get("entry_mark") or {}
        mark_price_available = (
            entry_mark.get("status") == "available"
            and _valid_protective_price(entry_mark.get("price")) is not None
        )
        mark_identity_matches = (
            entry_mark.get("symbol") == current.symbol
            and entry_mark.get("market_type") == current.market_type
        )
        mark_available = mark_price_available and mark_identity_matches
        mark_reason = (
            "candidate_entry_mark_available"
            if mark_available
            else (
                "candidate_entry_mark_identity_mismatch"
                if mark_price_available else "candidate_entry_mark_unavailable"
            )
        )
        current.filter_trace.append({
            "filter_name": "operator_entry_mark",
            "stage": "system_operator",
            "decision": "passed" if mark_available else "rejected",
            "reason_code": mark_reason,
            "inputs": {
                "decision_symbol": current.symbol,
                "decision_market_type": current.market_type,
                "mark_symbol": entry_mark.get("symbol"),
                "mark_market_type": entry_mark.get("market_type"),
                "authority": entry_mark.get("authority"),
            },
            "parameters": {"required_for_action": "trade"},
        })
        if not mark_available:
            return mark_reason

        entry_price = _valid_protective_price(entry_mark.get("price"))
        if current.market_type == "spot" and current.direction == "short":
            reason = "unsupported_operator_instrument_direction:spot_short"
            current.filter_trace.append({
                "filter_name": "operator_entry_plan",
                "stage": "system_operator",
                "decision": "rejected",
                "reason_code": reason,
                "inputs": {
                    "direction": current.direction,
                    "market_type": current.market_type,
                    "entry_price": entry_price,
                },
                "parameters": {
                    "allowed_spot_directions": ["long"],
                    "instrument_relabel_allowed": False,
                },
            })
            return reason
        invalid_fields: list[str] = []
        if current.direction == "long":
            if current.stop_price is not None and current.stop_price >= entry_price:
                invalid_fields.append("stop_price")
            if (
                current.take_profit is not None
                and current.take_profit <= entry_price
            ):
                invalid_fields.append("take_profit")
        elif current.direction == "short":
            if current.stop_price is not None and current.stop_price <= entry_price:
                invalid_fields.append("stop_price")
            if (
                current.take_profit is not None
                and current.take_profit >= entry_price
            ):
                invalid_fields.append("take_profit")
        geometry_reason = (
            "invalid_operator_entry_plan_geometry:" + "+".join(invalid_fields)
            if invalid_fields else "operator_entry_plan_geometry_valid"
        )
        risk_reward = None
        risk_reward_reason = None
        if not invalid_fields:
            risk_reward = calculate_entry_plan_risk_reward(
                entry_price,
                current.stop_price,
                current.take_profit,
            )
            if not entry_plan_risk_reward_is_plausible(risk_reward):
                risk_reward_reason = OPERATOR_RISK_REWARD_OUTSIDE_ENVELOPE
        entry_plan_reason = risk_reward_reason or geometry_reason
        current.filter_trace.append({
            "filter_name": "operator_entry_plan",
            "stage": "system_operator",
            "decision": (
                "rejected" if invalid_fields or risk_reward_reason else "passed"
            ),
            "reason_code": entry_plan_reason,
            "inputs": {
                "direction": current.direction,
                "entry_price": entry_price,
                "stop_price": current.stop_price,
                "take_profit": current.take_profit,
                "risk_reward": risk_reward,
            },
            "parameters": {
                "long_geometry": "stop < entry < take_profit",
                "short_geometry": "take_profit < entry < stop",
                "min_risk_reward": ENTRY_PLAN_MIN_RISK_REWARD,
                "max_risk_reward": ENTRY_PLAN_MAX_RISK_REWARD,
            },
        })
        return entry_plan_reason if invalid_fields or risk_reward_reason else None

    while decision.action == "trade":
        entry_plan_reason = _entry_plan_preflight(decision)
        if entry_plan_reason is None:
            break
        if entry_plan_reason.startswith("candidate_entry_mark_"):
            raise DecisionUnavailable(
                entry_plan_reason,
                stage="operator_entry_plan",
                filter_trace=list(decision.filter_trace),
            )
        if confirmation_attempts > 0:
            raise DecisionUnavailable(
                f"operator_output_not_repaired:{entry_plan_reason}",
                stage="operator_entry_plan",
                filter_trace=list(decision.filter_trace),
            )
        prior_trace = list(decision.filter_trace)
        confirmation_attempts = 1
        try:
            corrected = await _reassess_direct_skip(
                trading_context, decision_raw, entry_plan_reason
            )
        except DecisionUnavailable as repair_exc:
            raise DecisionUnavailable(
                "operator_entry_plan_reassessment_unavailable",
                stage="operator_entry_plan",
                filter_trace=prior_trace,
            ) from repair_exc
        try:
            decision = _parse_decision(corrected or {})
        except DecisionUnavailable as corrected_exc:
            raise DecisionUnavailable(
                f"operator_output_not_repaired:{corrected_exc.reason}",
                stage="operator_entry_plan",
                filter_trace=prior_trace,
            ) from corrected_exc
        # The rejected first draft is an output-repair fact, not the
        # authoritative business-gate exposure. If repair succeeds, keep that
        # audit without placing a subsequently approved trade in the rejected
        # ``operator_entry_plan`` learning cohort. A second invalid plan is
        # appended below as the true rejected exposure before the typed defer.
        repair_audit_trace = []
        for step in prior_trace:
            if (
                step.get("filter_name") == "operator_entry_plan"
                and step.get("decision") == "rejected"
                and step.get("reason_code") == entry_plan_reason
            ):
                step = {
                    **step,
                    "filter_name": "operator_entry_plan_output_repair",
                    "stage": "operator_output_validation",
                    "decision": "error",
                    "parameters": {
                        **(step.get("parameters") or {}),
                        "repair_attempted": True,
                    },
                }
            repair_audit_trace.append(step)
        decision.filter_trace = repair_audit_trace + decision.filter_trace
        _attach_opportunity_context(decision, trading_context)

    initial_skip_kind = decision.skip_kind
    skip_confirmation_required = (
        _direct_skip_confirmation_required(trading_context)
        and not _has_submitting_final(decision)
    )
    if (
        decision.action == "skip"
        and skip_confirmation_required
        and confirmation_attempts == 0
    ):
        validation_reason = (
            _direct_skip_validation_reason(decision, trading_context)
            or "counterfactual_confirmation_required"
        )
        initial_opportunity_id = decision.opportunity_id
        confirmation_attempts = 1
        reused_validation = _reuse_recent_skip_validation(trading_context, decision)
        if reused_validation is not None:
            # #321: an identical-material skip for this setup was counterfactually
            # confirmed recently. Overlay its validated adverse judgment instead of
            # re-invoking the model, then fall through to the SAME validation block
            # below. If the overlay fails to re-validate against the current ctx,
            # that block raises direct_skip_not_confirmed exactly as a live reassess
            # failure would — the reuse can save an LLM call but never manufacture a
            # trade or a wrongly-confirmed skip.
            decision.skip_kind = reused_validation.get("skip_kind") or decision.skip_kind
            decision.adverse_reason_codes = (
                reused_validation.get("adverse_reason_codes")
                or decision.adverse_reason_codes
            )
            decision.adverse_evidence = (
                reused_validation.get("adverse_evidence") or decision.adverse_evidence
            )
            decision.validated_adverse_claims = (
                reused_validation.get("validated_adverse_claims")
                or decision.validated_adverse_claims
            )
        else:
            confirmed_raw = await _reassess_direct_skip(
                trading_context, decision_raw, validation_reason
            )
            try:
                decision = _parse_decision(confirmed_raw or {})
            except DecisionUnavailable as exc:
                raise DecisionUnavailable(
                    f"direct_skip_not_confirmed:{exc.reason}",
                    stage="operator_skip_validation",
                ) from exc
            _attach_opportunity_context(decision, trading_context)
            if (
                decision.action == "skip"
                and initial_opportunity_id
                and decision.opportunity_id != initial_opportunity_id
            ):
                raise DecisionUnavailable(
                    "direct_skip_not_confirmed:candidate_changed",
                    stage="operator_skip_validation",
                )

    if decision.action == "skip" and skip_confirmation_required:
        invalid_skip_reason = _direct_skip_validation_reason(decision, trading_context)
        if invalid_skip_reason:
            raise DecisionUnavailable(
                f"direct_skip_not_confirmed:{invalid_skip_reason}",
                stage="operator_skip_validation",
            )
        from aletheia.agent_shell.decision_facts import render_validated_adverse_judgment

        decision.model_reasoning = decision.reasoning
        decision.reasoning, decision.adverse_evidence = render_validated_adverse_judgment(
            decision, trading_context
        )

    trade_actions = ("trade",)
    trade_intent = decision.action in trade_actions
    # A direct model skip is a real System Operator filter result.  It used to
    # return before debate/conviction tracing, leaving the most common terminal
    # path with no machine-readable fact or fixed-outcome link.  Record the
    # model's action before any deterministic gate can amend it; the terminal
    # claim persists this trace atomically with the candidate decision.
    assessment_inputs = {
        "model_action": decision.action,
        "initial_conviction": decision.conviction,
        "confirmation_attempts": confirmation_attempts,
    }
    if initial_action:
        assessment_inputs["initial_model_action"] = initial_action
    if initial_skip_kind:
        assessment_inputs["initial_skip_kind"] = initial_skip_kind
    if decision.skip_kind:
        assessment_inputs["skip_kind"] = decision.skip_kind
    if decision.adverse_reason_codes:
        assessment_inputs["adverse_reason_codes"] = decision.adverse_reason_codes
    if decision.validated_adverse_claims:
        assessment_inputs["validated_adverse_claims"] = decision.validated_adverse_claims
    if trading_context.get("skip_policy_epoch_id"):
        assessment_inputs["policy_epoch_id"] = trading_context["skip_policy_epoch_id"]
    b2_evidence = _b2_evidence_for_decision(decision, trading_context)
    if b2_evidence:
        assessment_inputs["b2_evidence"] = b2_evidence
    decision.filter_trace.insert(0, {
        "filter_name": "operator_action_assessment",
        "stage": "system_operator",
        "decision": "passed" if trade_intent else "rejected",
        # Keep the stable terminal-path reason code so existing B2/diagnostic
        # cohorts remain queryable; confirmation semantics are versioned in the
        # policy epoch and explicit inputs below.
        "reason_code": "operator_model_trade" if trade_intent else "operator_model_skip",
        "inputs": assessment_inputs,
        "parameters": {"schema_actions": ["trade", "skip"]},
    })

    bias_brake_reason = _proposal_bias_brake_reason(decision)
    if bias_brake_reason:
        decision.filter_trace.append({
            "filter_name": "direction_bias_brake",
            "stage": "system_operator",
            "decision": "rejected",
            "reason_code": "proposal_direction_concentration",
            "inputs": {"conviction": decision.conviction, "direction": decision.direction},
            "parameters": {
                "window_hours": _BIAS_BRAKE_WINDOW_HOURS,
                "min_proposals": _BIAS_BRAKE_MIN_PROPOSALS,
                "max_direction_pct": _BIAS_BRAKE_MAX_DIRECTION_PCT,
                "bypass_conviction": _BIAS_BRAKE_BYPASS_CONVICTION,
            },
        })
        # Use a distinct action so the decisions API can distinguish a
        # portfolio-guard rejection from a genuine LLM-initiated operator_skip.
        # (#330: direction_bias_brake rejection was misattributed as operator_skip)
        decision.action = "portfolio_guard_skip"
        decision.reasoning = f"{decision.reasoning} [{bias_brake_reason}]"
        return decision
    if trade_intent:
        decision.filter_trace.append({
            "filter_name": "direction_bias_brake",
            "stage": "system_operator",
            "decision": "passed",
            "reason_code": "proposal_direction_not_concentrated",
            "inputs": {"conviction": decision.conviction, "direction": decision.direction},
            "parameters": {
                "window_hours": _BIAS_BRAKE_WINDOW_HOURS,
                "min_proposals": _BIAS_BRAKE_MIN_PROPOSALS,
                "max_direction_pct": _BIAS_BRAKE_MAX_DIRECTION_PCT,
                "bypass_conviction": _BIAS_BRAKE_BYPASS_CONVICTION,
            },
        })

    # Run debate for uncertain conviction range
    if (decision.action in trade_actions
            and decision.symbol
            and _DEBATE_RANGE[0] <= decision.conviction <= _DEBATE_RANGE[1]):
        initial_conviction = decision.conviction
        debate = await _run_debate(decision.symbol, decision.direction, trading_context)
        if debate is None:
            raise DecisionUnavailable(
                "required_debate_unavailable", stage="operator_debate"
            )
        decision.debate_ran = True
        decision.debate_initial_conviction = initial_conviction
        decision.debate_bull_confidence = debate.bull_confidence
        decision.debate_bear_confidence = debate.bear_confidence
        # Debate adjusts the initial conviction; it does not replace it. Bull
        # confidence is success probability for the proposed direction and bear
        # confidence is its failure probability, so the difference is correctly
        # signed for either long or short.
        advantage = debate.bull_confidence - debate.bear_confidence
        adjusted = int(round(initial_conviction + advantage * 0.3))
        decision.conviction = max(0, min(100, adjusted))
        decision.debate_adjustment = decision.conviction - initial_conviction
        below_minimum = decision.conviction < _MIN_CONVICTION_TO_TRADE
        # Emit the audit trace BEFORE raising, so the trace exists even though
        # this raise turns the decision into a non-terminal defer (DL-S48).
        # Historical bucketing still reads ``debate_below_min_conviction`` so
        # cohorts remain queryable across policy_epoch changes; the new
        # ``debate_not_confirmed`` alias is what the aggregator maps to the
        # ``debate_not_confirmed`` bucket (see pipeline_progress.py).
        decision.filter_trace.append({
            "filter_name": "debate_downgrade",
            "stage": "system_operator_debate",
            "decision": "rejected" if below_minimum else "passed",
            "reason_code": (
                "debate_not_confirmed"
                if below_minimum
                else "debate_at_or_above_min_conviction"
            ),
            "inputs": {
                "initial_conviction": initial_conviction,
                "final_conviction": decision.conviction,
                "bull_confidence": debate.bull_confidence,
                "bear_confidence": debate.bear_confidence,
            },
            "parameters": {
                "debate_range": list(_DEBATE_RANGE),
                "minimum_conviction": _MIN_CONVICTION_TO_TRADE,
                "adjustment_weight": 0.3,
            },
        })
        if below_minimum:
            # DL-INV-2 (Phase C, 2026-07-18): a below-threshold conviction after
            # debate is no longer a terminal candidate skip. It is evaluation
            # unavailable — the caller preserves the pending carrier and waits
            # for a material context change (new feature snapshot, regime tag
            # flip, catalyst) to reopen the same candidate. This closes the
            # indirect bypass where SIDEWAYS/equity/tiny-sample reasoning
            # depressed conviction just enough to skip via the debate channel.
            raise DecisionUnavailable(
                "debate_not_confirmed",
                stage="operator_debate_gate",
                filter_trace=list(decision.filter_trace),
            )

    # Compute sizing for the single executable entry action.
    if decision.action in trade_actions and decision.conviction >= _MIN_CONVICTION_TO_TRADE:
        decision.filter_trace.append({
            "filter_name": "operator_conviction_gate",
            "stage": "system_operator",
            "decision": "passed",
            "reason_code": "conviction_at_or_above_minimum",
            "inputs": {"conviction": decision.conviction},
            "parameters": {"minimum_conviction": _MIN_CONVICTION_TO_TRADE},
        })
        ptc = _get_strategy_paper_trade_count(decision.strategy_id)
        decision.position_size_usdt = _compute_position_size(
            decision.conviction,
            symbol=decision.symbol,
            direction=decision.direction,
            paper_trade_count=ptc,
            opportunity_id=decision.opportunity_id,
            learning_candidate_key=decision.learning_candidate_key,
            sizing_trace=decision.sizing_trace,
        )
    elif trade_intent:
        # Initial conviction < 55 without a debate range trigger (or debate did
        # not raise). Same rationale as debate_not_confirmed above (DL-S47):
        # the candidate remains queued and a new material fingerprint reopens
        # evaluation. The trace preserves ``operator_low_conviction`` so
        # diagnostic aggregations can distinguish it from debate-driven defers.
        decision.filter_trace.append({
            "filter_name": "operator_conviction_gate",
            "stage": "system_operator",
            "decision": "rejected",
            "reason_code": "operator_low_conviction",
            "inputs": {"conviction": decision.conviction},
            "parameters": {"minimum_conviction": _MIN_CONVICTION_TO_TRADE},
        })
        raise DecisionUnavailable(
            "operator_low_conviction",
            stage="operator_conviction_gate",
            filter_trace=list(decision.filter_trace),
        )

    return decision


def _latest_exact_entry_snapshot(
    symbol: str,
    market_type: str,
    venue: str,
) -> dict | None:
    """Return metadata for the exact venue/instrument used by local entry pricing.

    Freshness authority remains in ``get_fresh_price_for_protective_close``. This
    helper only preserves the exact snapshot id/timestamp in Proposal rationale and
    distinguishes an old exact observation from no exact observation at all.
    """
    if not venue:
        return None
    from aletheia.data.symbols import to_bitget, to_canonical
    from aletheia.storage.repositories import list_market_snapshots

    canonical = to_canonical(symbol)
    symbols = [canonical]
    legacy = to_bitget(canonical)
    if legacy != canonical:
        symbols.append(legacy)
    physical_market = "perp" if market_type == "usdt_futures" else market_type
    markets = [physical_market]
    if market_type == "usdt_futures":
        markets.append("usdt_futures")
    rows: list[dict] = []
    for stored_symbol in symbols:
        for stored_market in markets:
            rows.extend(list_market_snapshots(
                symbol=stored_symbol,
                exchange=venue,
                market_type=stored_market,
                limit=1,
            ))
    return (
        max(rows, key=lambda row: str(row.get("snapshot_at") or ""))
        if rows else None
    )


def attach_candidate_entry_mark(ctx: dict, execution_mode: str) -> str | None:
    """Bind the exact selected carrier to the price the model may plan around.

    The mark is read-only prompt authority. Proposal preparation independently
    refreshes it after inference, so movement during the model call fails geometry
    closed instead of executing a stale plan.
    """
    layer2 = ctx.setdefault("layer2", {})
    pending = list(layer2.get("pending_signals") or [])
    if len(pending) != 1:
        layer2["entry_mark"] = {"status": "unavailable"}
        return "candidate_entry_mark_identity_unavailable"
    candidate = pending[0]
    symbol = str(candidate.get("symbol") or "").strip()
    raw_market = str(
        candidate.get("market_type") or candidate.get("market") or ""
    ).lower()
    market_type = (
        "usdt_futures"
        if raw_market in {"perp", "futures", "swap", "usdt_futures"}
        else "spot" if raw_market == "spot" else ""
    )
    if not symbol or not market_type:
        layer2["entry_mark"] = {"status": "unavailable"}
        return "candidate_entry_mark_identity_unavailable"

    snapshot: dict | None = None
    venue: str | None = None
    try:
        if execution_mode == "paper_internal":
            from aletheia.config.loader import load_config
            from aletheia.data.price_feed import (
                get_fresh_price_for_protective_close,
                protective_price_venue,
            )

            cfg = load_config()
            venue = protective_price_venue(
                execution_mode,
                configured_data_venue=cfg.market_data.ccxt_exchange,
            )
            price = get_fresh_price_for_protective_close(
                symbol, market_type, venue=venue or "",
            )
            snapshot = _latest_exact_entry_snapshot(symbol, market_type, venue or "")
            authority = "fresh_exact_market_mark"
        else:
            from aletheia.trading.market_data import latest_snapshot

            snapshot = latest_snapshot(symbol, market_type=market_type)
            price = (snapshot or {}).get("price")
            venue = (snapshot or {}).get("exchange")
            authority = "market_snapshot"
    except Exception:
        logger.warning(
            "candidate entry mark lookup unavailable for %s [%s]",
            symbol,
            market_type,
        )
        layer2["entry_mark"] = {
            "status": "unavailable",
            "symbol": symbol,
            "market_type": market_type,
            "price": None,
            "venue": venue,
            "authority": "unavailable",
            "snapshot_id": None,
            "snapshot_at": None,
        }
        return "candidate_entry_mark_unavailable"

    validated_price = _valid_protective_price(price)
    layer2["entry_mark"] = {
        "status": "available" if validated_price is not None else "unavailable",
        "symbol": symbol,
        "market_type": market_type,
        "price": validated_price,
        "venue": venue,
        "authority": authority,
        "snapshot_id": (snapshot or {}).get("id"),
        "snapshot_at": (snapshot or {}).get("snapshot_at"),
    }
    return None if validated_price is not None else "candidate_entry_mark_unavailable"


async def prepare_decision_as_proposal(decision: TradingDecision, execution_mode: str) -> dict:
    """Resolve one trade intent into an immutable Proposal without persisting it.

    The caller can commit this Proposal in the same short transaction as the
    authoritative PiDecision claim.  No Risk or broker side effect happens here.
    """
    identity_failure = None
    if decision.action != "trade":
        identity_failure = "invalid_action"
    elif not decision.symbol:
        identity_failure = "missing_symbol"
    elif not decision.direction:
        identity_failure = "missing_direction"
    if identity_failure:
        decision.filter_trace.append({
            "filter_name": "proposal_identity",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": identity_failure,
            "inputs": {"action": decision.action},
            "parameters": {
                "required_fields": ["symbol", "direction"],
                "trade_actions": ["trade"],
            },
        })
        raise DecisionUnavailable(identity_failure, stage="proposal_preflight")

    # A Universe Scanner carrier cannot authorize the opposite trade direction.
    # T0 is the immutable producer fact; the mutable Opportunity projection may
    # contain legacy rows whose outer direction was rewritten by enrichment.
    if decision.opportunity_id and decision.signal_source == "market_observer":
        from aletheia.storage.repositories import get_opportunity

        source_opportunity = get_opportunity(decision.opportunity_id)
        if source_opportunity:
            raw_metadata = source_opportunity.get("metadata") or {}
            try:
                metadata = (
                    raw_metadata
                    if isinstance(raw_metadata, dict)
                    else json.loads(raw_metadata)
                )
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            evidence_manifest = metadata.get("evidence_manifest") or {}
            if not isinstance(evidence_manifest, dict):
                evidence_manifest = {}
            t0 = evidence_manifest.get("t0") or {}
            if not isinstance(t0, dict):
                t0 = {}

            source_agent = str(
                source_opportunity.get("source_agent") or ""
            ).strip().lower()
            trigger_type = str(t0.get("trigger_type") or "").strip().lower()
            scanner_owned = (
                source_agent == "universe_scanner"
                or trigger_type == "universe_scanner"
            )
            scanner_direction = str(t0.get("direction") or "").strip().lower()
            decision_direction = str(decision.direction or "").strip().lower()
            if (
                scanner_owned
                and scanner_direction in {"long", "short"}
                and decision_direction in {"long", "short"}
            ):
                direction_matches = scanner_direction == decision_direction
                reason = (
                    "opportunity_direction_lineage_match"
                    if direction_matches
                    else "opportunity_direction_lineage_conflict"
                )
                decision.filter_trace.append({
                    "filter_name": "opportunity_direction_lineage",
                    "stage": "proposal_submission",
                    "decision": "passed" if direction_matches else "rejected",
                    "reason_code": reason,
                    "inputs": {
                        "opportunity_id": decision.opportunity_id,
                        "source_agent": source_agent,
                        "scanner_direction": scanner_direction,
                        "decision_direction": decision_direction,
                    },
                    "parameters": {"direction_match_required": True},
                })
                if not direction_matches:
                    raise DecisionUnavailable(
                        reason,
                        stage="proposal_preflight",
                        filter_trace=list(decision.filter_trace),
                    )

    missing_parameter_reasons = [
        key for key in _ENTRY_PARAMETER_KEYS
        if not isinstance(decision.parameter_rationale.get(key), str)
        or not decision.parameter_rationale[key].strip()
    ]
    if decision.parameter_rationale_required and missing_parameter_reasons:
        decision.filter_trace.append({
            "filter_name": "parameter_rationale_gate",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": "missing_parameter_rationale",
            "inputs": {"action": decision.action},
            "parameters": {"required_fields": list(_ENTRY_PARAMETER_KEYS)},
            "missing": missing_parameter_reasons,
        })
        logger.info(
            "proposal skipped: LLM decision omitted parameter reasons (%s)",
            ", ".join(missing_parameter_reasons),
        )
        raise DecisionUnavailable(
            "missing_parameter_rationale", stage="proposal_preflight"
        )

    overheated = _proposal_direction_overheated(decision.direction, decision.conviction)
    if overheated:
        decision.filter_trace.append({
            "filter_name": "direction_bias_brake",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": "direction_overheated",
            "inputs": {"direction": decision.direction, "conviction": decision.conviction},
            "parameters": {"min_sample": 15, "max_share": 0.95, "override_conviction": 75},
        })
        logger.info("proposal skipped by direction diversity brake: %s", overheated)
        return {"status": "skipped", "reason": "direction_overheated", "detail": overheated}
    decision.filter_trace.append({
        "filter_name": "direction_bias_brake",
        "stage": "proposal_submission",
        "decision": "passed",
        "reason_code": "direction_not_overheated",
        "inputs": {"direction": decision.direction, "conviction": decision.conviction},
        "parameters": {"min_sample": 15, "max_share": 0.95, "override_conviction": 75},
    })

    from aletheia.trading.market_data import latest_snapshot
    from aletheia.config.loader import load_config
    from aletheia.trust.guard_chain import run_guards, summarize

    cfg = load_config()

    # Resolve the instrument before looking up price. Spot and perp rows share the
    # canonical symbol but have different prices and freshness histories.
    market_type = decision.market_type
    if not market_type:
        market_type = "usdt_futures" if decision.direction == "short" else "spot"
    elif market_type == "spot" and decision.direction == "short":
        if decision.parameter_rationale_required:
            reason = "unsupported_operator_instrument_direction:spot_short"
            decision.filter_trace.append({
                "filter_name": "operator_entry_plan",
                "stage": "proposal_submission",
                "decision": "rejected",
                "reason_code": reason,
                "inputs": {
                    "direction": decision.direction,
                    "market_type": market_type,
                },
                "parameters": {"instrument_relabel_allowed": False},
            })
            raise DecisionUnavailable(
                reason,
                stage="operator_entry_plan",
                filter_trace=list(decision.filter_trace),
            )
        # Programmatic/historical objects retain the legacy compatibility path.
        market_type = "usdt_futures"

    snap: dict | None
    if execution_mode == "paper_internal":
        from aletheia.data.price_feed import (
            get_fresh_price_for_protective_close,
            protective_price_venue,
        )

        venue = protective_price_venue(
            execution_mode,
            configured_data_venue=cfg.market_data.ccxt_exchange,
        )
        price = float(
            get_fresh_price_for_protective_close(
                decision.symbol,
                market_type,
                venue=venue or "",
            )
            or 0
        )
        snap = _latest_exact_entry_snapshot(
            decision.symbol, market_type, venue or ""
        )
        if snap is None:
            snap = {
                "symbol": decision.symbol,
                "market_type": (
                    "perp" if market_type == "usdt_futures" else market_type
                ),
                "exchange": venue,
            }
        snap = dict(snap)
        snap["price"] = price or snap.get("price")
        snap["price_authority"] = "fresh_exact_market_mark"
    else:
        snap = latest_snapshot(decision.symbol, market_type=market_type)
        price = float((snap or {}).get("price") or 0)
    if not price:
        reason = (
            "market_price_stale"
            if execution_mode == "paper_internal" and snap.get("id")
            else "market_price_unavailable"
        )
        decision.filter_trace.append({
            "filter_name": "proposal_price_availability",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": reason,
            "inputs": {
                "symbol": decision.symbol,
                "market_type": market_type,
                "execution_mode": execution_mode,
                "venue": (snap or {}).get("exchange"),
            },
            "parameters": {
                "minimum_price": 0,
                "memory_max_age_seconds": 90,
                "database_max_age_seconds": 120,
            },
        })
        logger.warning("%s: %s", reason, decision.symbol)
        raise DecisionUnavailable(
            reason,
            stage="operator_entry_plan",
            filter_trace=list(decision.filter_trace),
        )
    decision.filter_trace.append({
        "filter_name": "proposal_price_availability",
        "stage": "proposal_submission",
        "decision": "passed",
        "reason_code": "market_price_available",
        "inputs": {"symbol": decision.symbol, "market_type": market_type},
        "parameters": {
            "minimum_price": 0,
            "exact_instrument_required": execution_mode == "paper_internal",
        },
    })

    # Guard chain is a PRE-RISK OBSERVABILITY layer (see trust/guard_chain docstring):
    # advisory only. It enriches the audit trail — guard_summary is attached to the
    # proposal rationale below (decision_meta["guards"]) — but MUST NOT hard-block the
    # PI's decision. The formal Risk Engine downstream (create_paper_proposal → risk
    # evaluation) is the real safety gate. Previously a guard "block" verdict silently
    # `return None`, which (a) contradicted guard_chain's own observability design and
    # (b) dropped the PI's decision with no detail surfaced to the caller (E1 fix).
    # Now: log the advisory, keep the verdict in the rationale, and let the proposal
    # flow to the Risk Engine, which decides.
    guard_verdicts = run_guards({
        "symbol": decision.symbol,
        "direction": decision.direction,
        "conviction": decision.conviction,
        "strategy_id": None,
    })
    guard_summary = summarize(guard_verdicts)
    if guard_summary["blocks"]:
        logger.info("guard_chain advisory (not blocking — Risk Engine is the gate): %s — %s",
                    decision.symbol, guard_summary["details"])

    notional = decision.position_size_usdt or 25.0
    quantity = round(notional / price, 6) or 0.000001

    # Phase 4 execution-school discriminator. Derived from the DECISION-level
    # signal_source (Phase 2): a 'serenity'-sourced decision opens a 'serenity'
    # position with the wider long-horizon stop. The risk engine stays school-
    # agnostic — only position_manager branches on it.
    trade_school = "serenity" if getattr(decision, "signal_source", None) == "serenity" else "short_term"

    def _reject_operator_entry_plan(
        reason: str,
        fields: list[str],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        inputs = {
            "action": decision.action,
            "direction": decision.direction,
            "market_type": market_type,
        }
        if diagnostics:
            inputs.update(diagnostics)
        decision.filter_trace.append({
            "filter_name": "operator_entry_plan",
            "stage": "proposal_submission",
            "decision": "rejected",
            "reason_code": reason,
            "inputs": inputs,
            "parameters": {
                "required_source": "system_operator",
                "fresh_entry_mark": price,
                "min_risk_reward": ENTRY_PLAN_MIN_RISK_REWARD,
                "max_risk_reward": ENTRY_PLAN_MAX_RISK_REWARD,
            },
            "missing_or_invalid": fields,
        })
        raise DecisionUnavailable(
            reason,
            stage="operator_entry_plan",
            filter_trace=list(decision.filter_trace),
        )

    stop_price = _valid_protective_price(decision.stop_price)
    if decision.parameter_rationale_required and stop_price is None:
        _reject_operator_entry_plan(
            "incomplete_operator_entry_plan:stop_price", ["stop_price"]
        )
    # Programmatic/legacy decisions retain their historical config fallback.
    # A parsed System Operator trade must keep its authored stop; invalid
    # geometry is retryable unavailability, never a silently different plan.
    _stop_from_operator = stop_price is not None
    if stop_price is not None:
        if decision.direction == "long" and stop_price >= price:
            if decision.parameter_rationale_required:
                _reject_operator_entry_plan(
                    "invalid_operator_entry_plan_geometry:stop_price", ["stop_price"]
                )
            stop_price = None
            _stop_from_operator = False
        elif decision.direction == "short" and stop_price <= price:
            if decision.parameter_rationale_required:
                _reject_operator_entry_plan(
                    "invalid_operator_entry_plan_geometry:stop_price", ["stop_price"]
                )
            stop_price = None
            _stop_from_operator = False
    if stop_price is None:
        stop_pct = (
            cfg.serenity.stop_loss_pct
            if trade_school == "serenity"
            else cfg.trading.default_stop_loss_pct
        )
        if decision.direction == "long":
            stop_price = round(price * (1 - stop_pct / 100.0), 8)
        else:
            stop_price = round(price * (1 + stop_pct / 100.0), 8)

    # Commit take-profit + reward:risk before execution. take_profit is the
    # operator's explicit price for parsed model decisions. Programmatic/legacy
    # decisions retain the historical configured fallback. risk_reward is exact
    # reward-per-unit / risk-per-unit from the committed entry/stop/tp.
    take_profit = _valid_protective_price(decision.take_profit)
    if decision.parameter_rationale_required and take_profit is None:
        _reject_operator_entry_plan(
            "incomplete_operator_entry_plan:take_profit", ["take_profit"]
        )
    if take_profit is not None:
        if decision.direction == "long" and take_profit <= price:
            if decision.parameter_rationale_required:
                _reject_operator_entry_plan(
                    "invalid_operator_entry_plan_geometry:take_profit", ["take_profit"]
                )
            take_profit = None
        elif decision.direction == "short" and take_profit >= price:
            if decision.parameter_rationale_required:
                _reject_operator_entry_plan(
                    "invalid_operator_entry_plan_geometry:take_profit", ["take_profit"]
                )
            take_profit = None
    if take_profit is None:
        tp_pct = cfg.trading.default_take_profit_pct
        if decision.direction == "long":
            take_profit = round(price * (1 + tp_pct / 100.0), 8)
        else:
            take_profit = round(price * (1 - tp_pct / 100.0), 8)
    risk_reward = calculate_entry_plan_risk_reward(
        price,
        stop_price,
        take_profit,
    )
    if (
        decision.parameter_rationale_required
        and not entry_plan_risk_reward_is_plausible(risk_reward)
    ):
        _reject_operator_entry_plan(
            OPERATOR_RISK_REWARD_OUTSIDE_ENVELOPE,
            ["risk_reward"],
            diagnostics={
                "entry_price": price,
                "stop_price": stop_price,
                "take_profit": take_profit,
                "risk_reward": risk_reward,
            },
        )

    # Capture the same exact venue + physical market that supplied the entry
    # mark. Parsed System Operator trades fail closed when that instrument has
    # no fresh microstructure evidence or is explicitly non-tradable. This runs
    # after entry-plan validation so existing typed plan errors keep precedence,
    # but still before immutable Proposal construction. Legacy programmatic
    # callers retain compatibility while receiving exact attribution.
    from aletheia.trading.entry_snapshot import capture_attribution_snapshot

    attribution_snapshot = capture_attribution_snapshot(
        decision.symbol,
        exchange=str((snap or {}).get("exchange") or ""),
        market_type=market_type,
    )
    if decision.parameter_rationale_required:
        market_context = attribution_snapshot.get("market_context")
        if not isinstance(market_context, dict):
            market_context = {}
        instrument = market_context.get("instrument")
        if not isinstance(instrument, dict):
            instrument = {}
        tradability = market_context.get("tradability")
        if not isinstance(tradability, dict):
            tradability = {}
        expected_market = (
            "perp" if market_type in {"usdt_futures", "swap"} else market_type
        )
        expected_exchange = str((snap or {}).get("exchange") or "").lower()
        identity_matches = (
            bool(expected_exchange)
            and instrument.get("symbol") == decision.symbol
            and str(instrument.get("exchange") or "").lower() == expected_exchange
            and instrument.get("market_type") == expected_market
            and str(tradability.get("exchange") or "").lower() == expected_exchange
        )
        if not identity_matches or "is_tradable" not in tradability:
            tradability_reason = "market_tradability_unavailable"
        elif tradability.get("is_tradable") is not True:
            tradability_reason = "market_not_tradable"
        else:
            tradability_reason = None
        decision.filter_trace.append({
            "filter_name": "market_tradability_gate",
            "stage": "proposal_submission",
            "decision": "rejected" if tradability_reason else "passed",
            "reason_code": tradability_reason or "market_tradable",
            "inputs": {
                "symbol": decision.symbol,
                "exchange": expected_exchange or None,
                "market_type": expected_market,
                "score": tradability.get("score"),
                "spread_bps": tradability.get("spread_bps"),
                "depth_10bps_usd": tradability.get("depth_10bps_usd"),
                "volume_24h_usd": tradability.get("volume_24h_usd"),
                "is_tradable": tradability.get("is_tradable"),
            },
            "parameters": {
                "exact_instrument_required": True,
                "missing_context_policy": "defer",
            },
        })
        if tradability_reason:
            raise DecisionUnavailable(
                tradability_reason,
                stage="operator_entry_plan",
                filter_trace=list(decision.filter_trace),
            )

    # Leverage only applies to futures. Clamp the PI's request to the per-asset-tier
    # cap (the risk engine enforces the same cap as a hard reject — clamping here keeps
    # the proposal inside the gate so a sane futures trade is not silently rejected for
    # an over-eager leverage number). Spot carries no leverage.
    leverage: int | None = None
    leverage_cap: int | None = None
    if market_type == "usdt_futures" and decision.leverage and decision.leverage > 1:
        from aletheia.trading.instruments import get_max_leverage
        is_live = "live" in (execution_mode or "")
        leverage_cap = get_max_leverage(decision.symbol, is_live, cfg)
        leverage = max(1, min(decision.leverage, leverage_cap))

    entry_parameters = _build_entry_parameter_snapshot(
        decision=decision,
        market_type=market_type,
        entry_price=price,
        quantity=quantity,
        stop_price=stop_price,
        take_profit=take_profit,
        requested_notional=notional,
        leverage=leverage,
        leverage_cap=leverage_cap,
        market_snapshot=snap,
    )

    decision_meta = {
        "conviction": decision.conviction,
        "debate_ran": decision.debate_ran,
        "debate_initial_conviction": decision.debate_initial_conviction,
        "debate_bull": decision.debate_bull_confidence,
        "debate_bear": decision.debate_bear_confidence,
        "debate_adjustment": decision.debate_adjustment,
        "supporting_signals": decision.supporting_signals[:5],
        "opposing_signals": decision.opposing_signals[:5],
        "bear_case": decision.bear_case,
        "key_assumptions": decision.key_assumptions[:3],
        # Legacy keys kept for existing dashboard/feedback readers.
        "supporting": decision.supporting_signals[:5],
        "opposing": decision.opposing_signals[:5],
        # R0: scanner_family is parsed back out of this JSON by
        # analytics/reasoning_feedback.py and fed to the PI prompt. Hard-coded
        # absence here is why reasoning_feedback scanner was permanently unknown.
        "scanner_family": decision.scanner_family,
        "sizing_learning": decision.sizing_trace,
        "guards": guard_summary,
        # Immutable proposal evidence for why each entry value was used.  The
        # proposal hash covers agent_rationale, so this snapshot cannot be
        # silently changed without breaking the Trading-as-Git chain.
        "entry_parameters": entry_parameters,
    }
    from aletheia.trading.trade_motivation import build_trade_motivation

    decision.trade_motivation = build_trade_motivation(
        decision, decision.evidence_manifest
    )
    decision_meta["trade_motivation"] = decision.trade_motivation
    # Cap ONLY the human thesis; append the full JSON AFTER capping so the JSON
    # is always whole. Do NOT re-truncate the combined string (the old `[:500]`
    # bug sliced the JSON into unparseable garbage — see _MAX_RATIONALE_TEXT).
    thesis = decision.reasoning or ""
    if len(thesis) > _MAX_RATIONALE_TEXT:
        thesis = thesis[:_MAX_RATIONALE_TEXT].rstrip() + "…"
    rationale = f"{thesis}\n---\n{json.dumps(decision_meta)}"

    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol=decision.symbol,
        market_type=market_type,
        direction=decision.direction,
        order_type="market" if execution_mode == "paper_internal" else "limit",
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        risk_reward=risk_reward,
        execution_mode=execution_mode,
        strategy_id=decision.strategy_id,
        opportunity_id=decision.opportunity_id,
        agent_rationale=rationale,
        attribution_snapshot=json.dumps(attribution_snapshot, default=str),
        trade_school=trade_school,
        # Exit-intent plan M2: carry the Operator's holding horizon and stop
        # provenance to the position at open. stop_source='operator' iff the AI
        # authored the stop (else config default filled it in above).
        expected_horizon_hours=decision.expected_horizon_hours,
        stop_source="operator" if _stop_from_operator else "default",
    )
    return {"status": "prepared", "proposal_id": proposal.id, "proposal": proposal}


async def submit_decision_as_proposal(decision: TradingDecision, execution_mode: str) -> dict:
    """Compatibility entry point that prepares, persists, and executes one Proposal."""
    prepared = await prepare_decision_as_proposal(decision, execution_mode)
    proposal = prepared.get("proposal")
    if proposal is None:
        return prepared

    from aletheia.execution.paper_tools import create_paper_proposal

    return create_paper_proposal(
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
        stop_price=proposal.stop_price,
        take_profit=proposal.take_profit,
        risk_reward=proposal.risk_reward,
        execution_mode=proposal.execution_mode,
        strategy_id=proposal.strategy_id,
        opportunity_id=proposal.opportunity_id,
        agent_rationale=proposal.agent_rationale,
        trade_school=proposal.trade_school,
    )


_ENTRY_PARAMETER_KEYS = (
    "entry_price",
    "stop_loss",
    "take_profit",
    "notional_usdt",
    "leverage",
)


def _build_entry_parameter_snapshot(
    *,
    decision: TradingDecision,
    market_type: str,
    entry_price: float,
    quantity: float,
    stop_price: float,
    take_profit: float,
    requested_notional: float,
    leverage: int | None,
    leverage_cap: int | None,
    market_snapshot: dict | None = None,
) -> dict[str, dict]:
    """Build the immutable, honest explanation of the final entry values.

    AI provides only short reasons.  This function owns the numerical facts and
    their source labels after defaults/caps are applied, so a dashboard cannot
    mistake a configured fallback or a risk-cap clamp for an AI-selected value.
    """
    supplied = decision.parameter_rationale if isinstance(decision.parameter_rationale, dict) else {}
    snapshot = market_snapshot or {}

    def reason_for(key: str) -> str | None:
        value = supplied.get(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value[:600] if value else None

    actual_notional = round(quantity * entry_price, 8)
    estimated_loss = round(quantity * abs(entry_price - stop_price), 8)
    requested_leverage = decision.leverage if market_type == "usdt_futures" else None
    final_leverage = leverage or 1
    if market_type != "usdt_futures":
        leverage_source = "not_applicable_spot"
    elif requested_leverage and leverage_cap and final_leverage != requested_leverage:
        leverage_source = "risk_cap_clamped"
    elif requested_leverage and requested_leverage > 1:
        leverage_source = "ai_requested"
    else:
        leverage_source = "default_1x"

    return {
        "entry_price": {
            "final_value": entry_price,
            "value_source": snapshot.get("price_authority") or "market_snapshot",
            "market_snapshot_id": snapshot.get("id"),
            "market_type": snapshot.get("market_type"),
            "snapshot_at": snapshot.get("snapshot_at"),
            "reason": reason_for("entry_price"),
        },
        "stop_loss": {
            "final_value": stop_price,
            "value_source": "ai_requested"
            if _valid_protective_price(decision.stop_price) is not None
            else "default_config",
            "distance_pct": round(abs(entry_price - stop_price) / entry_price * 100, 6) if entry_price else None,
            "reason": reason_for("stop_loss"),
        },
        "take_profit": {
            "final_value": take_profit,
            "value_source": "ai_requested"
            if _valid_protective_price(decision.take_profit) is not None
            else "default_config",
            "distance_pct": round(abs(take_profit - entry_price) / entry_price * 100, 6) if entry_price else None,
            "reason": reason_for("take_profit"),
        },
        "notional_usdt": {
            "requested_value": requested_notional,
            "actual_value": actual_notional,
            "estimated_loss_at_stop_usdt": estimated_loss,
            "value_source": "ai_requested" if decision.position_size_usdt is not None else "default_config",
            "reason": reason_for("notional_usdt"),
        },
        "leverage": {
            "requested_value": requested_leverage,
            "final_value": final_leverage,
            "maximum_allowed": leverage_cap,
            "estimated_margin_usdt": round(actual_notional / final_leverage, 8) if final_leverage else None,
            "value_source": leverage_source,
            "reason": reason_for("leverage"),
        },
    }


def _proposal_direction_overheated(
    direction: str | None,
    conviction: int,
    *,
    window_hours: int = 24,
    min_sample: int = 15,
    max_share: float = 0.95,
    override_conviction: int = 75,
) -> str | None:
    """Return a skip reason when native proposals are one-sided.

    This is a pre-proposal quality brake, not a risk/execution gate. It prevents
    normal-conviction decisions from adding yet another same-direction proposal
    when the recent proposal stream is already overwhelmingly one-sided.
    """
    if direction not in ("long", "short") or conviction >= override_conviction:
        return None
    try:
        from datetime import datetime, timedelta, timezone
        from collections import Counter
        from aletheia.storage.sqlite import get_db

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        rows = get_db().execute(
            "SELECT direction FROM trade_proposals "
            "WHERE created_at >= ? AND source != 'adopted' AND direction IS NOT NULL",
            (cutoff,),
        ).fetchall()
        dirs = [r["direction"] for r in rows if r["direction"] in ("long", "short")]
        total = len(dirs)
        if total < min_sample:
            return None
        counts = Counter(dirs)
        top_dir, top_n = counts.most_common(1)[0]
        share = top_n / total if total else 0.0
        if top_dir == direction and share > max_share:
            return (
                f"{window_hours}h native proposals are {top_n}/{total} {top_dir} "
                f"({share:.0%}); conviction {conviction} below override {override_conviction}"
            )
    except Exception:
        return None
    return None


# ── LLM calls ─────────────────────────────────────────────────────────────────

async def _assess_opportunity(ctx: dict) -> dict | None:
    """Call LLM with full trading context; return raw decision dict."""
    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.router import get_provider_for_tier

    system = (
        "You are the Trading Decision Authority for an autonomous paper trading system. "
        "You receive a structured three-layer context: (1) current environment snapshot, "
        "(2) your recent 7-day performance, (3) accumulated wisdom from past trades. "
        "The risk/guard system operates independently after your decision — "
        "focus on WHAT to trade, not HOW MUCH. Return a JSON object matching the schema."
    )
    user = _build_assessment_prompt(ctx)
    result, err = await llm_structured(
        system,
        user,
        schema=_DummySchema(_DECISION_SCHEMA),
        provider=get_provider_for_tier("deep"),
        caller="system_operator.assess_opportunity",
        reasoning_effort="none",
    )
    if err or result is None:
        logger.warning("LLM assessment failed: %s", err)
        error_kind = str(err or "").partition(":")[0]
        if error_kind == "structured_schema_invalid":
            raise DecisionUnavailable(
                f"operator_{error_kind}",
                stage="operator_output_validation",
            )
        raise DecisionUnavailable("llm_assessment_unavailable")
    return result


async def _reassess_direct_skip(
    ctx: dict,
    initial_raw: dict,
    validation_reason: str,
) -> dict | None:
    """Run the single bounded correction/counterfactual pass for a direct skip.

    This is deliberately not an instruction to trade. The reviewer may confirm
    a skip, but only with typed candidate-specific adverse evidence, or may
    return a normal trade decision which still faces debate, conviction, and
    every downstream Risk/Execution guard.
    """
    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.router import get_provider_for_tier

    system = (
        "You are the independent counterfactual reviewer for a paper-trading "
        "decision. Challenge the prior answer once. Do not force a trade. A skip "
        "is valid only for candidate-specific adverse evidence; missing history, "
        "insufficient matured outcomes, or a rank-order score alone is neutral. "
        "Return one complete JSON decision matching the schema."
    )
    # A direct-skip challenge is blind to the first model's prose. Showing the
    # same provider its own answer made relabelling look like independent proof.
    # Malformed-output repair still receives the exact syntax issue and payload.
    is_direct_skip = str(initial_raw.get("action") or "").lower() == "skip"
    repair_context = (
        # For a confirmed direct skip, pass only the gate-level rejection fact — not
        # the first model's prose — to preserve independent counterfactual review while
        # giving the reviewer a concrete signal to choose a different path.
        f"Gate rejection: {validation_reason} — the required numeric threshold was not "
        f"met by the data in the current context. Verify the exact threshold before "
        f"re-using this code, or use skip_kind=insufficient_evidence if evidence is "
        f"merely absent or below the required floor.\n\n"
    ) if is_direct_skip else (
        f"Validation issue: {validation_reason}\n"
        f"Malformed prior payload: {json.dumps(initial_raw, sort_keys=True, default=str)}\n\n"
    )
    user = (
        f"{repair_context}{_build_assessment_prompt(ctx)}\n\n"
        "Reassess the same pending candidate. Confirm skip only with skip_kind="
        "adverse_candidate_evidence, at least one allowed adverse_reason_code, "
        "and at least one concrete adverse_evidence item. Otherwise return a "
        "complete trade decision if warranted; if evidence is merely insufficient, "
        "return skip_kind=insufficient_evidence so the caller can defer honestly."
    )
    result, err = await llm_structured(
        system,
        user,
        schema=_DummySchema(_DECISION_SCHEMA),
        provider=get_provider_for_tier("deep"),
        caller="system_operator.reassess_direct_skip",
        reasoning_effort="none",
    )
    if err or result is None:
        raise DecisionUnavailable(
            "direct_skip_reassessment_unavailable",
            stage="operator_skip_validation",
        )
    return result


def _direct_skip_confirmation_required(ctx: dict) -> bool:
    policy = ctx.get("skip_policy") or {}
    return bool(policy.get("direct_skip_confirmation_required"))


def _has_submitting_final(decision: TradingDecision) -> bool:
    """A stored submission owns recovery; a fresh skip cannot supersede it."""
    if not decision.opportunity_id:
        return False
    try:
        from aletheia.storage.repositories import find_final_pi_decision_for_opportunity

        existing = find_final_pi_decision_for_opportunity(
            decision.opportunity_id,
            decision.signal_source or "market_observer",
        )
    except Exception:
        return False
    return bool(existing and existing.get("proposal_status") == "submitting")


def _direct_skip_validation_reason(
    decision: TradingDecision, ctx: dict | None = None
) -> str | None:
    """Return why a candidate-scoped direct skip is not terminally authoritative."""
    if not decision.opportunity_id:
        return "missing_candidate_identity"
    if decision.skip_kind != "adverse_candidate_evidence":
        return "neutral_or_missing_skip_kind"
    codes = [
        str(code).strip()
        for code in decision.adverse_reason_codes
        if str(code).strip()
    ]
    if not codes:
        return "missing_adverse_reason_code"
    if any(code not in _ADVERSE_SKIP_REASON_CODES for code in codes):
        return "unsupported_adverse_reason_code"
    evidence = [
        str(item).strip()
        for item in decision.adverse_evidence
        if str(item).strip()
    ]
    if not evidence:
        return "missing_candidate_adverse_evidence"
    if ctx is not None:
        from aletheia.agent_shell.decision_facts import validate_adverse_claims

        claims, invalid_reason = validate_adverse_claims(decision, ctx)
        if invalid_reason:
            decision.validated_adverse_claims = []
            return invalid_reason
        decision.validated_adverse_claims = claims
    return None


async def _run_debate(symbol: str, direction: str | None, ctx: dict) -> DebateResult | None:
    """Run Bull/Bear adversarial debate for a symbol."""
    import asyncio
    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.router import get_provider_for_tier

    bull_user = _build_bull_prompt(symbol, direction, ctx)
    bear_user = _build_bear_prompt(symbol, direction, ctx)
    system_bull = "You are a bull analyst. Return a JSON object matching the schema."
    system_bear = "You are a bear analyst. Return a JSON object matching the schema."

    _deep_provider = get_provider_for_tier("deep")
    (bull_result, bull_err), (bear_result, bear_err) = await asyncio.gather(
        llm_structured(
            system_bull,
            bull_user,
            schema=_DummySchema(_BULL_SCHEMA),
            provider=_deep_provider,
            caller="system_operator.debate_bull",
            reasoning_effort="none",
        ),
        llm_structured(
            system_bear,
            bear_user,
            schema=_DummySchema(_BEAR_SCHEMA),
            provider=_deep_provider,
            caller="system_operator.debate_bear",
            reasoning_effort="none",
        ),
    )
    if bull_err or bear_err or not bull_result or not bear_result:
        logger.debug("debate failed bull_err=%s bear_err=%s", bull_err, bear_err)
        raise DecisionUnavailable(
            "required_debate_unavailable", stage="operator_debate"
        )

    bull_conf = int(bull_result.get("confidence", 50))
    bear_conf = int(bear_result.get("confidence", 50))

    # NOTE: the bull/bear advantage is applied in evaluate_trading_opportunity
    # (initial_conviction + advantage*0.3); DebateResult intentionally carries the
    # raw confidences, not a pre-synthesized verdict. (Removed the dead
    # final_conviction field that was computed here but never read.)
    return DebateResult(
        bull_confidence=bull_conf,
        bull_thesis=bull_result.get("thesis", ""),
        bull_evidence=bull_result.get("evidence", []),
        bear_confidence=bear_conf,
        bear_thesis=bear_result.get("thesis", ""),
        bear_evidence=bear_result.get("evidence", []),
    )


# ── sizing ────────────────────────────────────────────────────────────────────

def _compute_position_size(
    conviction: int,
    symbol: str | None = None,
    direction: str | None = None,
    paper_trade_count: int = 0,
    opportunity_id: str | None = None,
    learning_candidate_key: str | None = None,
    sizing_trace: dict | None = None,
) -> float:
    """Compute notional position size from conviction score (0-100).

    P1-5: paper_trade_count is passed through to vol_target_size so new strategies
    start at 0.1 Kelly and ramp up as evidence accumulates (30+ trades = full Kelly).
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    if symbol:
        from aletheia.trading.sizing import vol_target_size
        return vol_target_size(
            symbol,
            conviction=conviction,
            direction=direction,
            paper_trade_count=paper_trade_count,
            candidate_key=f"opportunity:{opportunity_id}" if opportunity_id else None,
            outcome_candidate_key=(
                f"{learning_candidate_key}:4h" if learning_candidate_key else None
            ),
            opportunity_id=opportunity_id,
            sizing_trace=sizing_trace,
        )
    from aletheia.trading.sizing import compute_position_size
    base = cfg.trading.default_notional_usdt
    return compute_position_size(conviction, base)


def _get_strategy_paper_trade_count(strategy_id: str | None) -> int:
    """Return closed paper/demo positions count for a strategy — used for sample-aware Kelly."""
    if not strategy_id:
        return 0
    try:
        from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
        return sum(
            1 for row in eligible_strategy_outcomes()
            if str(row.get("strategy_id") or "") == strategy_id
            and "live" not in str(row.get("execution_mode") or "")
        )
    except Exception:
        return 0


def _proposal_bias_brake_reason(decision: TradingDecision) -> str | None:
    """Stop ordinary-conviction proposals from amplifying a one-sided 24h run.

    This is a proposal-layer guardrail, not a trading signal. It never forces the
    opposite direction; it only turns another same-direction trade into skip when
    the recent proposal stream is already dominated by that direction.
    """
    if decision.action != "trade":
        return None
    if decision.direction not in ("long", "short"):
        return None
    if decision.conviction >= _BIAS_BRAKE_BYPASS_CONVICTION:
        return None
    try:
        from aletheia.storage.sqlite import get_db
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=_BIAS_BRAKE_WINDOW_HOURS)).isoformat()
        rows = list(get_db().execute(
            "SELECT direction, COUNT(*) n FROM trade_proposals "
            "WHERE created_at >= ? AND source != 'adopted' "
            "GROUP BY direction ORDER BY n DESC",
            (cutoff,),
        ))
    except Exception as e:
        logger.debug("proposal bias brake skipped: %s", e)
        return None

    total = sum(int(r["n"]) for r in rows)
    if total < _BIAS_BRAKE_MIN_PROPOSALS or not rows:
        return None
    top_dir = rows[0]["direction"]
    top_n = int(rows[0]["n"])
    top_pct = top_n / total if total else 0
    if top_dir != decision.direction or top_pct <= _BIAS_BRAKE_MAX_DIRECTION_PCT:
        return None
    return (
        f"directional proposal brake: recent {total} proposals are "
        f"{top_pct:.0%} {top_dir}; waiting for cleaner evidence before adding another {top_dir}"
    )


# ── prompt builders ───────────────────────────────────────────────────────────

_OI_DIV_LABEL = {1: "hidden_acc", -1: "weak_rally", 0: ""}

def _fmt_family_edge(edge: dict | None) -> str:
    """Format per-(scanner_family, direction) live edge for the opportunity line.

    Thesis evidence (P1a): shows the PI the actual settled track record of this
    candidate's scanner family + direction so it weighs the pick against real
    outcomes, not just the current signal. Empty string when no settled data yet
    (never fabricate an edge from zero samples). n>=5 lends the number weight.
    """
    if not edge or not edge.get("n"):
        return ""
    n = edge["n"]
    wr = edge.get("win_rate")
    ar = edge.get("avg_return")
    wr_s = f"{wr:.0f}%WR" if wr is not None else "?WR"
    ar_s = f"{ar:+.2f}%avg_net" if ar is not None else ""
    weak = " (thin)" if n < 5 else ""
    return f" {{edge:{n}T {wr_s} {ar_s}{weak}}}"


def _fmt_position_x_context(context: dict | None) -> str:
    if not context or context.get("status") != "ok":
        return ""
    rendered = str(context.get("rendered") or "").strip()
    return f" | {rendered}" if rendered else ""


def _direction_skew_note(rows: list[dict], label: str) -> str:
    dirs = [
        str(r.get("direction") or r.get("direction_bias") or "").lower()
        for r in rows
    ]
    dirs = [d for d in dirs if d in ("long", "short")]
    if len(dirs) < 5:
        return ""
    long_n = sum(1 for d in dirs if d == "long")
    short_n = sum(1 for d in dirs if d == "short")
    top = max(long_n, short_n)
    if top / len(dirs) < 0.8:
        return ""
    top_dir = "long" if long_n >= short_n else "short"
    return (
        f"  Direction diversity check: {label} is {top}/{len(dirs)} {top_dir}. "
        "A trending market CAN be one-sided; do not treat skew as a veto. "
        "Only skip if the setup lacks independent confirmation."
    )


def _fmt_feature_line(f: dict) -> str:
    """Format one universe feature entry for the assessment prompt."""
    sym = f["symbol"]
    rsi = f.get("rsi_14")
    rsi15 = f.get("rsi_15m")
    rsi4h = f.get("rsi_4h")
    atr = f.get("atr_pct")
    r1 = f.get("return_1h")
    r4 = f.get("return_4h")
    r24 = f.get("return_24h")
    fr_str = _fmt_funding(f.get("funding_rate"))
    oi = f.get("oi_delta_1h")
    rs = f.get("relative_strength_24h")
    vr = f.get("vol_ratio")
    tkr = f.get("taker_buy_ratio")
    div = f.get("oi_price_divergence")
    crowding = f.get("crowding_signal", False)

    if rsi is None and atr is None:
        return f"  {sym}: price={f.get('price', '?')}"
    rsi_s = f"{rsi:.0f}" if rsi is not None else "?"
    atr_s = f"{atr:.1f}" if atr is not None else "?"
    base = (
        f"  {sym} [{f.get('market_type', '?')}]: RSI15m={rsi15 if rsi15 is not None else '?'} "
        f"RSI1h={rsi_s} RSI4h={rsi4h if rsi4h is not None else '?'} ATR1h={atr_s}% "
        f"1h={f'{r1:+.1f}%' if r1 is not None else '?'} "
        f"4h={f'{r4:+.1f}%' if r4 is not None else '?'} "
        f"24h={f'{r24:+.1f}%' if r24 is not None else '?'} "
        f"FR={fr_str} OI_Δ={oi if oi is not None else '?'}"
    )
    extras = []
    if rs is not None:
        extras.append(f"RS24h={rs:+.1f}%")
    if vr is not None:
        extras.append(f"vol_r={vr:.1f}x")
    if tkr is not None:
        extras.append(f"taker={tkr:.2f}")
    if div and div != 0:
        extras.append(_OI_DIV_LABEL.get(div, ""))
    range_atr = f.get("last_candle_range_atr_15m")
    if range_atr is not None:
        extras.append(f"15m_range={range_atr:.2f}ATR")
    if f.get("bb_squeeze"):
        extras.append("BB_squeeze")
    if crowding:
        extras.append("CROWDED")
    return base + (" [" + " ".join(x for x in extras if x) + "]" if extras else "")


_SIGNAL_RULES = """\
Signal interpretation (same logic as the scanner chain):
  FR>+0.01%: crowded longs → bearish bias for trend; bullish for mean-reversion
  FR<-0.01%: crowded shorts → bullish bias for trend; bearish for mean-reversion
  OI_div=hidden_acc (OI↑+price↓): possible accumulation → potential long setup
  OI_div=weak_rally (OI↓+price↑): distribution risk → potential short setup
  taker>1.2: aggressive buyers confirm momentum; taker<0.8: aggressive sellers
  vol_ratio>2x: volume breakout — confirms any directional trade
  CROWDED: high funding + large OI move together — elevated reversal risk"""


def _build_assessment_prompt(ctx: dict) -> str:
    """Build the three-layer assessment prompt from enriched trading context."""
    layer1 = ctx.get("layer1", {})
    layer2 = ctx.get("layer2", {})
    layer3 = ctx.get("layer3", {})

    # ── Layer 1: Environment Snapshot ──
    positions = layer1.get("positions", [])
    positions_meta = layer1.get("positions_meta") or {}
    total_positions = int(positions_meta.get("total", len(positions)))
    visible_positions = int(
        positions_meta.get("prompt_visible_count", min(len(positions), 5))
    )
    hidden_positions = int(
        positions_meta.get(
            "prompt_overflow_count", max(total_positions - visible_positions, 0)
        )
    )
    position_count_line = (
        f"Open positions: {total_positions} total; showing {visible_positions}; "
        f"{hidden_positions} not shown"
    )
    pos_lines = "\n".join(
        f"  id={p.get('id', '?')} {p.get('symbol', '?')} "
        f"[{p.get('market_type', '?')}] {p.get('direction', '?')} "
        f"qty={p.get('quantity', '?')} entry={p.get('entry_price', '?')} "
        f"uPnL={p.get('unrealized_pnl', '?')} hold={p.get('hold_hours', '?')}h"
        f"{_fmt_position_x_context(p.get('structure_context'))}"
        for p in positions[:5]
    ) or "  (none)"

    balance = layer1.get("balance")
    if balance:
        # available_usdt can be None on the cached-exchange branch.
        # equity_usdt is always a float for paper_internal (issue #316), but may
        # still be None on the exchange branch — render "unknown" to avoid
        # crashing the evaluation cycle on a NoneType format spec.
        _equity = balance.get("equity_usdt")
        _available = balance.get("available_usdt")
        _equity_str = f"${_equity:.0f}" if _equity is not None else "unknown (unsettled closes)"
        _available_str = f"${_available:.0f}" if _available is not None else "unknown"
        balance_line = f"  Equity: {_equity_str} | Available: {_available_str}"
    else:
        balance_line = "  (unavailable)"

    today_pnl = layer1.get("today_pnl")
    pnl_line = f"  Today realized: ${today_pnl:+.2f}" if today_pnl is not None else "  (no trades today)"

    regime = layer1.get("regime")
    # "long_bias=25%, short_bias=75%" read like directional VETOES to the LLM
    # (i.e. "never go long"); they are probability weights. Renamed to *_weight
    # with an explicit note so the LLM treats them as priors, not prohibitions.
    regime_line = (
        f"  {regime['state'].upper()} (conf={regime['confidence']:.0%}, "
        f"long_weight={regime['long_bias']:.0%}, short_weight={regime['short_bias']:.0%}) "
        f"[probability weights, not vetoes]"
        if regime else "  (unknown)"
    )

    features = layer1.get("features", [])
    feature_lines = "\n".join(
        _fmt_feature_line(f) if f.get('rsi_14') is not None else
        f"  {f['symbol']}: price={f.get('price', '?')}"
        for f in features[:10]
    ) or "  (no data)"

    risk = layer1.get("risk", {})

    # ── Layer 2: Short-term Memory ──
    reflection = layer2.get("reflection")

    stats = layer2.get("outcome_stats")
    _bypass = False
    try:
        from aletheia.config.loader import load_config
        _bypass = load_config().agent.bypass_distress_gate
    except Exception:
        pass
    if _bypass:
        stats_block = "  (performance stats suppressed — demo/test mode)"
    elif stats:
        # Render win_rate as "NW/ML (X% win rate)" — a bare "46.6% win rate" next
        # to a negative PnL was misread by the LLM as "-45.8% drawdown" and drove
        # a phantom-crisis all-skip. Explicit W/L counts make the metric
        # unambiguous. PnL labelled "realized_PnL" to distinguish from drawdown.
        stats_block = (
            f"  7d: {stats['total_trades']} trades, "
            f"{stats['wins']}W/{stats['losses']}L ({stats['win_rate']}% win rate), "
            f"realized_PnL=${stats['total_pnl']:+.2f}, avg=${stats['avg_pnl_per_trade']:+.2f}/trade\n"
            f"  Best: ${stats['best_win']:+.2f} | Worst: ${stats['worst_loss']:+.2f}"
        )
    else:
        stats_block = "  (no recent trades)"

    per_sym = layer2.get("per_symbol", [])
    sym_lines = "" if _bypass else (
        "\n".join(
            f"  {s['symbol']}: {s['trades']}T {s['wins']}W PnL=${s['pnl']:+.2f}"
            for s in per_sym[:5]
        ) or ""
    )

    strat_perf = layer2.get("strategies", [])
    strat_lines = "  (strategy performance suppressed — demo/test mode)" if _bypass else (
        "\n".join(
            f"  {s['name']}: WR={s.get('win_rate', '?')}% PnL={s.get('total_pnl', '?')} trades={s.get('trade_count', 0)}"
            for s in strat_perf[:5]
        ) or "  (none active)"
    )

    trade_analytics_lines = "" if _bypass else _fmt_trade_analytics(layer2.get("trade_analytics"))

    # ── Layer 3: Long-term Wisdom ──
    lessons = layer3.get("lessons", [])
    lesson_lines = "\n".join(
        f"  [{l['type']}] {l.get('symbol') or 'general'}: {l['content']}"
        for l in lessons[:7]
    ) or "  (none)"

    guards = layer3.get("guard_candidates", [])
    guard_lines = "\n".join(
        f"  ⚠ {g.get('symbol', '?')}: {g['content']}"
        for g in guards[:3]
    ) or ""

    serenity_opps = layer3.get("serenity_opportunities", [])

    def bounded_serenity_text(value, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + " ... [display bounded; full fact persisted]"

    # D2: show the INDEPENDENTLY assessed tier (authoritative gate), not the self-grade.
    # Phase 2: also surface the deterministic score/bucket/evidence_count so the PI
    # weighs research picks on the same transparent scale the Serenity scorer uses.
    # EQ-R: equity stock tokens are observation-only — labelled [EQUITY/OBS] so the PI
    # knows not to include them as order candidates.
    _serenity_lines: list[str] = []
    for o in serenity_opps:
        _is_eq = o.get("asset_class") == "equity" or not o.get("execution_eligible", True)
        _eq_tag = " [EQUITY/OBS — research context only, do not trade]" if _is_eq else ""
        _serenity_lines.append(
            f"  {o['symbol']}{_eq_tag} {o.get('direction', '?')} "
            f"[conf={o.get('confidence_score', '?')} tier={o.get('assessed_tier') or o.get('evidence_tier', '?')} "
            f"score={o.get('score', '?')} bucket={o.get('bucket', '?')} evidence={o.get('evidence_count', 0)}]: "
            f"{bounded_serenity_text(o.get('thesis_display', o.get('thesis', '')), 1300)}"
            + (
                "\n      key evidence: "
                + bounded_serenity_text(
                    o.get("key_evidence_display", o.get("key_evidence", "")), 2100
                )
                if o.get("key_evidence_display") or o.get("key_evidence")
                else ""
            )
        )
    serenity_lines = "\n".join(_serenity_lines) or ""

    # ── Data Health Alerts ──
    data_health = layer1.get("data_health", {})
    health_alerts: list[str] = []
    # Only inject the conservative-bias alert below MEANINGFUL coverage (<50%).
    # Firing it at 13/15 (87%) put an "operate conservatively" instruction in
    # every prompt regardless of signal quality — a third systematic skip-bias
    # stacked on the win_rate/regime misreads (2026-06-23 audit).
    _ready = data_health.get("members_with_full_coverage", 0)
    _total = data_health.get("total_members", 0) or 1
    if _ready < _total * 0.5:
        health_alerts.append(
            "ℹ️ Historical data sparse "
            f"({_ready}/{_total} members ready, <50%). "
            "Backtest/Optuna coverage thin — weight live signals over backtested ones."
        )
    # ── Assemble prompt ──
    parts = []
    if health_alerts:
        parts.append("# ⚠️ System Health Alerts")
        parts.extend(health_alerts)
        parts.append("")

    parts += [
        "# Layer 1: Environment Snapshot (current state)",
        "",
        "## Portfolio",
        position_count_line,
        pos_lines,
        "",
        "## Account",
        balance_line,
        pnl_line,
        "",
        "## Market Regime",
        regime_line,
        "",
        "## Technical Features (top universe assets)",
        feature_lines,
        "",
        _SIGNAL_RULES,
        "",
    ]

    # Opportunities from market_observer
    opps = layer1.get("opportunities", [])
    if opps:
        opp_lines = "\n".join(
            f"  {o['symbol']} [{o.get('market', '?')}] {o.get('direction_bias', '?')} "
            f"conf={o.get('confidence_score', '?')} "
            f"[{o.get('scanner_family', '')}]: {o.get('thesis', '')}"
            f"{(' | obs: ' + '; '.join(o['key_observations'])) if o.get('key_observations') else ''}"
            f"{(' | technical: ' + _fmt_feature_line(o['technical_context']).strip()) if o.get('technical_context') else ' | technical: unavailable'}"
            f"{_fmt_position_x_context(o.get('structure_context'))}"
            for o in opps[:8]
        )
        parts += ["## Open Opportunities (market_observer output)", opp_lines, ""]
        skew_note = _direction_skew_note(opps[:8], "open opportunities")
        if skew_note:
            parts += [skew_note, ""]

    pending_evidence = layer2.get("pending_signals", [])
    if pending_evidence:
        from aletheia.analytics.pi_evidence import render_b2_evidence_summary
        evidence_lines = "\n".join(
            f"  {p.get('symbol', '?')} {p.get('direction', '?')}: "
            f"{render_b2_evidence_summary(p.get('b2_evidence'))}"
            for p in pending_evidence[:8]
        )
        parts += ["## B2 Candidate-Conditioned Evidence (shared across assessment and debate)", evidence_lines, ""]

    # Factor feedback — blocked/preferred categories
    factors = layer1.get("factor_feedback", [])
    if factors:
        factor_lines = "\n".join(
            f"  {f['factor_key']} ({f['group_type']}): {f['status']} "
            f"WR={f.get('win_rate', '?')}% exp={f.get('expectancy', '?')} "
            f"n={f.get('trade_count', 0)}"
            for f in factors
        )
        parts += ["## Factor Feedback (historical pattern performance)", factor_lines, ""]

    parts += [
        f"## Risk: kill_switch={risk.get('kill_switch', False)}, "
        f"max_daily_loss={risk.get('max_daily_loss_pct', '?')}%, "
        f"max_positions={risk.get('max_open_positions', '?')}",
        "",
    ]

    # Futures limits — leverage ceilings + account leverage headroom so the PI's
    # market_type/leverage choice is bounded by reality, not guessed.
    futures = layer1.get("futures") or {}
    if futures:
        f_parts = ["## Futures Limits (leverage ceilings + account headroom)"]
        lim = futures.get("leverage_limits") or {}
        if lim:
            lim_lines = ", ".join(
                f"{s}({v['tier']}≤{v['max_demo']}x)" for s, v in list(lim.items())[:8]
            )
            f_parts.append(f"  Per-symbol max leverage (demo): {lim_lines}")
        alr = futures.get("account_leverage_ratio")
        cap = futures.get("account_leverage_cap")
        if alr is not None or cap is not None:
            f_parts.append(
                f"  Account leverage: {alr if alr is not None else '?'}x / cap {cap if cap is not None else '?'}x"
            )
        amp = futures.get("available_margin_pct")
        if amp is not None:
            f_parts.append(f"  Available margin: {amp}% of equity")
        near = futures.get("positions_near_liquidation") or []
        if near:
            near_str = ", ".join(f"{n['symbol']} (liq {n['gap_pct']}% away)" for n in near)
            f_parts.append(f"  ⚠ NEAR LIQUIDATION: {near_str}")
        parts += f_parts + [""]

    parts += [
        "---",
        "# Layer 2: Short-term Memory (past 7 days)",
        "",
    ]
    if reflection and not _bypass:
        parts.append("## Last Decision Reflection")
        parts.append(f"  {reflection}")
        parts.append("")
    parts += [
        "## Recent Performance",
        stats_block,
    ]
    if sym_lines:
        parts.append(sym_lines)
    parts += [
        "",
        "## Strategy Performance",
        strat_lines,
        "",
    ]
    if trade_analytics_lines:
        parts += [
            "## Trades Analytics (closed positions)",
            trade_analytics_lines,
            "",
        ]

    # A10 (LEARNING_LOOP_AUDIT / PIPELINE_GAPS D1): these three analytics signals
    # were COMPUTED in layer2 (trading_context._build_layer2_short_term) but never
    # rendered into the prompt — so calibration, the trading memo, and reasoning
    # feedback were silently dropped before reaching the System Operator. They are real,
    # data-derived signals; surface them.
    if not _bypass:
        memo = layer2.get("trading_memo")
        if memo and memo.get("rendered"):
            parts += ["## Trading Memo (insights from historical performance analysis)",
                      memo["rendered"], ""]
        calibration_fb = layer2.get("calibration_feedback")
        if calibration_fb:
            parts += ["## Conviction Calibration (your past conviction vs actual win-rate)",
                      calibration_fb, ""]
        reasoning_fb = layer2.get("reasoning_feedback")
        if reasoning_fb:
            parts += ["## Reasoning Feedback (which signal patterns have actually worked)",
                      reasoning_fb, ""]

    # Executable candidates are the ranker/pending exact-identity intersection.
    ranker = (
        layer2.get("executable_candidates")
        if "executable_candidates" in layer2
        else layer2.get("ranker_candidates", [])
    )
    if ranker:
        ranker_lines = "\n".join(
            f"  {c['symbol']} {c.get('direction', '?')} "
            f"rank_priority={c.get('trade_score', '?')}"
            for c in ranker[:6]
        )
        parts += [
            "## Executable Candidates (ranked picks backed by durable pending carriers)",
            "rank_priority is an ordering value among already-eligible candidates; it is "
            "not a probability, conviction, or hidden execution threshold.",
            ranker_lines,
            "",
        ]
        skew_note = _direction_skew_note(ranker[:6], "executable candidates")
        if skew_note:
            parts += [skew_note, ""]
    analysis_only = layer2.get("analysis_only_candidates") or []
    if analysis_only:
        preview_lines = "\n".join(
            f"  {c['symbol']} {c.get('direction', '?')} score={c.get('trade_score', '?')}"
            for c in analysis_only[:6]
        )
        parts += [
            "## Analysis-only Ranker Preview (not queued; never select for execution)",
            preview_lines,
            "",
        ]
    excluded_summary = layer2.get("excluded_candidates_summary") or []
    if excluded_summary:
        excluded_lines = []
        for row in excluded_summary[:6]:
            examples = ", ".join(
                f"{e.get('symbol')} {e.get('direction')}"
                for e in (row.get("examples") or [])[:3]
                if e.get("symbol")
            )
            suffix = f" examples={examples}" if examples else ""
            excluded_lines.append(f"  {row.get('reason')}: {row.get('count', 0)}{suffix}")
        parts += ["## Excluded Candidate Summary (not executable now)", "\n".join(excluded_lines), ""]

    # Phase 4b: Pending signals awaiting your decision
    pending = layer2.get("pending_signals", [])
    if pending:
        pending_lines = "\n".join(
            f"  {p['symbol']} {p.get('direction', '?')} "
            f"[{p.get('market_type') or 'unknown market'}]: {p.get('reasoning', '')}"
            for p in pending
        )
        queue = layer2.get("pending_queue") or {}
        queue_line = (
            f"Queue: {queue.get('unresolved_total', '?')} unresolved, "
            f"{queue.get('overflow_count', '?')} beyond this visible batch; "
            "oldest visible signal is listed first."
        )
        entry_mark = layer2.get("entry_mark") or {}
        entry_mark_line = (
            "Authoritative entry mark for planning: "
            f"{entry_mark.get('symbol')} [{entry_mark.get('market_type')}] "
            f"price={entry_mark.get('price')} venue={entry_mark.get('venue')} "
            f"authority={entry_mark.get('authority')}"
            if entry_mark.get("status") == "available"
            else "Authoritative entry mark for planning: unavailable"
        )
        parts += [
            "## ⚡ Pending Signals (awaiting your decision)",
            "These signals were collected by the scanner pipeline. You are the sole decision maker —",
            "make exactly one trade or skip decision for the single selected durable carrier.",
            queue_line,
            entry_mark_line,
            pending_lines, "",
        ]
        skew_note = _direction_skew_note(pending, "pending signals")
        if skew_note:
            parts += [skew_note, ""]

    parts += [
        "---",
        "# Layer 3: Long-term Wisdom",
        "",
        "## Lessons & Guards",
        lesson_lines,
    ]
    if guard_lines:
        parts.append("")
        parts.append("## Pending Guard Candidates (patterns that failed recently)")
        parts.append(guard_lines)
    if serenity_lines:
        parts.append("")
        parts.append("## Research-sourced Opportunities (from Serenity agent)")
        parts.append(
            "  These are LONG-HORIZON, fundamentals/supply-chain theses — a different "
            "school from the short-term price-action signals above, NOT same-day setups. "
            "`tier` is an automatic evidence floor (a deterministic evaluator's grade, "
            "not a conviction), `score`/`bucket` are computed, not human-vetted. Treat "
            "each as analyst input to weigh independently — you are the sole decision "
            "maker; do not rubber-stamp."
        )
        parts.append(serenity_lines)

    # L-Bug-B: dreaming-agent post-trade lessons (advisory reflections, not risk
    # blocks). These ps_lessons previously had no path into the PI prompt at all.
    ps_lessons = layer3.get("ps_lessons", [])
    if ps_lessons:
        parts.append("")
        parts.append("## Recent Post-trade Lessons (dreaming agent reflections)")
        for l in ps_lessons[:5]:
            parts.append(
                f"  [{l.get('severity') or 'info'}] {l.get('category') or 'general'}: "
                f"{l.get('summary', '')}"
            )

    parts += [
        "",
        "---",
        "# Your Reasoning Space",
        "",
        "Reason freely about whether to act, but execution authority is not free-form:",
        "- For trade, choose only an exact symbol, direction, and market_type "
        "shown in Pending Signals / Executable Candidates.",
        "- Analysis-only Ranker Preview rows are not queued and must never be selected "
        "for execution. If no durable pending signal is suitable, skip.",
        "- A rank_priority value only orders candidates that already passed upstream "
        "eligibility. Never reject a candidate merely because that value looks low.",
        "- Insufficient matured outcomes or missing historical evidence is neutral. It "
        "cannot by itself justify a terminal skip.",
        "- Missing optional telemetry (for example OI delta) is unknown, not zero and not "
        "adverse evidence. Oversold RSI is opposing/rebound risk for a short, not short confirmation.",
        "- Account equity affects downstream sizing, not candidate quality. A tiny recent "
        "performance sample (especially one trade) cannot authorize a candidate skip.",
        "- Conviction bypass rule: never lower conviction using account equity, "
        "tiny-sample recent trades (especially one loss), a neutral 50/50 or SIDEWAYS "
        "regime tag, missing optional telemetry, or Ranker rank ordering. Conviction "
        "must be derived from candidate-specific evidence only — technical structure, "
        "funding/OI signals, catalyst context. Those factors belong in bear_case or "
        "risk_factors as caveats, not in the number that decides the trade.",
        "- For skip, set skip_kind. A terminal adverse skip must use "
        "adverse_candidate_evidence and include at least one allowed adverse_reason_code "
        "plus concrete candidate-specific adverse_evidence. Use insufficient_evidence "
        "when history is merely unavailable; the caller will defer rather than consume "
        "the candidate.",
        "  Each adverse_reason_code has a HARD numeric gate — use it only if the data "
        "shown in this context actually satisfies the exact predicate:\n"
        "  adverse_price_structure → return_4h ≤ -1.0% AND return_24h ≤ -2.0% (long) "
        "or return_4h ≥ +1.0% AND return_24h ≥ +2.0% (short)\n"
        "  signal_conflict → return_1h ≤ -0.5% AND return_4h ≤ -1.0% (long) "
        "or return_1h ≥ +0.5% AND return_4h ≥ +1.0% (short)\n"
        "  regime_mismatch → regime.state is bearish/bear/downtrend for a long entry "
        "(or bullish/bull/uptrend for a short) AND regime.confidence ≥ 0.55\n"
        "  negative_same_cohort_outcomes → b2.status=qualified_negative AND "
        "b2.directional_verdict=negative AND b2.shrunk_avg_net_pct < 0\n"
        "  liquidity_or_execution_quality → candidate.execution_eligible=False OR "
        "candidate.risk_flags is non-empty\n"
        "  invalid_risk_reward → reward/risk < 1.0 given your stop_price and "
        "take_profit vs entry price\n"
        "  A claim that does not satisfy its predicate is rejected by the validation gate "
        "and the candidate is deferred, not consumed. Missing optional fields such as "
        "oi_delta_1h or funding_rate are unknown, not adverse — use "
        "skip_kind=insufficient_evidence for data quality concerns.",
        "- What is the dominant market narrative right now? Is it already priced in?",
        "- Am I seeing confluence (multiple signals aligning) or conflict?",
        "- Is there an asymmetric risk/reward setup I'm missing?",
        "- Do the lessons or guard candidates warn against this specific setup?",
        "- What would make me change my mind about this trade?",
        "",
        "The guard chain handles risk limits independently — focus purely on:",
        "SHOULD this trade happen, in WHAT direction, with WHAT conviction?",
        "",
        "## Market type & leverage",
        "- market_type: for a new entry, copy the exact market_type from its durable "
        "Pending Signal; never relabel spot as futures or futures as spot downstream. "
        "A short requires an existing usdt_futures carrier (spot cannot be sold short).",
        "- leverage: integer ≥1, only for usdt_futures. Scale with conviction — 1 (none) when "
        "uncertain, 2-3 for solid setups, higher only for the strongest confluence. The risk "
        "engine clamps to a per-asset-tier cap (majors allow more than alts), so an over-eager "
        "number is reduced, not rejected. Leave at 1 if unsure.",
        "",
        "Actions available:",
        "- trade: open a new position",
        "- skip: no action now",
        "",
        "Return: action, symbol (BTC/USDT format), direction (long/short), "
        "conviction (0-100), market_type (spot/usdt_futures), leverage (integer ≥1), "
        "stop_price, take_profit, "
        "reasoning, bear_case (required: strongest concrete reason this decision can fail), "
        "risk_factors, supporting_signals, opposing_signals, "
        "skip_kind, adverse_reason_codes, adverse_evidence, "
        "key_assumptions (array of ≤3 short strings: critical premises this decision rests on), "
        "observed_facts (fact plus evidence_refs copied from the supplied context), "
        "interpretations, invalidation_conditions, and uncertainty_reason. "
        "Use empty arrays or unavailable when facts or refs are absent; never invent them.",
        "",
        "For every trade action, stop_price and take_profit are mandatory finite positive "
        "absolute prices authored from the exact candidate facts above. For a long use "
        "stop_price < current candidate price < take_profit; for a short use "
        "take_profit < current candidate price < stop_price. Never return 0/null/sentinel "
        "values. The rounded reward:risk ratio "
        "round(abs(take_profit-entry)/abs(entry-stop_price), 2) must stay within "
        f"{ENTRY_PLAN_MIN_RISK_REWARD:.2f}-{ENTRY_PLAN_MAX_RISK_REWARD:.2f} inclusive. "
        "Never invent levels when the shown facts do not support them; choose "
        "skip with insufficient_evidence instead. Also return parameter_rationale with five "
        "short, setup-specific reasons: entry_price, stop_loss, take_profit, notional_usdt, "
        "and leverage. Explain why each exact choice fits this trade now. These are shown to "
        "the operator next to the final values. Entry remains the fresh exact submission mark, "
        "and notional/leverage remain subject to deterministic sizing and risk caps; do not "
        "claim that you calculated values owned by those downstream authorities.",
    ]

    return "\n".join(parts)


def _fmt_trade_analytics(trade_analytics: dict | None) -> str:
    if not trade_analytics:
        return ""

    lines: list[str] = []
    totals = trade_analytics.get("totals") or {}
    total_trades = int(totals.get("trade_count") or 0)
    if total_trades:
        lines.append(
            "  total: "
            f"{total_trades} trades, WR={totals.get('win_rate', '?')}%, "
            f"PnL=${float(totals.get('total_pnl') or 0):+.2f}, "
            f"avg_hold={totals.get('avg_hold_hours', '?')}h, "
            f"median_hold={totals.get('median_hold_hours', '?')}h"
        )

    for src in (trade_analytics.get("by_source") or [])[:3]:
        if not src.get("trade_count"):
            continue
        lines.append(
            f"  source {src.get('source_type', 'unknown')}: "
            f"{src.get('trade_count', 0)}T WR={src.get('win_rate', '?')}% "
            f"PnL=${float(src.get('total_pnl') or 0):+.2f} "
            f"expectancy={src.get('expectancy_pct', '?')}%"
        )

    for fam in (trade_analytics.get("by_family") or [])[:4]:
        if not fam.get("trade_count"):
            continue
        family = fam.get("family") or fam.get("scanner_family") or "unknown"
        lines.append(
            f"  family {family}: "
            f"{fam.get('trade_count', 0)}T WR={fam.get('win_rate', '?')}% "
            f"expectancy={fam.get('expectancy_pct', '?')}% "
            f"alpha_vs_btc={fam.get('alpha_vs_btc_pct', '?')}"
        )

    hold = [
        h for h in (trade_analytics.get("hold_distribution") or [])
        if h.get("count", 0)
    ][:4]
    if hold:
        lines.append(
            "  hold "
            + ", ".join(
                f"{h.get('bucket', '?')}={h.get('count', 0)} ({h.get('pct', 0)}%)"
                for h in hold
            )
        )

    return "\n".join(lines)


def _fmt_funding(rate: float | None) -> str:
    if rate is None:
        return "?"
    return f"{rate * 100:+.4f}%"


def _feature_for_candidate(symbol: str, direction: str | None, ctx: dict) -> dict:
    """Resolve a feature snapshot by full instrument identity when available."""
    layer1 = ctx.get("layer1") or {}
    feature_index = layer1.get("feature_index") or {}
    candidates = [
        row for row in feature_index.values() if row.get("symbol") == symbol
    ]
    pending = [
        row for row in (ctx.get("layer2") or {}).get("pending_signals", [])
        if row.get("symbol") == symbol
        and (not direction or row.get("direction") == direction)
    ]
    market = pending[0].get("market_type") if len(pending) == 1 else None
    if market:
        normalized = "perp" if str(market).lower() in {
            "perp", "futures", "swap", "usdt_futures"
        } else "spot"
        exact = [row for row in candidates if row.get("market_type") == normalized]
        if len(exact) == 1:
            return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous identity is unavailable, never guessed from a same-symbol row.
    return {}


def _build_bull_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    snap = _feature_for_candidate(symbol, direction, ctx)
    regime = ctx.get("layer1", {}).get("regime")
    regime_str = f"Regime: {regime['state']} (conf={regime['confidence']:.0%})" if regime else ""

    oi_div = snap.get("oi_price_divergence")
    deriv_extras = f" OI_Δ={snap.get('oi_delta_1h', '?')} taker={snap.get('taker_buy_ratio', '?')} RS24h={snap.get('relative_strength_24h', '?')}%"
    div_note = f" [{_OI_DIV_LABEL[oi_div]}]" if oi_div else ""
    _dir = direction or "long"
    if _dir == "short":
        note = "Note: FR>+0.01% = crowded longs → supports short; taker<0.8 = seller aggression → supports short; OI_div=weak_rally = distribution → supports short."
    else:
        note = "Note: FR<-0.01% = crowded shorts → bullish; taker>1.2 = buyer aggression; OI_div=hidden_acc = potential reversal long."
    evidence = _render_b2_evidence_for(symbol, _dir, ctx)
    return f"""You are a bull analyst. Make the STRONGEST case for {symbol} {_dir} RIGHT NOW.

Data: RSI={snap.get('rsi_14', '?')} ATR={snap.get('atr_pct', '?')}% 1h={snap.get('return_1h', '?')}% 4h={snap.get('return_4h', '?')}% 24h={snap.get('return_24h', '?')}% FR={_fmt_funding(snap.get('funding_rate'))}{deriv_extras}{div_note}
{regime_str}
{note}
{evidence}

Be specific: technical setup, regime alignment, risk/reward.
Return confidence (0-100) where 100 = near-certain win. Provide 3-5 evidence points."""


def _build_bear_prompt(symbol: str, direction: str | None, ctx: dict) -> str:
    snap = _feature_for_candidate(symbol, direction, ctx)
    regime = ctx.get("layer1", {}).get("regime")
    regime_str = f"Regime: {regime['state']} (conf={regime['confidence']:.0%})" if regime else ""

    oi_div = snap.get("oi_price_divergence")
    deriv_extras = f" OI_Δ={snap.get('oi_delta_1h', '?')} taker={snap.get('taker_buy_ratio', '?')} RS24h={snap.get('relative_strength_24h', '?')}%"
    div_note = f" [{_OI_DIV_LABEL[oi_div]}]" if oi_div else ""
    _dir = direction or "long"
    if _dir == "short":
        note = "Note: FR<-0.01% = crowded shorts → fade risk (short squeeze); taker>1.2 = buyer aggression → challenges short; OI_div=hidden_acc = potential reversal → challenges short."
    else:
        note = "Note: FR>+0.01% = crowded longs → bearish; taker<0.8 = seller aggression; OI_div=weak_rally = distribution risk."
    evidence = _render_b2_evidence_for(symbol, _dir, ctx)
    return f"""You are a bear analyst. Find every reason why {symbol} {_dir} RIGHT NOW is a BAD idea.

Data: RSI={snap.get('rsi_14', '?')} ATR={snap.get('atr_pct', '?')}% 1h={snap.get('return_1h', '?')}% 4h={snap.get('return_4h', '?')}% 24h={snap.get('return_24h', '?')}% FR={_fmt_funding(snap.get('funding_rate'))}{deriv_extras}{div_note}
{regime_str}
{note}
{evidence}

Challenge aggressively: overbought/oversold, adverse regime, funding cost, better entry timing.
Return confidence (0-100) where 100 = trade will definitely fail. Provide 3-5 counter-evidence points."""


# ── helpers ────────────────────────────────────────────────────────────────────

def _b2_evidence_for(symbol: str | None, direction: str | None, ctx: dict) -> dict | None:
    for candidate in (ctx.get("layer2") or {}).get("pending_signals") or []:
        if candidate.get("symbol") != symbol or candidate.get("direction") != direction:
            continue
        evidence = candidate.get("b2_evidence")
        if isinstance(evidence, dict):
            return evidence
    return None


def _b2_evidence_for_decision(decision: TradingDecision, ctx: dict) -> dict | None:
    return _b2_evidence_for(decision.symbol, decision.direction, ctx)


def _render_b2_evidence_for(symbol: str, direction: str, ctx: dict) -> str:
    from aletheia.analytics.pi_evidence import render_b2_evidence_summary
    return render_b2_evidence_summary(_b2_evidence_for(symbol, direction, ctx))

def _parse_decision(raw: dict) -> TradingDecision:
    mt = raw.get("market_type")
    if mt not in ("spot", "usdt_futures"):
        mt = None
    lev_raw = raw.get("leverage")
    try:
        leverage = int(lev_raw) if lev_raw is not None else None
    except (TypeError, ValueError):
        leverage = None
    if leverage is not None and leverage < 1:
        leverage = 1
    raw_assumptions = raw.get("key_assumptions")
    key_assumptions = raw_assumptions if isinstance(raw_assumptions, list) else []

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _conviction(v):
        # Invalid model output is evaluation unavailability. Normalizing it to
        # zero used to manufacture a terminal skip and consume a real candidate.
        if isinstance(v, bool) or v is None:
            raise DecisionUnavailable(
                "invalid_conviction", stage="operator_output_validation"
            )
        try:
            parsed = int(v)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DecisionUnavailable(
                "invalid_conviction", stage="operator_output_validation"
            ) from exc
        if isinstance(v, float) and not v.is_integer():
            raise DecisionUnavailable(
                "invalid_conviction", stage="operator_output_validation"
            )
        if not 0 <= parsed <= 100:
            raise DecisionUnavailable(
                "invalid_conviction", stage="operator_output_validation"
            )
        return parsed

    raw_parameter_rationale = raw.get("parameter_rationale")
    parameter_rationale: dict[str, str] = {}
    if isinstance(raw_parameter_rationale, dict):
        for key in _ENTRY_PARAMETER_KEYS:
            value = raw_parameter_rationale.get(key)
            if isinstance(value, str) and value.strip():
                parameter_rationale[key] = value.strip()[:600]

    if not raw.get("action"):
        raise DecisionUnavailable(
            "missing_action", stage="operator_output_validation"
        )
    raw_action = str(raw.get("action")).strip().lower()
    if raw_action not in {"trade", "skip"}:
        raise DecisionUnavailable(
            f"unsupported_action:{raw_action}", stage="operator_output_validation"
        )
    symbol = raw.get("symbol")
    direction = raw.get("direction")
    stop_price = _valid_protective_price(raw.get("stop_price"))
    take_profit = _valid_protective_price(raw.get("take_profit"))
    if raw_action == "trade":
        incomplete_plan: list[str] = []
        if not isinstance(symbol, str) or not symbol.strip():
            incomplete_plan.append("symbol")
        if direction not in {"long", "short"}:
            incomplete_plan.append("direction")
        if mt is None:
            incomplete_plan.append("market_type")
        if stop_price is None:
            incomplete_plan.append("stop_price")
        if take_profit is None:
            incomplete_plan.append("take_profit")
        incomplete_plan.extend(
            f"parameter_rationale.{key}"
            for key in _ENTRY_PARAMETER_KEYS
            if key not in parameter_rationale
        )
        if incomplete_plan:
            raise DecisionUnavailable(
                "incomplete_operator_entry_plan:" + "+".join(incomplete_plan),
                stage="operator_output_validation",
            )
        symbol = symbol.strip()
    raw_codes = raw.get("adverse_reason_codes")
    adverse_reason_codes = raw_codes if isinstance(raw_codes, list) else []
    raw_adverse_evidence = raw.get("adverse_evidence")
    adverse_evidence = (
        raw_adverse_evidence if isinstance(raw_adverse_evidence, list) else []
    )
    decision = TradingDecision(
        action=raw_action,
        symbol=symbol,
        direction=direction,
        conviction=_conviction(raw.get("conviction")),
        reasoning=raw.get("reasoning", ""),
        bear_case=str(raw.get("bear_case") or "")[:500],
        market_type=mt,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        expected_horizon_hours=_num(raw.get("expected_horizon_hours")),
        order_type=raw.get("order_type"),
        limit_price=_num(raw.get("limit_price")),
        risk_factors=raw.get("risk_factors", []),
        supporting_signals=raw.get("supporting_signals", []),
        opposing_signals=raw.get("opposing_signals", []),
        skip_kind=raw.get("skip_kind"),
        adverse_reason_codes=adverse_reason_codes,
        adverse_evidence=adverse_evidence,
        key_assumptions=key_assumptions,
        observed_facts=raw.get("observed_facts", []),
        interpretations=raw.get("interpretations", []),
        invalidation_conditions=raw.get("invalidation_conditions", []),
        uncertainty_reason=str(raw.get("uncertainty_reason") or "")[:600],
        parameter_rationale=parameter_rationale,
        parameter_rationale_required=True,
    )
    return decision


def _resolve_pending_carrier_identity(
    carrier: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return one exact pending identity, recovering only legacy Serenity market type.

    Current ``signal_pending`` producers persist all four authority fields.  Historical
    Serenity rows predate ``pi_decisions.market_type`` and therefore carry NULL in that
    one column.  They remain usable only when their durable SerenityOpportunity proves
    the same id, symbol, and direction and supplies a non-empty market type.  No other
    partial carrier is treated as a wildcard.
    """
    if not isinstance(carrier, dict):
        return None
    resolved = dict(carrier)
    opportunity_id = resolved.get("opportunity_id")
    symbol = resolved.get("symbol")
    direction = resolved.get("direction")
    market_type = resolved.get("market_type")
    signal_source = resolved.get("signal_source") or "market_observer"
    if not opportunity_id or not symbol or not direction:
        return None
    if signal_source not in {"market_observer", "serenity"}:
        return None
    if signal_source == "market_observer":
        if not market_type:
            return None
        resolved["signal_source"] = signal_source
        return resolved

    try:
        from aletheia.storage.repositories import get_serenity_opportunity

        source = get_serenity_opportunity(opportunity_id)
    except Exception:
        return None
    if (
        not source
        or source.get("id") != opportunity_id
        or source.get("symbol") != symbol
        or source.get("direction") != direction
        or not source.get("market_type")
        or (market_type and source.get("market_type") != market_type)
    ):
        return None
    resolved["market_type"] = market_type or source["market_type"]
    resolved["signal_source"] = "serenity"
    return resolved


def _attach_opportunity_context(decision: TradingDecision, ctx: dict) -> None:
    """Backfill opportunity_id + signal_source from the source opportunity (R0 + Phase 2).

    The System Operator freely picks a symbol+direction from the whole context; this
    matches that pick back to the opportunity it came from so the decision
    records a real opportunity_id (not the hard-coded None that severed the
    attribution chain). Sources are matched in priority order:

      1. market_observer `pending_signals` — the exact queue carrier emitted by
         paper_trader. It wins over a different same-symbol open opportunity.
      2. serenity `pending_signals` for Opportunity decisions. Layer 1/ranker rows
         are analysis-only and cannot authorize either entry or terminal identity.
      3. Legacy analysis-only helper contexts that omit the pending seam may still
         use Layer 1/ranker/Serenity attribution. Serenity sets
         signal_source='serenity' so opportunity_id is later
         resolved against serenity_opportunities (NOT opportunities) — the two id
         spaces are indistinguishable bare uuid4().hex[:12] strings.

    Best-effort and honest: if the pick maps to neither (e.g. it came from raw
    features/signals), the fields stay None rather than being fabricated. See
    LEARNING_LOOP_AUDIT_2026-06-26 §R0 +
    docs/archive/legacy/plans/trading-pipeline/serenity_execution_path.md §3.2.
    """
    # M1: non-entry actions short-circuit — no opportunity context to attach
    if decision.action in {"close", "reduce", "adjust_position"}:
        return

    # The exact queued carrier is more specific than a later same-symbol/direction
    # open row, so consume it before heuristic Layer-1 matches.
    layer2 = ctx.get("layer2") or {}
    raw_pending = layer2.get("pending_signals") or []
    pending = [
        resolved
        for carrier in raw_pending
        if (resolved := _resolve_pending_carrier_identity(carrier)) is not None
    ]
    if decision.action == "trade":
        compatible_by_identity: dict[tuple, dict] = {}
        for carrier in pending:
            if decision.opportunity_id and carrier.get("opportunity_id") != decision.opportunity_id:
                continue
            if decision.symbol and carrier.get("symbol") != decision.symbol:
                continue
            if decision.direction and carrier.get("direction") != decision.direction:
                continue
            if decision.market_type and carrier.get("market_type") != decision.market_type:
                continue
            identity = (
                carrier.get("signal_source") or "market_observer",
                carrier.get("opportunity_id"),
                carrier.get("symbol"),
                carrier.get("direction"),
                carrier.get("market_type"),
            )
            compatible_by_identity[identity] = carrier
        compatible = list(compatible_by_identity.values())
        if len(compatible) > 1:
            decision.filter_trace.append({
                "filter_name": "pending_signal_identity",
                "stage": "operator_context_binding",
                "decision": "rejected",
                "reason_code": "pending_identity_ambiguous",
                "inputs": {
                    "symbol": decision.symbol,
                    "direction": decision.direction,
                    "market_type": decision.market_type,
                    "compatible_carriers": len(compatible),
                },
                "parameters": {
                    "required_identity": [
                        "opportunity_id", "signal_source", "symbol",
                        "direction", "market_type",
                    ],
                },
            })
            return
        if not compatible:
            return
        # Every later branch now sees the same unique authority row. Missing
        # model fields may be inferred only from this one durable identity.
        pending = compatible
    if not decision.symbol:
        carrier = pending[0] if pending else None
        if carrier is None:
            return
        decision.symbol = carrier.get("symbol")
        decision.direction = carrier.get("direction")
        decision.market_type = carrier.get("market_type") or decision.market_type
        decision.opportunity_id = carrier.get("opportunity_id")
        decision.signal_source = carrier.get("signal_source") or "market_observer"
        try:
            if decision.signal_source == "serenity":
                from aletheia.storage.repositories import get_serenity_opportunity

                source = get_serenity_opportunity(decision.opportunity_id)
                if source and not decision.market_type:
                    decision.market_type = source.get("market_type")
            else:
                from aletheia.storage.repositories import get_opportunity

                source = get_opportunity(decision.opportunity_id)
                if source:
                    decision.strategy_id = source.get("linked_strategy_id")
                    decision.scanner_family = source.get("scanner_family")
                    if not decision.market_type:
                        decision.market_type = source.get("market")
                    try:
                        raw_meta = source.get("metadata") or "{}"
                        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
                    except (TypeError, ValueError):
                        meta = {}
                    decision.learning_candidate_key = meta.get("learning_candidate_key")
        except Exception:
            pass
        return

    for p in pending:
        if (p.get("signal_source") or "market_observer") != "market_observer":
            continue
        if p.get("symbol") != decision.symbol:
            continue
        pdir = p.get("direction")
        pmarket = p.get("market_type")
        if not pdir or not pmarket:
            continue
        if decision.direction and pdir != decision.direction:
            continue
        if decision.market_type and pmarket != decision.market_type:
            continue
        oid = p.get("opportunity_id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.signal_source = "market_observer"
        decision.direction = decision.direction or pdir
        decision.market_type = decision.market_type or pmarket
        # Recover the source fields omitted from PiDecision. The id remains an
        # honest attribution anchor even if this best-effort lookup fails.
        try:
            from aletheia.storage.repositories import get_opportunity

            src_opp = get_opportunity(oid)
            if src_opp:
                try:
                    raw_meta = src_opp.get("metadata") or "{}"
                    meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
                except (TypeError, ValueError):
                    meta = {}
                decision.learning_candidate_key = meta.get("learning_candidate_key")
                decision.strategy_id = src_opp.get("linked_strategy_id")
                decision.scanner_family = src_opp.get("scanner_family")
                if not decision.market_type and src_opp.get("market"):
                    decision.market_type = src_opp.get("market")
        except Exception:
            pass
        return

    # Every decision about a fresh Opportunity may inherit identity only from the
    # durable pending queue. Layer-1/ranker rows remain analysis context: without
    # this return, a heartbeat skip could silently retire an open Opportunity
    # that paper_trader/Serenity never queued.
    if decision.action in {"trade", "skip"}:
        for p in pending:
            if p.get("signal_source") != "serenity":
                continue
            if p.get("symbol") != decision.symbol:
                continue
            pdir = p.get("direction")
            pmarket = p.get("market_type")
            if not pdir or not pmarket:
                continue
            if decision.direction and pdir != decision.direction:
                continue
            if decision.market_type and pmarket != decision.market_type:
                continue
            oid = p.get("opportunity_id")
            if not oid:
                continue
            decision.opportunity_id = oid
            decision.signal_source = "serenity"
            decision.direction = decision.direction or pdir
            decision.market_type = decision.market_type or pmarket
            if not decision.market_type:
                try:
                    from aletheia.storage.repositories import get_serenity_opportunity

                    source = get_serenity_opportunity(oid)
                    if source and source.get("market_type"):
                        decision.market_type = source["market_type"]
                except Exception:
                    pass
            return
        # Production Operator context always carries the pending queue seam, even
        # when its value is zero. Stop there so Layer-1/ranker previews cannot own
        # a heartbeat terminal. A legacy analysis-only helper context may omit the
        # seam entirely; it can retain best-effort in-memory attribution, while
        # claim_decision still refuses to persist it without a durable carrier.
        if "pending_signals" in layer2 or "pending_queue" in layer2:
            return

    opps = (ctx.get("layer1") or {}).get("opportunities") or []
    for o in opps:
        if o.get("symbol") != decision.symbol:
            continue
        bias = o.get("direction_bias")
        # Require direction agreement only when both sides declare one; an
        # opportunity with no bias still matches on symbol.
        if decision.direction and bias and bias != decision.direction:
            continue
        decision.opportunity_id = o.get("id")
        try:
            raw_meta = o.get("metadata") or "{}"
            meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
        except (TypeError, ValueError):
            meta = {}
        decision.learning_candidate_key = meta.get("learning_candidate_key")
        decision.strategy_id = o.get("linked_strategy_id")
        decision.scanner_family = o.get("scanner_family")
        decision.signal_source = "market_observer"
        # Inherit the instrument type from the source opportunity when the PI did
        # not explicitly choose one — so a spot opportunity trades as spot rather
        # than defaulting to perp (owner principle 3: no fixed instrument bias).
        if not decision.market_type and o.get("market"):
            decision.market_type = o.get("market")
        return

    # #152: even when a same-symbol Layer-1 opportunity was seen but its direction
    # conflicted (so the loop above did not attach), fall through to the ranker +
    # serenity fallbacks — a matching-direction ranker candidate (same market_observer
    # provenance) or serenity pick still carries the real opportunity_id, so the
    # attribution chain (DL-INV-4) is preserved instead of severed.

    # Ranker candidates are a second deterministic source of market_observer
    # opportunity ids. They may include candidates not currently visible in the
    # compact Layer-1 opportunity list, so use them before falling back to Serenity.
    ranker = (layer2.get("executable_candidates") or []) + (layer2.get("ranker_candidates") or [])
    for c in ranker:
        if c.get("symbol") != decision.symbol:
            continue
        cdir = c.get("direction") or c.get("direction_bias")
        if decision.direction and cdir and cdir != decision.direction:
            continue
        oid = c.get("opportunity_id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.learning_candidate_key = c.get("learning_candidate_key")
        decision.strategy_id = c.get("strategy_id")
        decision.scanner_family = c.get("scanner_family")
        decision.signal_source = c.get("signal_source") or "market_observer"
        if not decision.market_type and c.get("market_type"):
            decision.market_type = c.get("market_type")
        return

    # Serenity direct match (Phase 2 fix): pending_signals carry opportunity_id
    # from auto_promote. Match directly — avoids the layer3 limit=5 truncation
    # that would otherwise sever the attribution chain for promoted opps.
    for p in pending:
        if p.get("signal_source") != "serenity":
            continue
        if p.get("symbol") != decision.symbol:
            continue
        pdir = p.get("direction")
        pmarket = p.get("market_type")
        if not pdir or not pmarket:
            continue
        if decision.direction and pdir != decision.direction:
            continue
        if decision.market_type and pmarket != decision.market_type:
            continue
        oid = p.get("opportunity_id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.signal_source = "serenity"
        decision.direction = decision.direction or pdir
        decision.market_type = decision.market_type or pmarket
        # Legacy pending rows lack market_type. Recover it from the source
        # serenity_opportunity without weakening the attribution anchor.
        if not decision.market_type:
            try:
                from aletheia.storage.repositories import get_serenity_opportunity
                src_opp = get_serenity_opportunity(oid)
                if src_opp and src_opp.get("market_type"):
                    decision.market_type = src_opp["market_type"]
            except Exception:
                pass
        return

    # Serenity fallback (Phase 2): the pick matched no market_observer opportunity
    # and no pending_signal. Try the research long-hold picks surfaced in Layer 3.
    # Only anchor on an opportunity that actually carries an id (the attribution anchor).
    serenity_opps = (ctx.get("layer3") or {}).get("serenity_opportunities") or []
    for o in serenity_opps:
        # EQ-R: observation-only equity tokens must never become execution anchors.
        # "equity_tradeable" (Serenity research-produced) IS eligible — skip only "equity".
        if not o.get("execution_eligible", True):
            continue
        if o.get("asset_class") == "equity":
            continue
        if o.get("symbol") != decision.symbol:
            continue
        sdir = o.get("direction")
        if decision.direction and sdir and sdir != decision.direction:
            continue
        oid = o.get("id")
        if not oid:
            continue
        decision.opportunity_id = oid
        decision.signal_source = "serenity"
        # #21/#102: serenity_opportunities dicts store the instrument under
        # "market_type" (trading_context builds them that way), NOT "market" — the
        # old o.get("market") always returned None, so a serenity spot pick fell
        # through to the short->perp fallback. Read the correct key.
        if not decision.market_type and o.get("market_type"):
            decision.market_type = o.get("market_type")
        return
