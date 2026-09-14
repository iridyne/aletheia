"""Optional prospective Persona sizing evidence; no Core sizing authority."""

import logging

logger = logging.getLogger("aletheia.learning.sizing_evidence")


def resolve_sizing_evidence(
    *,
    baseline_notional: float,
    learned_notional: float,
    symbol: str,
    direction: str | None,
    regime_bucket: str | None,
    candidate_key: str | None,
    outcome_candidate_key: str | None,
    opportunity_id: str | None,
) -> dict:
    """Prospectively expose a learned size and return an auditable decision.

    Historical persona rewards only propose ``learned_notional``. The actual size
    stays at ``baseline_notional`` unless the common Learning experiment adapter
    authorizes this paper treatment. Missing causal keys therefore fail closed.
    """
    from aletheia.learning.experiments import apply_numeric_treatment

    baseline = float(baseline_notional)
    learned = float(learned_notional)
    if abs(learned - baseline) < 1e-9:
        from aletheia.learning.experiments import InfluenceDecision
        decision = InfluenceDecision(baseline, False, "baseline", "no_learned_delta")
    else:
        try:
            decision = apply_numeric_treatment(
                path_id="persona_sizing",
                scope={
                    "symbol": symbol,
                    "direction": direction,
                    "regime": regime_bucket,
                },
                action={"component": "base_notional", "method": "persona_reward_nudge"},
                source_cohort=f"persona_regime:{regime_bucket or 'unknown'}",
                target_horizon="4h",
                candidate_key=candidate_key if outcome_candidate_key else None,
                baseline=baseline,
                treatment=learned,
                consumer="paper_sizing_policy",
                consumer_stage="notional",
                outcome_candidate_key=outcome_candidate_key,
                opportunity_id=opportunity_id,
                manifest={
                    "baseline_notional": baseline,
                    "learned_notional": learned,
                    "regime_bucket": regime_bucket,
                },
            )
        except Exception as exc:
            logger.warning("Sizing Learning adapter failed closed: %s", exc)
            from aletheia.learning.experiments import InfluenceDecision
            decision = InfluenceDecision(baseline, False, "unavailable", "adapter_error")
    return {
        "baseline_notional": baseline,
        "learned_notional": learned,
        "actual_notional": float(decision.value),
        "authority_applied": bool(decision.applied),
        "assignment": decision.assignment,
        "reason": decision.reason,
        "hypothesis_id": decision.hypothesis_id,
        "experiment_id": decision.experiment_id,
        "exposure_id": decision.exposure_id,
    }
