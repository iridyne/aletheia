"""Executable SDD claims for the measured Learning evolution target."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_evolution"


@spec_claim(
    SpecClaim(
        "LE-S04",
        _CONTRACT,
        "§2.2 declared-path registry",
        "L2a",
        "Every behavior-changing Learning path has one unique registry entry with an authority ceiling, overlap group, outcome source, and static fallback.",
        status="implemented",
    )
)
def test_learning_behavior_paths_are_declared_with_safe_fallbacks():
    from aletheia.learning.registry import (
        LEARNING_PATHS,
        supported_paper_influence_path_ids,
    )

    by_id = {path.path_id: path for path in LEARNING_PATHS}
    assert len(by_id) == len(LEARNING_PATHS)
    assert {
        "universe_family_calibration",
        "observer_advisory_bundle",
        "ranker_case_score",
        "ranker_memo_score",
        "ranker_factor_filter",
        "ranker_learned_guard",
        "operator_learning_bundle",
        "risk_lesson_constraint",
        "persona_sizing",
        "advisory_skill",
        "exit_policy_advice",
        "filter_ttl_shadow",
        "operator_safety_control",
        "trust_guard_observability",
    } <= set(by_id)
    for path in LEARNING_PATHS:
        assert path.producer
        assert path.consumer
        assert path.authority
        assert path.overlap_group
        assert path.outcome_source
        assert path.fallback
        assert not ({"credentials", "live_unlock", "order_submission"} & set(path.allowed_effects))

    assert by_id["exit_policy_advice"].allowed_effects == ("read_only",)
    assert by_id["operator_safety_control"].is_learning_treatment is False
    assert by_id["trust_guard_observability"].allowed_effects == ("observe",)
    assert by_id["ranker_learned_guard"].consumer_wired is True
    assert by_id["ranker_learned_guard"].settings_supported is True
    assert by_id["advisory_skill"].consumer_wired is False
    assert by_id["advisory_skill"].settings_supported is False
    # filter_parameter_tune is a skeleton placeholder — declared so future PRs can
    # incrementally land the outcome contract, override table, consumer wiring, and
    # auto-hypothesis generator against a stable named path. Every safety gate is
    # off in this declaration: no consumer reads overrides, cohorts.SUPPORTED_PATH_IDS
    # rejects any accidental exposures (is_learning_treatment=False), Settings does
    # not surface it. See docs/archive/legacy/plans/learning/filter_parameter_tune_design.md.
    assert "filter_parameter_tune" in by_id
    fpt = by_id["filter_parameter_tune"]
    assert fpt.is_learning_treatment is False
    assert fpt.consumer_wired is False
    assert fpt.settings_supported is False
    assert fpt.allowed_effects == ("filter_parameter",)
    assert fpt.overlap_group == "filter_parameter"
    assert supported_paper_influence_path_ids() == {
        "universe_family_calibration",
        "operator_learning_bundle",
    }


def test_paper_influence_support_requires_every_capability(monkeypatch):
    from aletheia.learning import registry
    from aletheia.learning.registry import LearningPath

    supported = LearningPath(
        path_id="supported",
        producer="producer",
        consumer="consumer",
        authority="paper authority",
        allowed_effects=("score_adjustment",),
        overlap_group="test",
        outcome_source="settled outcome",
        fallback="static fallback",
        measurement_supported=True,
    )
    monkeypatch.setattr(registry, "LEARNING_PATHS", (
        supported,
        replace(supported, path_id="not_treatment", is_learning_treatment=False),
        replace(supported, path_id="consumer_unwired", consumer_wired=False),
        replace(supported, path_id="settings_unsupported", settings_supported=False),
        replace(supported, path_id="measurement_unwired", measurement_supported=False),
    ))

    assert registry.supported_paper_influence_path_ids() == {"supported"}


@spec_claim(SpecClaim(
    "LE-S05", _CONTRACT, "§2.2 Settings path inventory", "L2a",
    "Settings renders registry-backed supported/unsupported checkboxes and has no free-text paper-influence path editor.",
    status="implemented",
))
def test_learning_settings_uses_registry_backed_path_checkboxes():
    root = Path(__file__).resolve().parents[2]
    settings = (root / "frontend/src/pages/Settings.tsx").read_text(encoding="utf-8")
    client = (root / "frontend/src/api/client.ts").read_text(encoding="utf-8")
    start = settings.index('label="Allowed paper paths"')
    end = settings.index('label="Auto-refreeze"', start)
    section = settings[start:end]
    assert 'type="checkbox"' in section
    assert "learning_path_options" in settings
    # Every backend capability state has a truthful, distinct UI label.
    assert "Consumer unwired" in section
    assert "Measurement unwired" in section
    assert "Authority unwired" in section
    assert ".split(',')" not in section
    assert "consumer_unwired" in client
    assert "measurement_unwired" in client


@spec_claim(
    SpecClaim(
        "LE-B17",
        _CONTRACT,
        "§9.8 narrative evidence",
        "L2b",
        "Narrative and diagnostic artifacts have zero quantitative sample weight.",
        status="implemented",
    )
)
def test_narrative_and_diagnostic_evidence_cannot_add_samples():
    from aletheia.learning.evidence import EvidenceClass, quantitative_sample_weight

    assert quantitative_sample_weight(EvidenceClass.SETTLED_QUANTITATIVE) == 1
    assert quantitative_sample_weight(EvidenceClass.DETERMINISTIC_DERIVED) == 0
    assert quantitative_sample_weight(EvidenceClass.NARRATIVE) == 0
    assert quantitative_sample_weight(EvidenceClass.DIAGNOSTIC_PROPOSAL) == 0


@spec_claim(
    SpecClaim(
        "LE-B18",
        _CONTRACT,
        "§9.8 exit diagnostics",
        "L2b",
        "Exit-policy advice remains read-only and has no config, risk, position-manager, order, or execution mutation path.",
        status="implemented",
    )
)
def test_exit_policy_advisor_has_no_mutation_consumer():
    root = Path(__file__).resolve().parents[2]
    path = root / "aletheia" / "analytics" / "exit_policy_advisor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_modules = {
        "aletheia.config.loader",
        "aletheia.risk",
        "aletheia.execution",
        "aletheia.portfolio.position_manager",
    }
    forbidden_calls = {
        "save_config",
        "update_position",
        "submit_proposal",
        "execute_order",
        "adjust_stop",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not any((node.module or "").startswith(prefix) for prefix in forbidden_modules)
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert name not in forbidden_calls


@spec_claim(
    SpecClaim(
        "LE-B21",
        _CONTRACT,
        "§6.1 policy epochs",
        "L2b",
        "Concurrent effective policy changes produce deterministic non-secret epochs and invalidate affected experiment comparisons.",
        status="partial",
    ),
    SpecClaim(
        "LE-C13",
        _CONTRACT,
        "§2.2 concurrent safety controls",
        "L2c",
        "Safety tightening is never withheld and is never eligible as a Learning treatment.",
        status="partial",
    ),
)
def test_policy_epochs_invalidate_affected_learning_without_withholding_safety():
    from aletheia.learning.policy_epochs import build_policy_epoch, compare_policy_epochs

    before = build_policy_epoch(
        "operator_config",
        {"min_confidence": "medium", "api_secret": "must-not-persist"},
        affected_path_ids=("ranker_factor_filter",),
    )
    reordered = build_policy_epoch(
        "operator_config",
        {"api_secret": "different-secret", "min_confidence": "medium"},
        affected_path_ids=("ranker_factor_filter",),
    )
    tightened = build_policy_epoch(
        "operator_config",
        {"min_confidence": "high"},
        affected_path_ids=("ranker_factor_filter",),
        safety_required=True,
    )

    assert before.epoch_hash == reordered.epoch_hash
    assert "secret" not in str(before.effective_values).lower()
    unchanged = compare_policy_epochs(before, reordered, "ranker_factor_filter")
    assert unchanged.experiment_action == "continue"
    changed = compare_policy_epochs(before, tightened, "ranker_factor_filter")
    assert changed.experiment_action == "invalidate"
    assert changed.safety_action == "apply_immediately"
    assert tightened.is_learning_treatment is False
