"""Checks intentionally separate from Operator decision and dispatch code."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .artifacts import write_failure_artifacts


class ControlLoopInvariantError(AssertionError):
    pass


_OPPOSING = (
    frozenset({"tighten_confidence", "loosen_confidence"}),
    frozenset({"reduce_sizing", "increase_sizing"}),
)
_CONTROL_ACTIONS = {
    "tighten_confidence", "loosen_confidence", "reduce_sizing", "increase_sizing",
}


def assert_parameter_bounds(harness: Any) -> None:
    cfg = harness.load_config()
    confidence = cfg.trading.min_confidence_to_trade
    sizing = float(cfg.risk.demo.capital.max_position_notional_usdt)
    if confidence not in {"speculative", "medium", "high"}:
        raise ControlLoopInvariantError(f"confidence escaped its ladder: {confidence!r}")
    if not 20.0 <= sizing <= 500.0:
        raise ControlLoopInvariantError(f"sizing escaped hard bounds: {sizing}")


def assert_no_opposing_actions(harness: Any) -> None:
    for summary in harness.summaries:
        actions = {item.get("action") for item in summary.get("results", [])}
        for pair in _OPPOSING:
            if pair <= actions:
                raise ControlLoopInvariantError(
                    f"one cycle emitted opposing actions: {sorted(pair)}"
                )


def assert_no_orphan_control_actions(harness: Any) -> None:
    for summary in harness.summaries:
        for result in summary.get("results", []):
            if result.get("action") in _CONTROL_ACTIONS and result.get("status") == "proposed":
                raise ControlLoopInvariantError(
                    f"control action has no expiring consumer: {result.get('action')}"
                )


def assert_executed_actions_are_audited(harness: Any) -> None:
    for summary in harness.summaries:
        for result in summary.get("results", []):
            if result.get("action") not in _CONTROL_ACTIONS or result.get("status") != "executed":
                continue
            param = result.get("param")
            row = harness.db.execute(
                "SELECT 1 FROM audit_events WHERE actor='operator_loop' "
                "AND event_type='config_patch' AND entity_id=? LIMIT 1",
                (param,),
            ).fetchone()
            if row is None:
                raise ControlLoopInvariantError(
                    f"executed action lacks durable audit evidence: {result.get('action')}"
                )


def assert_confidence_liveness(harness: Any) -> None:
    if not harness.summaries:
        return
    assessment = harness.summaries[-1].get("assessment", {})
    hours = assessment.get("hours_since_last_trade")
    if hours is None or hours < 4 or assessment.get("kill_switch"):
        return
    if harness.load_config().trading.min_confidence_to_trade != "high":
        return
    blocker = harness.db.execute(
        "SELECT 1 FROM audit_events WHERE event_type='operator_control_loop_blocked' "
        "AND entity_id='min_confidence_to_trade' LIMIT 1"
    ).fetchone()
    if blocker is None:
        raise ControlLoopInvariantError(
            "confidence remained high after a 4h drought without a durable visible blocker"
        )


DEFAULT_INVARIANTS: tuple[Callable[[Any], None], ...] = (
    assert_parameter_bounds,
    assert_no_opposing_actions,
    assert_no_orphan_control_actions,
    assert_executed_actions_are_audited,
    assert_confidence_liveness,
)


def assert_invariants(
    harness: Any,
    checks: Iterable[Callable[[Any], None]] = DEFAULT_INVARIANTS,
    *,
    artifact_root: Path | None = None,
    run_id: str = "dcls-failure",
    seed: int = 0,
    reproduce: str = "uv run pytest tests/simulation/control_loop/ -q",
) -> None:
    try:
        for check in checks:
            check(harness)
    except BaseException as error:
        if artifact_root is not None:
            write_failure_artifacts(
                harness, error, artifact_root=artifact_root, run_id=run_id,
                seed=seed, reproduce=reproduce,
            )
        raise
