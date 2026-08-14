"""Deterministic candidate facts for System Operator judgment validation.

The model may explain a decision, but it may not manufacture the facts that
make a direct skip terminal.  This module projects the one selected carrier
into a compact fact bundle and validates every claimed adverse reason against
that bundle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json


_CANDIDATE_TECHNICAL_MAX_AGE_SEC = 7200


def _market_type(value: Any) -> str | None:
    raw = str(value or "").lower()
    if raw in {"perp", "futures", "swap", "usdt_futures"}:
        return "perp"
    return "spot" if raw == "spot" else (raw or None)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _selected_candidate(ctx: dict[str, Any], opportunity_id: str | None = None) -> dict[str, Any]:
    pending = list((ctx.get("layer2") or {}).get("pending_signals") or [])
    if opportunity_id:
        for candidate in pending:
            if candidate.get("opportunity_id") == opportunity_id:
                return candidate
    return pending[0] if len(pending) == 1 else {}


def _selected_feature(ctx: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    symbol = candidate.get("symbol")
    market = _market_type(candidate.get("market_type") or candidate.get("market"))
    asset_id = candidate.get("asset_id")
    layer1 = ctx.get("layer1") or {}
    features = list((layer1.get("feature_index") or {}).values())
    features.extend(layer1.get("features") or [])
    if asset_id:
        exact = [feature for feature in features if feature.get("asset_id") == asset_id]
        if exact:
            return exact[0]
    for feature in features:
        if feature.get("symbol") != symbol:
            continue
        if _market_type(feature.get("market_type")) == market:
            return feature
    return {}


def candidate_context_unavailable_reason(
    ctx: dict[str, Any], *, opportunity_id: str | None = None,
) -> str | None:
    """Return a retryable reason when exact candidate technical facts are unusable."""
    candidate = _selected_candidate(ctx, opportunity_id)
    if (candidate.get("signal_source") or "market_observer") == "serenity":
        serenity_status = candidate.get("serenity_context_status")
        if serenity_status != "available":
            return (
                candidate.get("serenity_context_unavailable_reason")
                or "serenity_context_unavailable"
            )
    feature = _selected_feature(ctx, candidate)
    if not feature.get("feature_snapshot_id") or not feature.get("feature_ts"):
        return "candidate_technical_snapshot_unavailable"

    core_values = (
        feature.get("rsi_15m"), feature.get("rsi_14"), feature.get("rsi_4h"),
        feature.get("return_15m"), feature.get("return_1h"),
        feature.get("return_4h"), feature.get("return_24h"),
    )
    if not any(_number(value) is not None for value in core_values):
        return "candidate_technical_snapshot_incomplete"

    source_age = _number(feature.get("data_freshness_sec"))
    if source_age is None:
        return "candidate_technical_snapshot_incomplete"
    try:
        observed = datetime.fromisoformat(
            str(feature.get("feature_ts")).replace("Z", "+00:00")
        )
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        row_age = max(
            0.0,
            (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds(),
        )
    except (TypeError, ValueError):
        return "candidate_technical_snapshot_unavailable"
    effective_age = row_age + max(source_age, 0.0)
    if effective_age > _CANDIDATE_TECHNICAL_MAX_AGE_SEC:
        return "candidate_technical_snapshot_stale"
    return None


def candidate_fact_bundle(
    ctx: dict[str, Any], *, opportunity_id: str | None = None
) -> dict[str, Any]:
    """Return the compact, typed facts authorized for one candidate judgment."""
    candidate = _selected_candidate(ctx, opportunity_id)
    feature = _selected_feature(ctx, candidate)
    regime = (ctx.get("layer1") or {}).get("regime") or {}
    b2 = candidate.get("b2_evidence") or {}
    outcome = (ctx.get("layer2") or {}).get("outcome_stats") or {}
    balance = (ctx.get("layer1") or {}).get("balance") or {}
    return {
        "candidate": {
            "opportunity_id": candidate.get("opportunity_id"),
            "signal_source": candidate.get("signal_source") or "market_observer",
            "symbol": candidate.get("symbol"),
            "direction": candidate.get("direction") or candidate.get("direction_bias"),
            "market_type": _market_type(candidate.get("market_type") or candidate.get("market")),
            "scanner_family": candidate.get("scanner_family"),
            "rank_priority": _number(candidate.get("trade_score")),
            "rank_priority_semantics": "ordering_only",
            "execution_eligible": candidate.get("execution_eligible"),
            "risk_flags": list(candidate.get("risk_flags") or []),
        },
        "b2": {
            key: b2.get(key)
            for key in (
                "version", "policy_epoch_id", "horizon", "status", "n_total", "n_net",
                "outcome_coverage", "avg_net_pct", "shrunk_avg_net_pct",
                "directional_verdict", "cost_model_version",
            )
        },
        "regime": {
            key: regime.get(key)
            for key in ("state", "confidence", "long_bias", "short_bias")
        },
        "technical": {
            key: feature.get(key)
            for key in (
                "feature_snapshot_id", "feature_ts", "data_freshness_sec",
                "price", "rsi_15m", "rsi_14", "rsi_4h", "return_15m",
                "return_1h", "return_4h", "return_24h", "funding_rate",
                "oi_delta_1h", "relative_strength_24h", "vol_ratio",
                "taker_buy_ratio", "oi_price_divergence", "bb_squeeze",
                "crowding_signal",
            )
        },
        # These facts remain visible for audit, but cannot authorize a direct
        # adverse skip. Sizing and account protection belong downstream.
        "recent_performance": {
            "window_days": 7,
            "total_trades": outcome.get("total_trades"),
            "wins": outcome.get("wins"),
            "losses": outcome.get("losses"),
            "total_pnl": outcome.get("total_pnl"),
        },
        "account": {
            "equity_usdt": balance.get("equity_usdt"),
            "source": balance.get("source"),
            "adverse_skip_authority": False,
        },
    }


def validate_adverse_claims(decision: Any, ctx: dict[str, Any]) -> tuple[list[dict], str | None]:
    """Validate every model reason code against selected-candidate facts.

    Returns machine-generated claim references and, when any code is not
    supported, a retryable validation reason.  Partial validation is rejected:
    a terminal judgment may not retain even one fabricated adverse ground.
    """
    facts = candidate_fact_bundle(ctx, opportunity_id=getattr(decision, "opportunity_id", None))
    direction = str(facts["candidate"].get("direction") or "").lower()
    b2 = facts["b2"]
    regime = facts["regime"]
    technical = facts["technical"]
    claims: list[dict] = []

    for raw_code in getattr(decision, "adverse_reason_codes", None) or []:
        code = str(raw_code).strip()
        refs: list[str] | None = None
        if code == "negative_same_cohort_outcomes":
            if (
                b2.get("status") == "qualified_negative"
                and b2.get("directional_verdict") == "negative"
                and (_number(b2.get("shrunk_avg_net_pct")) or 0.0) < 0
            ):
                refs = [
                    "b2.status", "b2.n_net", "b2.outcome_coverage",
                    "b2.shrunk_avg_net_pct", "b2.directional_verdict",
                ]
        elif code == "regime_mismatch":
            state = str(regime.get("state") or "").lower()
            confidence = _number(regime.get("confidence"))
            long_bias = _number(regime.get("long_bias"))
            short_bias = _number(regime.get("short_bias"))
            hostile = (
                direction == "long"
                and (state in {"bear", "bearish", "downtrend"} or (short_bias or 0) >= 0.60)
            ) or (
                direction == "short"
                and (state in {"bull", "bullish", "uptrend"} or (long_bias or 0) >= 0.60)
            )
            if hostile and (confidence is None or confidence >= 0.55):
                bias_ref = "regime.long_bias" if direction == "long" else "regime.short_bias"
                refs = ["regime.state", "regime.confidence", bias_ref]
        elif code == "signal_conflict":
            r1 = _number(technical.get("return_1h"))
            r4 = _number(technical.get("return_4h"))
            opposed = (
                direction == "long" and r1 is not None and r4 is not None and r1 <= -0.5 and r4 <= -1.0
            ) or (
                direction == "short" and r1 is not None and r4 is not None and r1 >= 0.5 and r4 >= 1.0
            )
            if opposed:
                refs = ["technical.return_1h", "technical.return_4h"]
        elif code == "adverse_price_structure":
            r4 = _number(technical.get("return_4h"))
            r24 = _number(technical.get("return_24h"))
            opposed = (
                direction == "long" and r4 is not None and r24 is not None and r4 <= -1 and r24 <= -2
            ) or (
                direction == "short" and r4 is not None and r24 is not None and r4 >= 1 and r24 >= 2
            )
            if opposed:
                refs = ["technical.return_4h", "technical.return_24h"]
        elif code == "liquidity_or_execution_quality":
            if facts["candidate"].get("execution_eligible") is False or facts["candidate"].get("risk_flags"):
                refs = ["candidate.execution_eligible", "candidate.risk_flags"]
        elif code == "invalid_risk_reward":
            price = _number(technical.get("price"))
            stop = _number(getattr(decision, "stop_price", None))
            target = _number(getattr(decision, "take_profit", None))
            if price and stop and target:
                risk = price - stop if direction == "long" else stop - price
                reward = target - price if direction == "long" else price - target
                if risk > 0 and reward > 0 and reward / risk < 1.0:
                    refs = ["technical.price", "decision.stop_price", "decision.take_profit"]
        # candidate_data_integrity intentionally has no terminal support. Missing
        # optional OI/funding or stale required data is retryable, not adverse.

        if refs is None:
            return [], f"unverified_adverse_reason:{code}"
        claims.append({"reason_code": code, "fact_refs": refs})
    return claims, None


def render_validated_adverse_judgment(decision: Any, ctx: dict[str, Any]) -> tuple[str, list[str]]:
    """Render the authoritative terminal wording from validated facts only."""
    facts = candidate_fact_bundle(ctx, opportunity_id=getattr(decision, "opportunity_id", None))
    evidence: list[str] = []
    for claim in getattr(decision, "validated_adverse_claims", None) or []:
        values = []
        for ref in claim.get("fact_refs") or []:
            section, _, key = str(ref).partition(".")
            value = (
                getattr(decision, key, None)
                if section == "decision"
                else (facts.get(section) or {}).get(key)
            )
            values.append(f"{ref}={json.dumps(value, sort_keys=True, default=str)}")
        evidence.append(f"{claim.get('reason_code')}: " + ", ".join(values))
    reasoning = "Deterministic selected-candidate facts support terminal skip: " + "; ".join(evidence)
    return reasoning, evidence
