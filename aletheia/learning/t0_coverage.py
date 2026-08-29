"""Versioned, code-declared T0 coverage contracts by Learning path."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class T0CoverageContractV1:
    path_id: str
    version: str
    consumer_stage: str
    anchor_kind: str
    required_domains: tuple[str, ...]
    recommended_domains: tuple[str, ...] = ()
    population_required: bool = False
    max_material_scope_dimensions: int = 3


_RANKER_REQUIRED = (
    "candidate", "population", "features", "market_identity", "regime",
    "liquidity_cost", "ordering_policy",
)

T0_COVERAGE_CONTRACTS: dict[str, T0CoverageContractV1] = {
    "universe_family_calibration": T0CoverageContractV1(
        "universe_family_calibration", "learning-t0-coverage/ufc/v1",
        "universe_allocation", "opportunity_generation",
        _RANKER_REQUIRED, ("event_context",), True,
    ),
    "ranker_case_score": T0CoverageContractV1(
        "ranker_case_score", "learning-t0-coverage/ranker-case/v1",
        "ranker_evaluation", "filter_decision",
        _RANKER_REQUIRED, ("event_context",), True,
    ),
    "ranker_memo_score": T0CoverageContractV1(
        "ranker_memo_score", "learning-t0-coverage/ranker-memo/v1",
        "ranker_evaluation", "filter_decision",
        (*_RANKER_REQUIRED, "memo_manifest"), (), True,
    ),
    "ranker_factor_filter": T0CoverageContractV1(
        "ranker_factor_filter", "learning-t0-coverage/ranker-factor/v1",
        "ranker_evaluation", "filter_decision", _RANKER_REQUIRED, (), True,
    ),
    "ranker_learned_guard": T0CoverageContractV1(
        "ranker_learned_guard", "learning-t0-coverage/ranker-guard/v1",
        "ranker_evaluation", "filter_decision",
        (*_RANKER_REQUIRED, "guard_manifest"), (), True,
    ),
    "observer_advisory_bundle": T0CoverageContractV1(
        "observer_advisory_bundle", "learning-t0-coverage/observer/v1",
        "observer_prompt_render", "observer_run",
        ("market_identity", "universe", "model_template", "learning_bundle"),
    ),
    "operator_learning_bundle": T0CoverageContractV1(
        "operator_learning_bundle", "learning-t0-coverage/operator/v1",
        "operator_prompt_render", "opportunity_generation",
        (
            "opportunity", "candidate", "risk_portfolio_account",
            "learning_bundle", "model_template",
        ),
    ),
    "risk_lesson_constraint": T0CoverageContractV1(
        "risk_lesson_constraint", "learning-t0-coverage/risk/v1",
        "risk_evaluation", "proposal",
        ("proposal", "risk_limits", "portfolio_account", "lesson_manifest"),
    ),
    "persona_sizing": T0CoverageContractV1(
        "persona_sizing", "learning-t0-coverage/sizing/v1",
        "sizing_calculation", "proposal",
        (
            "proposal", "balance", "portfolio_exposure", "risk_limits",
            "liquidity_capacity", "sizing_inputs",
        ),
    ),
    "exit_policy_advice": T0CoverageContractV1(
        "exit_policy_advice", "learning-t0-coverage/exit/v1",
        "position_management_decision", "position_path_observation",
        ("position_state", "protection_state", "market_identity", "policy"),
    ),
}


def get_t0_coverage_contract(path_id: str) -> T0CoverageContractV1:
    try:
        return T0_COVERAGE_CONTRACTS[path_id]
    except KeyError as exc:
        raise KeyError(f"no T0 coverage contract for Learning path: {path_id}") from exc
