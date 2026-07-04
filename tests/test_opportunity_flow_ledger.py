from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner


def _insert_opp(
    db: sqlite3.Connection,
    *,
    opp_id: str = "opp-flow-1",
    member_id: str = "member-flow-1",
    symbol: str = "BTC/USDT",
    direction: str = "long",
    confidence_score: int = 66,
) -> None:
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, thesis, confidence, confidence_score, source_agent, "
        "created_at, status, asset_class, universe_member_id, scanner_family, execution_eligible) "
        "VALUES (?, ?, 'usdt_futures', ?, 'persisted thesis', 'high', ?, 'market_observer', "
        "datetime('now','-2 hours'), 'open', 'crypto', ?, 'momentum', 1)",
        (opp_id, symbol, direction, confidence_score, member_id),
    )


def _insert_decision(
    db: sqlite3.Connection,
    *,
    idx: int = 1,
    opp_id: str = "opp-flow-1",
    action: str = "skip",
    conviction: int = 48,
    reasoning: str = "debate lowered conviction below threshold",
    debate_ran: bool = True,
    debate_initial: int | None = 60,
    debate_bull: int | None = 40,
    debate_bear: int | None = 80,
    debate_adjustment: int | None = -12,
) -> None:
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, opportunity_id, debate_ran, "
        "debate_initial_conviction, debate_bull_confidence, debate_bear_confidence, "
        "debate_adjustment, created_at) "
        "VALUES (?, 'BTC/USDT', 'long', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','-1 hour'))",
        (
            f"pi-flow-{idx}",
            action,
            conviction,
            reasoning,
            opp_id,
            1 if debate_ran else 0,
            debate_initial,
            debate_bull,
            debate_bear,
            debate_adjustment,
        ),
    )


def _insert_forward(
    db: sqlite3.Connection,
    *,
    member_id: str = "member-flow-1",
    horizon: str = "1h",
    ret: float = 0.8,
    direction: str = "long",
) -> None:
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, status) "
        "VALUES (?, ?, 'snap-flow', 'BTC/USDT', 'momentum', ?, ?, 100, datetime('now','-2 hours'), "
        "datetime('now','-1 hour'), datetime('now'), 100.8, ?, 'settled')",
        (f"fwd-flow-{member_id}-{horizon}", member_id, direction, horizon, ret),
    )


def _insert_proposal_chain(db: sqlite3.Connection, *, opp_id: str = "opp-flow-1") -> None:
    db.execute(
        "INSERT INTO trade_proposals "
        "(id, opportunity_id, symbol, market_type, direction, order_type, quantity, price, "
        "execution_mode, status, created_at) "
        "VALUES ('prop-flow-1', ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', 0.2, 100, "
        "'bitget_demo', 'executed', datetime('now','-50 minutes'))",
        (opp_id,),
    )
    db.execute(
        "INSERT INTO risk_decisions "
        "(id, proposal_id, approved, risk_score, violations, message, created_at) "
        "VALUES ('risk-flow-1', 'prop-flow-1', 1, 0.1, '[]', 'approved', datetime('now','-45 minutes'))"
    )
    db.execute(
        "INSERT INTO orders "
        "(id, proposal_id, risk_decision_id, symbol, market_type, direction, order_type, quantity, "
        "price, execution_mode, status, created_at, filled_at) "
        "VALUES ('order-flow-1', 'prop-flow-1', 'risk-flow-1', 'BTC/USDT', 'usdt_futures', "
        "'long', 'limit', 0.2, 100, 'bitget_demo', 'filled', datetime('now','-40 minutes'), "
        "datetime('now','-39 minutes'))"
    )
    db.execute(
        "INSERT INTO positions "
        "(id, symbol, market_type, direction, quantity, entry_price, execution_mode, proposal_id, "
        "order_id, status, leverage, opened_at, closed_at, close_reason, realized_pnl) "
        "VALUES ('pos-flow-1', 'BTC/USDT', 'usdt_futures', 'long', 0.2, 100, 'bitget_demo', "
        "'prop-flow-1', 'order-flow-1', 'closed', 1, datetime('now','-38 minutes'), "
        "datetime('now','-20 minutes'), 'take_profit', 2.5)"
    )


def test_opportunity_flow_read_model_has_no_llm_ranker_guard_or_execution_calls():
    src_path = Path(__file__).resolve().parents[1] / "aletheia" / "analytics" / "opportunity_flow.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden_import_roots = {
        "aletheia.agents",
        "aletheia.agent_shell",
        "aletheia.exchanges",
        "aletheia.execution",
        "aletheia.knowledge.guards",
        "aletheia.model_provider",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "aiohttp",
    }
    forbidden_call_names = {
        "build_guard_impact_audit",
        "build_paper_candidates",
        "call_llm",
        "chat",
        "complete",
        "completion",
        "completions",
        "evaluate_guards",
        "generate",
        "get_model_provider",
        "llm_structured",
        "place_order",
        "refresh_okx_demo_symbols",
    }

    def forbidden_root(name: str) -> str | None:
        for root in forbidden_import_roots:
            if name == root or name.startswith(f"{root}."):
                return root
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert forbidden_root(alias.name) is None
        elif isinstance(node, ast.ImportFrom):
            assert forbidden_root(node.module or "") is None
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in forbidden_call_names


def test_opportunity_flow_empty_db_does_not_crash(monkeypatch):
    import aletheia.storage.sqlite as sq
    from aletheia.analytics.opportunity_flow import build_opportunity_flow_ledger

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn

    report = build_opportunity_flow_ledger(hours=24)

    assert report["summary"]["total_opportunities"] == 0
    assert report["opportunities"] == []
    assert report["read_only"] is True


def test_opportunity_flow_debate_skip_with_positive_forward_outcome_is_missed_alpha():
    from aletheia.analytics.opportunity_flow import (
        build_opportunity_flow_ledger,
        render_flow_audit,
        trace_opportunity_flow,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(db)
    _insert_forward(db, ret=0.8)

    before = db.total_changes
    report = build_opportunity_flow_ledger(hours=24)
    trace = trace_opportunity_flow("opp-flow-1")
    after = db.total_changes

    assert after == before
    row = report["opportunities"][0]
    assert row["final_state"] == "dropped"
    assert row["final_stage"] == "debate_downgraded"
    assert row["counterfactual"]["verdict"] == "missed_alpha_suspect"
    assert row["counterfactual"]["forward"]["1h"]["direction_adjusted_return_pct"] == 0.8
    debate = report["summary"]["debate"]
    assert debate["decision_flow"]["debated_decisions"] == 1
    assert debate["decision_flow"]["debated_skips"] == 1
    assert debate["trace_flow"]["trace_complete_decisions"] == 1
    assert debate["trace_flow"]["trace_missing_decisions"] == 0
    assert debate["next_needed"] == "inspect_debate_adjustments_before_tuning"
    rendered = render_flow_audit(report)
    assert "Debate signal:" in rendered
    assert "trace_complete: 1" in rendered
    assert trace["found"] is True
    assert [event["stage"] for event in trace["timeline"]] == [
        "created",
        "operator_skip",
        "debate_downgraded",
        "forward_outcome_resolved",
    ]


def test_opportunity_flow_missing_legacy_debate_fields_stay_insufficient_data():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(
        db,
        debate_initial=None,
        debate_bull=None,
        debate_bear=None,
        debate_adjustment=None,
    )

    trace = trace_opportunity_flow("opp-flow-1")
    debate = [event for event in trace["timeline"] if event["stage"] == "debate_downgraded"][0]

    assert debate["details"]["trace_status"] == "insufficient_data"
    assert "Legacy debate row has no trace fields." in debate["reason"]


def test_opportunity_flow_audit_filters_by_stage_verdict_and_symbol():
    from aletheia.analytics.opportunity_flow import build_opportunity_flow_ledger, render_flow_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, opp_id="opp-flow-debate", member_id="member-flow-debate", symbol="BTC/USDT")
    _insert_decision(db, opp_id="opp-flow-debate")
    _insert_forward(db, member_id="member-flow-debate", ret=0.8)
    _insert_opp(
        db,
        opp_id="opp-flow-filtered",
        member_id="member-flow-filtered",
        symbol="ETH/USDT",
        confidence_score=20,
    )

    by_stage = build_opportunity_flow_ledger(hours=24, stage="debate_downgraded")
    by_verdict = build_opportunity_flow_ledger(hours=24, verdict="missed_alpha_suspect")
    by_symbol = build_opportunity_flow_ledger(hours=24, symbol="ETH/USDT")

    assert [row["opportunity_id"] for row in by_stage["opportunities"]] == ["opp-flow-debate"]
    assert [row["opportunity_id"] for row in by_verdict["opportunities"]] == ["opp-flow-debate"]
    assert [row["opportunity_id"] for row in by_symbol["opportunities"]] == ["opp-flow-filtered"]
    assert by_symbol["filters"]["symbol"] == "ETH/USDT"
    assert by_symbol["summary"]["source_opportunities"] == 1
    rendered = render_flow_audit(by_stage)
    assert "Filters:" in rendered
    assert "stage: debate_downgraded" in rendered


def test_opportunity_flow_success_path_reaches_closed_position():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(db, action="trade", conviction=72, reasoning="trade intent", debate_ran=False)
    _insert_proposal_chain(db)

    trace = trace_opportunity_flow("opp-flow-1")

    assert trace["final_state"] == "closed"
    assert trace["final_stage"] == "position_closed"
    stages = [event["stage"] for event in trace["timeline"]]
    assert "proposal_created" in stages
    assert "risk_approved" in stages
    assert "order_executed" in stages
    assert "position_opened" in stages
    assert "position_closed" in stages


def test_opportunity_flow_cli_outputs_audit_and_trace():
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(db)

    runner = CliRunner()
    audit = runner.invoke(
        app,
        [
            "flow",
            "audit",
            "--hours",
            "24",
            "--limit",
            "5",
            "--stage",
            "debate_downgraded",
            "--verdict",
            "insufficient_data",
            "--symbol",
            "BTC/USDT",
        ],
    )
    trace = runner.invoke(app, ["flow", "trace", "--opportunity-id", "opp-flow-1", "--json"])

    assert audit.exit_code == 0
    assert "Opportunity Flow Ledger" in audit.output
    assert "debate_downgraded" in audit.output
    assert "Filters:" in audit.output
    assert trace.exit_code == 0
    payload = json.loads(trace.output)
    assert payload["opportunity_id"] == "opp-flow-1"
    assert payload["final_stage"] == "debate_downgraded"
