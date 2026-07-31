"""Declared inventory of Learning and Learning-adjacent behavior paths.

The registry keeps capability, authority, and fallback reviewable. Only paths
with ``measurement_supported=True`` may attach durable experiments/exposures;
consumer wiring or Settings visibility alone never grants that capability.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearningPath:
    path_id: str
    producer: str
    consumer: str
    authority: str
    allowed_effects: tuple[str, ...]
    overlap_group: str
    outcome_source: str
    fallback: str
    is_learning_treatment: bool = True
    consumer_wired: bool = True
    settings_supported: bool = True
    measurement_supported: bool = False
    behavior_contract_version: str | None = None


LEARNING_PATHS: tuple[LearningPath, ...] = (
    LearningPath(
        "universe_family_calibration", "universe calibration stats", "universe allocator",
        "bounded paper opportunity-score multiplier", ("score_adjustment",),
        "universe", "fixed-horizon candidate net outcome", "neutral multiplier 1.0",
        measurement_supported=True,
        behavior_contract_version="universe-family-calibration/v2",
    ),
    LearningPath(
        "observer_advisory_bundle", "knowledge router and trading memo", "market observer",
        "advisory prompt context", ("prompt_advisory",), "observer",
        "fixed-horizon emitted-opportunity outcome", "empty Learning memory bundle",
    ),
    LearningPath(
        "ranker_case_score", "retrieved trade cases", "paper ranker",
        "bounded paper score adjustment", ("score_adjustment",), "ranker",
        "fixed-horizon candidate net outcome", "zero case delta",
    ),
    LearningPath(
        "ranker_memo_score", "trading memo", "paper ranker",
        "bounded paper score adjustment", ("score_adjustment",), "ranker",
        "fixed-horizon candidate net outcome", "zero memo delta",
    ),
    LearningPath(
        "ranker_factor_filter", "factor feedback", "paper ranker",
        "prospective paper score/filter treatment", ("candidate_filter", "score_adjustment"), "ranker",
        "fixed-horizon candidate net outcome", "factor filter disabled",
    ),
    LearningPath(
        "ranker_learned_guard", "learned trading guards", "paper ranker",
        "paper-entry candidate block/penalty", ("candidate_filter", "score_adjustment"), "ranker",
        "blocked-candidate net outcome", "shadow-only guard evaluation",
    ),
    LearningPath(
        "operator_learning_bundle", "memo/reasoning/calibration/reflection/lessons", "System Operator",
        "advisory paper decision context", ("prompt_advisory",), "operator",
        "decision-linked realized or fixed-horizon outcome", "no Learning prompt bundle",
    ),
    LearningPath(
        "risk_lesson_constraint", "applied structured risk lesson", "Risk Engine",
        "existing proposal rejection only", ("proposal_filter",), "risk_lesson",
        "proposal and decision-linked outcome", "existing static risk behavior",
    ),
    LearningPath(
        "persona_sizing", "persona outcome learner", "paper sizing policy",
        "bounded paper base-notional adjustment", ("size_adjustment",), "sizing",
        "clean realized source cohort plus fixed-horizon candidate net outcome",
        "static persona baseline notional",
    ),
    LearningPath(
        "advisory_skill", "proven hypothesis skill", "prompt or ranker advisory adapter",
        "advisory only", ("prompt_advisory", "score_adjustment"), "skill",
        "inherited source-path outcome", "skill inactive",
        consumer_wired=False, settings_supported=False,
    ),
    LearningPath(
        "exit_policy_advice", "exit policy advisor", "analytics API",
        "read-only diagnostic", ("read_only",), "diagnostic",
        "trade-path diagnostic", "no mutation",
        is_learning_treatment=False, settings_supported=False,
    ),
    LearningPath(
        "filter_ttl_shadow", "filter replay/controller", "shadow experiment report",
        "observe-only recommendation", ("read_only",), "filter_shadow",
        "fixed-horizon filter outcome", "production filter unchanged",
        is_learning_treatment=False, settings_supported=False,
    ),
    LearningPath(
        "operator_safety_control", "operator system assessment", "operator config control",
        "existing safety tightening and human-gated loosening", ("safety_control",),
        "concurrent_policy", "operator health and audit outcomes", "existing operator contract",
        is_learning_treatment=False, settings_supported=False,
    ),
    LearningPath(
        "trust_guard_observability", "trust guard chain", "pre-risk audit context",
        "observability only", ("observe",), "trust_observability",
        "audit facts", "no blocking effect",
        is_learning_treatment=False, settings_supported=False,
    ),
    # ─── Skeleton placeholder — see docs/archive/legacy/plans/learning/filter_parameter_tune_design.md
    #
    # filter_parameter_tune: the future home for prospective A/B experiments that
    # would loosen a filter parameter (e.g. family_ttl_sec, ranker_confidence_gate)
    # based on filter_controller's persistent overrestrictive_suspect signals.
    #
    # Explicitly NOT WIRED in this declaration:
    #   consumer_wired=False        → no filter reads its overrides at check time
    #   is_learning_treatment=False → cohorts.py::SUPPORTED_PATH_IDS gate rejects
    #                                 any accidental exposure attempts
    #   settings_supported=False    → not exposed in the Settings paper-influence UI
    #
    # This entry exists so the inventory reflects the design intent and future
    # PRs can land the missing pieces (outcome contract, override table,
    # consumer wiring, auto-hypothesis generator, safety envelope) incrementally
    # against a stable named path. See the design doc for the full architecture
    # and what a landing sequence for actual wiring must cover.
    LearningPath(
        "filter_parameter_tune", "persistent filter controller signals", "filter parameter override",
        "bounded paper filter-parameter override (not yet wired)",
        ("filter_parameter",), "filter_parameter",
        "aggregate fixed-horizon filter-cell outcome", "static filter parameter",
        is_learning_treatment=False, consumer_wired=False, settings_supported=False,
    ),
)


def get_learning_path(path_id: str) -> LearningPath:
    for path in LEARNING_PATHS:
        if path.path_id == path_id:
            return path
    raise KeyError(f"undeclared Learning path: {path_id}")


def paper_influence_path_options() -> tuple[dict[str, object], ...]:
    """Return the honest Settings inventory for behavior-changing paths."""
    options = []
    for path in LEARNING_PATHS:
        if not path.is_learning_treatment:
            continue
        supported = bool(
            path.consumer_wired
            and path.settings_supported
            and path.measurement_supported
        )
        status = (
            "supported"
            if supported
            else "consumer_unwired" if not path.consumer_wired else "authority_unwired"
        )
        if path.consumer_wired and path.settings_supported and not path.measurement_supported:
            status = "measurement_unwired"
        options.append({
            "path_id": path.path_id,
            "label": path.producer,
            "supported": supported,
            "status": status,
        })
    return tuple(options)


def supported_paper_influence_path_ids() -> frozenset[str]:
    return frozenset(
        path.path_id
        for path in LEARNING_PATHS
        if (
            path.is_learning_treatment
            and path.consumer_wired
            and path.settings_supported
            and path.measurement_supported
        )
    )
