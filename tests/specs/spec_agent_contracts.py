"""Spec: Agent Contracts (agent_contracts.md)

Scheduler job structure, agent IO shapes, max_workers cap.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AC-S01", "agent_contracts", "§1 scheduler cap", "L2a",
        "ThreadPoolExecutor max_workers is capped at <= 3",
    ),
)
def test_scheduler_max_workers_cap():
    import re
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agent_runtime" / "scheduler.py"
    source = src.read_text(encoding="utf-8")
    matches = re.findall(r"max_workers\s*=\s*(\d+)", source)
    assert matches, "No max_workers found in scheduler.py"
    for val in matches:
        assert int(val) <= 3, (
            f"Scheduler max_workers={val} exceeds cap of 3"
        )


@spec_claim(
    SpecClaim(
        "AC-S02", "agent_contracts", "§2 PI agent", "L2a",
        "PI Agent (trading_decision) exists with evaluate_trading_opportunity entry point",
    ),
)
def test_pi_agent_entry_point():
    from aletheia.agent_shell import trading_decision

    assert callable(trading_decision.evaluate_trading_opportunity)


@spec_claim(
    SpecClaim(
        "AC-S03", "agent_contracts", "§3 paper_trader", "L2a",
        "paper_trader module exists and does not import LLM helpers",
    ),
)
def test_paper_trader_no_llm():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "agents" / "paper_trader.py"
    if not src.exists():
        pytest.skip("paper_trader.py not found")
    imports = set()
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    llm_imports = [i for i in imports if "llm" in i.lower() or "openai" in i.lower()]
    assert llm_imports == [], (
        f"paper_trader must not call LLM directly: {llm_imports}"
    )


@spec_claim(
    SpecClaim(
        "AC-S04", "agent_contracts", "§4 knowledge manager", "L2a",
        "Knowledge manager exposes CRUD functions for all item types",
    ),
)
def test_knowledge_manager_crud():
    from aletheia.knowledge import manager

    required = [
        "create_research_note",
        "create_trading_guard",
        "create_guard_candidate",
        "archive_item",
    ]
    for fn_name in required:
        assert hasattr(manager, fn_name) and callable(getattr(manager, fn_name)), \
            f"knowledge/manager.py missing {fn_name}"


@spec_claim(
    SpecClaim(
        "AC-S05", "agent_contracts", "§5 guard evaluation", "L2a",
        "Guard evaluation pipeline exists (load -> evaluate -> format)",
    ),
)
def test_guard_pipeline_functions():
    from aletheia.knowledge import guards

    assert callable(guards.load_trading_guards)
    assert callable(guards.evaluate_guards)
    assert callable(guards.format_guard_block)
