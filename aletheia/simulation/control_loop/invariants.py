"""Checks intentionally separate from Operator decision and dispatch code."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .artifacts import write_failure_artifacts


class ControlLoopInvariantError(AssertionError):
    pass


_OPPOSING = (
    frozenset({"reduce_sizing", "increase_sizing"}),
)
_CONTROL_ACTIONS = {
    "reduce_sizing", "increase_sizing",
}


def assert_parameter_bounds(harness: Any) -> None:
    cfg = harness.load_config()
    sizing = float(cfg.risk.demo.capital.max_position_margin_usdt)
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


DEFAULT_INVARIANTS: tuple[Callable[[Any], None], ...] = (
    assert_parameter_bounds,
    assert_no_opposing_actions,
    assert_no_orphan_control_actions,
    assert_executed_actions_are_audited,
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
