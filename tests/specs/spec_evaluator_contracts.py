"""Spec: Strategy Evaluator contracts (written 2026-06-28).

Two structural regression locks for the RuleSet evaluator, both surfaced by the
`content.strategy_health` doctor probe (3 zombie strategies trading 0 fills for
8-10 days). Root cause was NOT bad strategies — it was two silent evaluator bugs
that made every momentum_pct condition evaluate against the wrong data:

EV-S01 — the indicator dispatch read the param key `lookback` for momentum_pct
         while the DSL contract (dsl.py) and the LLM strategy designer both emit
         `{"params": {"period": N}}`. Every other indicator reads `period`. So
         momentum_pct silently always used the default lookback=10, ignoring the
         period the strategy chose (4 / 5 / 24 in the zombie rulesets).

EV-S02 — multi-timeframe conditions (Condition.timeframe="4h") were never fed
         multi_tf_bars by ruleset_scanner._scan_strategy, so they fell through to
         the primary-timeframe branch and were evaluated on 1h bars (wrong
         horizon entirely).

These are L2a structural tripwires: they parse the source AST so a future refactor
that reverts either fix fails loudly. They do not require a populated DB.
"""

from __future__ import annotations

import ast
import pathlib

from tests.specs.spec_meta import SpecClaim, spec_claim


def _project_root() -> str:
    return str(pathlib.Path(__file__).resolve().parents[2])


def _read(rel: str) -> str:
    return (pathlib.Path(_project_root()) / rel).read_text(encoding="utf-8")


_EVALUATOR = "aletheia/strategy/evaluator.py"
_SCANNER = "aletheia/trading/ruleset_scanner.py"


# ---------------------------------------------------------------------------
# EV-S01 — momentum_pct must honour the `period` param key
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("EV-S01", "evaluator_contract", "§momentum_pct period key", "L2a",
              "compute_indicator's momentum_pct branch must read the 'period' param "
              "key (the DSL/designer contract), not only 'lookback' — else every "
              "momentum_pct condition silently uses the default and ignores the "
              "strategy's chosen period"),
)
def test_momentum_pct_reads_period_key():
    """The momentum_pct dispatch must reference the 'period' param key.

    Guards the zombie-strategy root cause: evaluator.py read p.get("lookback", 10)
    while rulesets store {"params": {"period": N}}. We locate the momentum_pct
    Call node in compute_indicator and assert a Constant "period" appears anywhere
    in its argument subtree.
    """
    src = _read(_EVALUATOR)
    tree = ast.parse(src)

    found_call = False
    period_referenced = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name == "momentum_pct":
                found_call = True
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and sub.value == "period":
                        period_referenced = True

    assert found_call, "no momentum_pct(...) call found in evaluator.py — refactor likely happened"
    assert period_referenced, (
        "momentum_pct dispatch does not read the 'period' param key — it would "
        "silently use the default lookback and ignore the strategy's period. "
        "Fix: int(p.get('period', p.get('lookback', 10))). See "
        "docs/plans/quirky-conjuring-dream.md FIX 1 (EV-S01)."
    )


# ---------------------------------------------------------------------------
# EV-S02 — scanner must inject multi_tf_bars for cross-timeframe conditions
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("EV-S02", "evaluator_contract", "§MTF scanner injection", "L2a",
              "_scan_strategy must collect referenced timeframes and pass "
              "multi_tf_bars to evaluate_at_bar, so Condition.timeframe='4h' is "
              "evaluated on 4h bars instead of silently falling back to 1h"),
)
def test_scanner_injects_multi_tf_bars():
    """ruleset_scanner must build and pass multi_tf_bars.

    (a) it must call collect_referenced_timeframes (timeframe tree walk), and
    (b) at least one evaluate_at_bar call must pass the multi_tf_bars keyword.
    """
    src = _read(_SCANNER)
    tree = ast.parse(src)

    calls_collect = False
    evaluate_with_mtf = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
            if name == "collect_referenced_timeframes":
                calls_collect = True
            if name == "evaluate_at_bar":
                if any(kw.arg == "multi_tf_bars" for kw in node.keywords):
                    evaluate_with_mtf = True

    assert calls_collect, (
        "ruleset_scanner._scan_strategy never calls collect_referenced_timeframes — "
        "MTF conditions get no extra-timeframe bars. See FIX 1 (EV-S02)."
    )
    assert evaluate_with_mtf, (
        "evaluate_at_bar is called without the multi_tf_bars keyword — "
        "Condition.timeframe='4h' silently evaluates on the primary timeframe. "
        "See FIX 1 (EV-S02)."
    )
