"""Small, executable inventory of behavior-changing authority.

This is deliberately narrower than the Learning path registry. The Learning
registry says what a path may propose; this inventory is the final consumer-side
answer to who may actually block a paper candidate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BehaviorAuthority:
    behavior_id: str
    consumer_entrypoints: tuple[str, ...]
    allowed_sources: tuple[str, ...]
    prospective_authority_sources: tuple[str, ...]
    learning_path_by_source: tuple[tuple[str, str], ...]
    forbidden_sources: tuple[str, ...]
    fallback: str


BEHAVIOR_AUTHORITIES: tuple[BehaviorAuthority, ...] = (
    BehaviorAuthority(
        behavior_id="block_paper_candidate",
        consumer_entrypoints=(
            "aletheia.knowledge.guards.evaluate_guards",
            "aletheia.agents.paper_trader_ranker._evaluate_candidate",
        ),
        # Seed guards are shipped static safety policy. Manual guards are explicit
        # owner-authored static policy. Neither is historical auto-learning.
        allowed_sources=("seed_guard", "manual_guard", "learned_guard", "factor_filter"),
        prospective_authority_sources=("learned_guard", "factor_filter"),
        learning_path_by_source=(
            ("learned_guard", "ranker_learned_guard"),
            ("factor_filter", "ranker_factor_filter"),
        ),
        forbidden_sources=(
            "lesson",
            "historical_auto_guard",
            "inline_historical_ranker",
            "arbitrary_ranker_condition",
        ),
        fallback="observe_only",
    ),
)


def get_behavior_authority(behavior_id: str) -> BehaviorAuthority:
    for authority in BEHAVIOR_AUTHORITIES:
        if authority.behavior_id == behavior_id:
            return authority
    raise KeyError(f"undeclared behavior authority: {behavior_id}")


def authorize_candidate_block(
    *,
    source: str,
    prospective_authority: bool = False,
    learning_path_id: str | None = None,
) -> bool:
    """Return whether this source may block one paper candidate.

    Unknown/forbidden sources fail closed to no behavioral effect. Learning
    sources additionally require their exact registered path and a prospective
    authority decision. The safe fallback is observation, never a silent block.
    """
    authority = get_behavior_authority("block_paper_candidate")
    if source in authority.forbidden_sources or source not in authority.allowed_sources:
        return False
    if source not in authority.prospective_authority_sources:
        return True

    required_paths = dict(authority.learning_path_by_source)
    required_path = required_paths.get(source)
    if not prospective_authority or learning_path_id != required_path:
        return False
    try:
        from aletheia.learning.registry import get_learning_path

        learning_path = get_learning_path(required_path)
    except (KeyError, TypeError):
        return False
    return "candidate_filter" in learning_path.allowed_effects
