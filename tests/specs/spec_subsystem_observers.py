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
_EQUITY_OBS = _ROOT / "extensions" / "research" / "equity_observer.py"
_PRED_OBS = _ROOT / "extensions" / "research" / "prediction_observer.py"
_SCHEDULER = _ROOT / "core" / "aletheia" / "agent_runtime" / "scheduler.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "OBS-S01", "agent_contracts", "§4 equity_observer", "L2a",
        "extensions/research/equity_observer.py exists and exports a callable run_equity_observer",
    ),
)
def test_equity_observer_exists_and_callable():
    assert _EQUITY_OBS.exists(), "equity_observer.py not found"
    from extensions.research import equity_observer

    assert callable(equity_observer.run_equity_observer)


@spec_claim(
    SpecClaim(
        "OBS-S02", "agent_contracts", "§4 prediction_observer", "L2a",
        "extensions/research/prediction_observer.py exists and exports a callable run_prediction_observer",
    ),
)
def test_prediction_observer_exists_and_callable():
    assert _PRED_OBS.exists(), "prediction_observer.py not found"
    from extensions.research import prediction_observer

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
        "OBS-S08", "agent_contracts", "§4 stock_observer retired", "L2b",
        "The retired stock_observer cannot be resolved for execution",
    ),
)
def test_retired_stock_observer_cannot_run():
    from aletheia.agents import get_agent_fn

    with pytest.raises(ValueError, match="Unknown agent"):
        get_agent_fn("stock_observer")


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


@spec_claim(
    SpecClaim(
        "OBS-NEWS-ENTITY", "agent_contracts", "§market_observer news context", "L2b",
        "News association requires an exact ticker or asset name; substrings do not match.",
        status="implemented",
    ),
)
def test_news_entity_matching_is_exact():
    from aletheia.data.news_entities import match_crypto_entities

    assert match_crypto_entities("Bitcoin and SOL move") == ["BTC/USDT", "SOL/USDT"]
    assert match_crypto_entities("solution adoption in Canada") == []


@spec_claim(
    SpecClaim(
        "OBS-A01", "agent_contracts", "§4 equity_observer attribution fields", "L2c",
        "Every Opportunity constructed in equity_observer stamps scanner_family='equity_daily' "
        "and a non-null valid_until field. confidence_score must never be NULL — the "
        "LLM-unavailable fallback uses 0 (schema-defined 'no idea' sentinel).",
        status="implemented",
    ),
)
def test_equity_observer_stamps_scanner_family_and_valid_until():
    """AST-level: equity_observer Opportunity() calls must set scanner_family and valid_until."""
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

        assert "scanner_family" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing scanner_family kwarg"
        )
        sf = kwargs["scanner_family"]
        assert isinstance(sf, ast.Constant) and sf.value == "equity_daily", (
            f"Opportunity() at line {call.lineno}: scanner_family={ast.unparse(sf)!r}, "
            "expected 'equity_daily'"
        )

        assert "valid_until" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing valid_until kwarg"
        )

    # confidence_score must not pass None directly — must use a fallback expression
    src = _EQUITY_OBS.read_text(encoding="utf-8")
    assert "confidence_score is not None else 0" in src, (
        "equity_observer must use 'confidence_score if confidence_score is not None else 0' "
        "to prevent NULL confidence_score in persisted Opportunities"
    )


@spec_claim(
    SpecClaim(
        "OBS-A02", "agent_contracts", "§4 prediction_observer attribution fields", "L2c",
        "Every Opportunity constructed in prediction_observer stamps "
        "scanner_family='prediction_market' and a non-null valid_until field. "
        "confidence_score must never be NULL for the LLM-derived top market — "
        "fallback uses 0 when the structured read is unavailable.",
        status="implemented",
    ),
)
def test_prediction_observer_stamps_scanner_family_and_valid_until():
    """AST-level: prediction_observer Opportunity() calls must set scanner_family and valid_until."""
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

        assert "scanner_family" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing scanner_family kwarg"
        )
        sf = kwargs["scanner_family"]
        assert isinstance(sf, ast.Constant) and sf.value == "prediction_market", (
            f"Opportunity() at line {call.lineno}: scanner_family={ast.unparse(sf)!r}, "
            "expected 'prediction_market'"
        )

        assert "valid_until" in kwargs, (
            f"Opportunity() at line {call.lineno} is missing valid_until kwarg"
        )

    # confidence_score for the LLM-derived top market must use a 0 fallback
    src = _PRED_OBS.read_text(encoding="utf-8")
    assert "confidence_score is not None else 0" in src, (
        "prediction_observer must use 'confidence_score if confidence_score is not None else 0' "
        "for the LLM-derived top market to prevent NULL confidence_score"
    )


@spec_claim(
    SpecClaim(
        "OBS-NEWS-ANALYZER", "agent_contracts", "§scheduler news analysis", "L2a",
        "News analysis is a separate small-batch timeout job and RSS fetch never calls it.",
        status="implemented",
    ),
)
def test_news_analysis_is_separate_from_rss_scheduler_job():
    import inspect

    from aletheia.agent_runtime import scheduler

    assert scheduler._ASYNC_JOB_TIMEOUT_SEC["news_analyze"] <= 120
    assert "analyze_pending_news(batch_size=3)" in inspect.getsource(
        scheduler._async_news_analyze
    )
    assert "analyze_pending_news" not in inspect.getsource(scheduler._async_news_fetch)
