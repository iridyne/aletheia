"""Versioned B1 policy attribution for System Operator skip remediation."""

from __future__ import annotations

import os

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


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
_EFFECTIVE_VALUES = {
    "version": "b1.6",
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
)


def ensure_skip_remediation_policy_epoch() -> str:
    """Return the durable B1.4 policy epoch, rotating only if policy changes."""
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
