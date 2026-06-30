"""Spec: Subsystem Observers — equity_observer + prediction_observer

Covers structural and invariant claims for the two observation-only agents
that feed non-execution signals into the Opportunity pipeline.

Contract: agent_contracts.md §4 (observer agents)
"""

import ast
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent
_EQUITY_OBS = _ROOT / "aletheia" / "agents" / "equity_observer.py"
_PRED_OBS = _ROOT / "aletheia" / "agents" / "prediction_observer.py"
_SYMBOLS = _ROOT / "aletheia" / "data" / "symbols.py"
_SCHEDULER = _ROOT / "aletheia" / "agent_runtime" / "scheduler.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OBS-S01", "agent_contracts", "§4 equity_observer", "L2a",
        "aletheia/agents/equity_observer.py exists and exports a callable run_equity_observer",
    ),
)
def test_equity_observer_exists_and_callable():
    assert _EQUITY_OBS.exists(), "equity_observer.py not found"
    from aletheia.agents import equity_observer

    assert callable(equity_observer.run_equity_observer)


@spec_claim(
    SpecClaim(
        "OBS-S02", "agent_contracts", "§4 prediction_observer", "L2a",
        "aletheia/agents/prediction_observer.py exists and exports a callable run_prediction_observer",
    ),
)
def test_prediction_observer_exists_and_callable():
    assert _PRED_OBS.exists(), "prediction_observer.py not found"
    from aletheia.agents import prediction_observer

    assert callable(prediction_observer.run_prediction_observer)


@spec_claim(
    SpecClaim(
        "OBS-S05", "agent_contracts", "§4 prediction_observer tables", "L2a",
        "prediction_observer writes to prediction_events, prediction_markets, "
        "prediction_probability_snapshots, prediction_market_claims tables",
    ),
)
def test_prediction_observer_writes_expected_tables():
    """Grep prediction_observer.py source for the four required table/function names."""
    src = _PRED_OBS.read_text(encoding="utf-8")
    required = {
        "upsert_prediction_event": "prediction_events",
        "upsert_prediction_market": "prediction_markets",
        "insert_probability_snapshot": "prediction_probability_snapshots",
        "insert_prediction_claim": "prediction_market_claims",
    }
    for func_name, table in required.items():
        assert func_name in src, (
            f"prediction_observer.py does not call {func_name!r} "
            f"(expected write to {table!r})"
        )


@spec_claim(
    SpecClaim(
        "OBS-S06", "agent_contracts", "§4 equity_observer scheduler", "L2a",
        "equity_observer is registered in scheduler.py via add_job",
    ),
)
def test_equity_observer_registered_in_scheduler():
    src = _SCHEDULER.read_text(encoding="utf-8")
    # add_job call must reference the agent id string "equity_observer"
    assert 'add_job' in src and '"equity_observer"' in src, (
        "scheduler.py does not contain an add_job call referencing 'equity_observer'"
    )
    # Verify both appear in proximity (same add_job block)
    lines = src.splitlines()
    add_job_lines = [i for i, ln in enumerate(lines) if "add_job" in ln]
    found = False
    for lineno in add_job_lines:
        block = "\n".join(lines[lineno : lineno + 6])
        if "equity_observer" in block:
            found = True
            break
    assert found, "No add_job block references 'equity_observer' within 6 lines"


@spec_claim(
    SpecClaim(
        "OBS-S07", "agent_contracts", "§4 xstock mapping", "L2a",
        "_XSTOCK_BASE_TO_TICKER mapping dict exists in aletheia/data/symbols.py",
    ),
)
def test_xstock_base_to_ticker_mapping_exists():
    """The xStock symbol map lives in symbols.py (equity_observer imports ticker_to_xstock from there)."""
    assert _SYMBOLS.exists(), "aletheia/data/symbols.py not found"
    src = _SYMBOLS.read_text(encoding="utf-8")
    assert "_XSTOCK_BASE_TO_TICKER" in src, (
        "_XSTOCK_BASE_TO_TICKER dict not found in aletheia/data/symbols.py"
    )
    # Also verify it is assigned as a dict (plain or annotated), not just a comment.
    # The source uses an annotated assignment: `_XSTOCK_BASE_TO_TICKER: dict[str, str] = {…}`
    # which the AST represents as AnnAssign, not Assign.
    tree = ast.parse(src)
    dict_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            dict_names.add(node.target.id)
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Dict)
        ):
            dict_names.add(node.targets[0].id)
    assert "_XSTOCK_BASE_TO_TICKER" in dict_names, (
        "_XSTOCK_BASE_TO_TICKER is not assigned as a dict literal in symbols.py"
    )


@spec_claim(
    SpecClaim(
        "OBS-S08", "agent_contracts", "§4 stock_observer deprecated", "L2a",
        "stock_observer (deprecated) is NOT registered in scheduler.py via add_job",
    ),
)
def test_stock_observer_not_registered_in_scheduler():
    src = _SCHEDULER.read_text(encoding="utf-8")
    lines = src.splitlines()
    add_job_lines = [i for i, ln in enumerate(lines) if "add_job" in ln]
    for lineno in add_job_lines:
        block = "\n".join(lines[lineno : lineno + 6])
        assert "stock_observer" not in block, (
            f"scheduler.py add_job at line {lineno + 1} still registers "
            "deprecated stock_observer"
        )


# ---------------------------------------------------------------------------
# L2c — Invariants (AST-level, no runtime execution)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OBS-S03", "agent_contracts", "§4 prediction_observer opportunity invariant", "L2c",
        "Every Opportunity constructed in prediction_observer has "
        "execution_eligible=False and asset_class='prediction'",
    ),
)
def test_prediction_observer_opportunity_kwargs():
    """Walk the AST of prediction_observer.py and assert every Opportunity() call
    has the correct execution_eligible and asset_class keyword arguments."""
    tree = ast.parse(_PRED_OBS.read_text(encoding="utf-8"))
    opportunity_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Opportunity"
    ]
    assert opportunity_calls, "No Opportunity() constructor calls found in prediction_observer.py"

    for call in opportunity_calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords}

        # execution_eligible must be False
        assert "execution_eligible" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing execution_eligible kwarg"
        )
        val = kwargs["execution_eligible"]
        assert isinstance(val, ast.Constant) and val.value is False, (
            f"Opportunity() at line {call.lineno}: execution_eligible={ast.unparse(val)!r}, "
            "expected False"
        )

        # asset_class must be 'prediction'
        assert "asset_class" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing asset_class kwarg"
        )
        ac = kwargs["asset_class"]
        assert isinstance(ac, ast.Constant) and ac.value == "prediction", (
            f"Opportunity() at line {call.lineno}: asset_class={ast.unparse(ac)!r}, "
            "expected 'prediction'"
        )


@spec_claim(
    SpecClaim(
        "OBS-S04", "agent_contracts", "§4 equity_observer opportunity invariant", "L2c",
        "Every Opportunity constructed in equity_observer has execution_eligible=False",
    ),
)
def test_equity_observer_opportunity_execution_eligible():
    """Walk the AST of equity_observer.py and assert every Opportunity() call
    has execution_eligible=False."""
    tree = ast.parse(_EQUITY_OBS.read_text(encoding="utf-8"))
    opportunity_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Opportunity"
    ]
    assert opportunity_calls, "No Opportunity() constructor calls found in equity_observer.py"

    for call in opportunity_calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords}

        assert "execution_eligible" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing execution_eligible kwarg"
        )
        val = kwargs["execution_eligible"]
        assert isinstance(val, ast.Constant) and val.value is False, (
            f"Opportunity() at line {call.lineno}: execution_eligible={ast.unparse(val)!r}, "
            "expected False"
        )


# ---------------------------------------------------------------------------
# Round 2: Market Observer LLM Pre-Filter (OBS-PRE-FILTER)
# Coriva impl plan 2026-06-30 — P1-7
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OBS-PRE-FILTER", "agent_contracts",
        "§market_observer pre_filter_no_signal",
        "L2a",
        "_has_sufficient_signal() 函数存在于 market_observer.py，"
        "且在 _analyze_symbol() 中于 llm_structured 调用前被调用。",
        status="implemented",
    ),
)
def test_observer_has_pre_filter_function():
    import inspect
    import aletheia.agents.market_observer as mo
    assert hasattr(mo, "_has_sufficient_signal"), (
        "_has_sufficient_signal 不存在 — OBS-PRE-FILTER 未实现"
    )
    src = inspect.getsource(mo._analyze_symbol)
    assert "_has_sufficient_signal" in src, (
        "_analyze_symbol 未调用 _has_sufficient_signal — pre-filter 未接入"
    )


@spec_claim(
    SpecClaim(
        "OBS-PRE-FILTER-LOGIC", "agent_contracts",
        "§market_observer pre_filter_signal_detection",
        "L2b",
        "_has_sufficient_signal: RSI>12 偏离返回 True；平静市场返回 False。",
        status="implemented",
    ),
)
def test_pre_filter_signal_detection():
    import aletheia.agents.market_observer as mo
    assert mo._has_sufficient_signal("| RSI | 1h | 72.3 | rising |") is True
    assert mo._has_sufficient_signal("| RSI | 1h | 51.2 | flat |") is False
