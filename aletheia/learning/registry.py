"""Declared inventory of Learning and Learning-adjacent behavior paths.

The registry is intentionally static in Phase 0. It makes authority and fallback
reviewable without changing any consumer behavior. Later phases attach durable
experiments/exposures to these stable path ids.
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


LEARNING_PATHS: tuple[LearningPath, ...] = (
    LearningPath(
        "universe_family_calibration", "universe calibration stats", "universe allocator",
        "bounded paper opportunity-score multiplier", ("score_adjustment",),
        "universe", "fixed-horizon candidate net outcome", "neutral multiplier 1.0",
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
        "paper-entry candidate block", ("candidate_filter",), "ranker",
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
    ),
    LearningPath(
        "exit_policy_advice", "exit policy advisor", "analytics API",
        "read-only diagnostic", ("read_only",), "diagnostic",
        "trade-path diagnostic", "no mutation",
        is_learning_treatment=False,
    ),
    LearningPath(
        "filter_ttl_shadow", "filter replay/controller", "shadow experiment report",
        "observe-only recommendation", ("read_only",), "filter_shadow",
        "fixed-horizon filter outcome", "production filter unchanged",
    ),
    LearningPath(
        "operator_safety_control", "operator system assessment", "operator config control",
        "existing safety tightening and human-gated loosening", ("safety_control",),
        "concurrent_policy", "operator health and audit outcomes", "existing operator contract",
        is_learning_treatment=False,
    ),
    LearningPath(
        "trust_guard_observability", "trust guard chain", "pre-risk audit context",
        "observability only", ("observe",), "trust_observability",
        "audit facts", "no blocking effect",
        is_learning_treatment=False,
    ),
)


def get_learning_path(path_id: str) -> LearningPath:
    for path in LEARNING_PATHS:
        if path.path_id == path_id:
            return path
    raise KeyError(f"undeclared Learning path: {path_id}")
