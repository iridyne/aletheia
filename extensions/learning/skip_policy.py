"""Versioned B1 policy attribution for System Operator skip remediation."""

from __future__ import annotations

import os

from extensions.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


# Operational kill switch (env). When set truthy, skip_policy_context() forces
# every symbol-level material-dedup consumer flag back OFF at read time, so the
# whole #321 feature set reverts to prior per-opportunity behavior WITHOUT a
# redeploy. The persisted policy epoch still records the configured values; this
# only overrides the live read. (#321)
_MATERIAL_DEDUP_KILL_ENV = "ALETHEIA_DISABLE_MATERIAL_DEDUP"
_MATERIAL_DEDUP_CONSUMER_FLAGS = (
    "emit_active_defer_suppression",
    "symbol_level_defer_suppression",
    "reuse_recent_skip_validation",
)


def _material_dedup_killed() -> bool:
    return os.environ.get(_MATERIAL_DEDUP_KILL_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


_EPOCH_KIND = "system_operator_skip_remediation"
NEUTRAL_SKIP_DEFER_REASON = "direct_skip_not_confirmed:neutral_or_missing_skip_kind"
STALE_SNAPSHOT_DEFER_REASON = "candidate_technical_snapshot_stale"
_NO_VALID_UNTIL_EXTEND_REASONS = frozenset({
    NEUTRAL_SKIP_DEFER_REASON,
    STALE_SNAPSHOT_DEFER_REASON,
    "candidate_technical_snapshot_unavailable",
    "candidate_technical_snapshot_incomplete",
    "direct_skip_reassessment_unavailable",
    "operator_structured_schema_invalid",
})
_EFFECTIVE_VALUES = {
    "version": "b1.9",
    "aggregate_signal_health_candidate_veto": False,
    "zero_pending_heartbeat_trading_evaluation": False,
    "aggregate_signal_health_config_tightening": False,
    # ``medium`` is the neutral, risk-aware candidate supply tier.  It still
    # excludes speculative reads and still passes every deterministic guard,
    # System Operator conviction, and Risk Engine check.  ``high`` remains a
    # useful quality label/priority, but must not strand the pipeline when an
    # old automatic tightening made it a universal hard gate.
    "ranker_neutral_confidence_floor": "medium",
    "auto_tightened_confidence_supply_recovery": True,
    "manual_high_confidence_preserved": True,
    # B1.2: a direct model skip is no longer an asymmetric one-pass veto.
    # One bounded counterfactual confirmation must provide typed,
    # candidate-specific adverse evidence. Neutral insufficiency defers.
    "direct_skip_confirmation_required": True,
    "insufficient_evidence_terminal_skip": False,
    "rank_priority_is_terminal_veto": False,
    "unchanged_context_defer_window_minutes": 120,
    # B1.3: terminal language is subordinate to deterministic selected-candidate
    # facts. One carrier is shown per call and neutral retries rotate by carrier.
    "deterministic_adverse_fact_validation": True,
    "single_candidate_model_context": True,
    "candidate_specific_defer": True,
    "blind_direct_skip_counterfactual": True,
    "missing_numeric_values_are_unknown": True,
    "decision_evidence_manifest": "operator-candidate-v3",
    # B1.7: validated claims that reference decision-authored values persist
    # those exact values instead of rendering them as unavailable context facts.
    "validated_decision_fact_values": True,
    # B1.4: exact technical delivery and Learning attribution happen at the
    # same durable carrier scope. Missing technical facts defer before inference;
    # a new exact feature snapshot changes the fingerprint and reopens the call.
    "candidate_targeted_feature_hydration": True,
    "missing_candidate_technical_context_model_invocation": False,
    "candidate_feature_snapshot_reopens_evaluation": True,
    "candidate_scoped_learning_exposure": True,
    "candidate_feature_refresh_wakes_operator": True,
    # B1.5 (2026-07-18): a below-threshold conviction is no longer a terminal
    # candidate skip via either channel. It is evaluation unavailable; the
    # caller preserves the pending carrier and waits for a material context
    # change. This closes the indirect judge-word bypass where SIDEWAYS /
    # account equity / tiny-sample recent performance / neutral funding /
    # missing optional telemetry depressed conviction just enough to consume
    # the pending signal via the debate or conviction-gate channel.
    # min_conviction remains 55 (DL-INV-2 / DL-C01) and debate range [40,80];
    # only the *action* on a sub-threshold outcome changes from skip to defer.
    "min_conviction_terminal_skip": False,
    "debate_not_confirmed_terminal_skip": False,
    "conviction_derived_from_candidate_specific_evidence_only": True,
    "conviction_bypass_reasons_forbidden": True,
    # B1.6 (#321): symbol-level material de-duplication. universe refresh mints a
    # fresh opportunity_id for the same symbol every ~10 min, defeating the
    # per-opportunity fast-path and re-burning LLM calls on a symbol already
    # judged direct_skip. The shared safety key is operator_decision_material_fingerprint
    # (symbol-level, snapshot-id-free): a real market move flips it and reopens.
    # Emergency revert: set ALETHEIA_DISABLE_MATERIAL_DEDUP=1 (no redeploy needed).
    # Emit-source suppression: don't mint a new open row for a symbol with an
    # active direct_skip defer whose material fingerprint is unchanged.
    "emit_active_defer_suppression": True,
    # LLM fast-path: match the market-quiet suppression by symbol+direction+
    # market_type (+ material fingerprint guard) instead of opportunity_id.
    "symbol_level_defer_suppression": True,
    # Reassess reuse: skip the second (counterfactual) LLM call when a recent
    # validated adverse skip exists for the same setup at an unchanged material
    # fingerprint.
    "reuse_recent_skip_validation": True,
    "reuse_skip_validation_window_minutes": 30,
    # B1.8: an unconfirmed skip (missing skip_kind) is not a 120-minute market
    # hedge. Keep a short re-ask cooldown so feature-snapshot churn cannot
    # re-burn the LLM every 10 minutes, but do not freeze the only queued
    # candidate for two hours or suppress a fresh emit of the same setup.
    # Stale technical snapshots must not chain another 120-minute window —
    # that window equals the freshness budget and guarantees another stale.
    "neutral_skip_kind_emit_suppression": False,
    "neutral_skip_kind_defer_window_minutes": 30,
    "technical_stale_defer_window_minutes": 20,
    # B1.9: stale/shrug/unavailable is not "worth another look". Extending
    # valid_until on those reasons life-supports a zombie carrier (UNI 6f46a46a84b1
    # chained 08:05 → 08:25 → 08:45). Market-judgment retries still widen TTL.
    "technical_or_shrug_defer_extends_valid_until": False,
}
_AFFECTED_PATHS = (
    "system_operator_candidate_context",
    "system_operator_heartbeat_evaluation",
    "operator_loop_aggregate_signal_diagnosis",
    "paper_ranker_confidence_gate",
    "operator_loop_candidate_supply_recovery",
    "operator_direct_skip_fact_validation",
    "operator_candidate_specific_defer",
    "operator_decision_evidence_manifest",
    "operator_candidate_targeted_feature_context",
    "operator_candidate_context_availability",
    "operator_candidate_learning_attribution",
    "universe_feature_refresh_operator_wake",
    # B1.5 additions
    "operator_low_conviction_defer_semantics",
    "operator_debate_not_confirmed_defer_semantics",
    "operator_conviction_bypass_reason_ban",
    # B1.6 additions (#321)
    "emit_active_defer_material_suppression",
    "operator_symbol_level_defer_suppression",
    "operator_reuse_recent_skip_validation",
    # B1.8 additions
    "operator_neutral_skip_kind_short_defer",
    "emit_neutral_skip_kind_not_suppressed",
    "operator_stale_snapshot_short_defer",
    # B1.9
    "operator_stale_shrug_does_not_extend_valid_until",
)


def ensure_skip_remediation_policy_epoch() -> str:
    """Return the durable B1.9 policy epoch, rotating only if policy changes."""
    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    )
    return persist_policy_epoch(epoch)


def skip_policy_context() -> dict:
    """Return the exact non-secret effective values used by the decision path.

    Applies the ALETHEIA_DISABLE_MATERIAL_DEDUP kill switch at read time: when
    set, every #321 material-dedup consumer flag is forced OFF regardless of the
    configured epoch value, so operators can instantly revert to prior behavior.
    """
    values = dict(_EFFECTIVE_VALUES)
    if _material_dedup_killed():
        for flag in _MATERIAL_DEDUP_CONSUMER_FLAGS:
            values[flag] = False
    return values


def defer_window_minutes_for_reason(reason: str | None, default: int = 120) -> int:
    """Return the bounded re-ask window for one persisted defer reason.

    Confirmed market-quiet hedges keep the 120-minute default. An unconfirmed
    skip and a stale technical snapshot use shorter windows so the only queued
    candidate cannot chain two-hour freezes.
    """
    values = skip_policy_context()
    code = str(reason or "")
    fallback = max(int(default), 1)
    if code == NEUTRAL_SKIP_DEFER_REASON:
        return max(int(values.get("neutral_skip_kind_defer_window_minutes") or 30), 1)
    if code == STALE_SNAPSHOT_DEFER_REASON:
        return max(int(values.get("technical_stale_defer_window_minutes") or 20), 1)
    return fallback


def defer_extends_carrier_valid_until(reason: str | None) -> bool:
    """Whether this defer may CAS-extend Opportunity.valid_until.

    Low-conviction and debate retries still get a second look inside a widened
    lifecycle. A stale/incomplete snapshot or an unconfirmed shrug is not a
    market hedge — extending valid_until turns the only queued carrier into a
    zombie that outlives its original TTL.
    """
    values = skip_policy_context()
    if values.get("technical_or_shrug_defer_extends_valid_until"):
        return True
    return str(reason or "") not in _NO_VALID_UNTIL_EXTEND_REASONS


def emit_suppression_applies_to_defer(prior_skip: dict | None) -> bool:
    """Whether emit-source suppression should honor this active-defer row.

    Validated adverse skips still suppress a same-fingerprint remint. A shrug
    (``neutral_or_missing_skip_kind``) does not — that is not a market judgment.
    """
    values = skip_policy_context()
    if not values.get("emit_active_defer_suppression"):
        return False
    if not prior_skip:
        return False
    reason = str(prior_skip.get("defer_reason") or "")
    if reason == NEUTRAL_SKIP_DEFER_REASON and not values.get(
        "neutral_skip_kind_emit_suppression"
    ):
        return False
    return True
