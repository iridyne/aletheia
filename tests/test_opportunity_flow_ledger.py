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
    snapshot_id: str = "snap-flow",
) -> None:
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, thesis, confidence, confidence_score, source_agent, "
        "created_at, status, asset_class, universe_member_id, universe_snapshot_id, scanner_family, execution_eligible) "
        "VALUES (?, ?, 'usdt_futures', ?, 'persisted thesis', 'high', ?, 'market_observer', "
        "datetime('now','-2 hours'), 'open', 'crypto', ?, ?, 'momentum', 1)",
        (opp_id, symbol, direction, confidence_score, member_id, snapshot_id),
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
    snapshot_id: str = "snap-flow",
) -> None:
    directional = -ret if direction == "short" else ret
    net = directional - 0.2
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, gross_return_pct, "
        "directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, "
        "estimated_funding_pct, net_return_pct, min_tradable_edge_pct, cost_model_version, status) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'momentum', ?, ?, 100, datetime('now','-2 hours'), "
        "datetime('now','-1 hour'), datetime('now'), 100.8, ?, ?, ?, 0.1, 0.1, 0, ?, 0.05, 'v1', 'settled')",
        (
            f"fwd-flow-{member_id}-{snapshot_id}-{horizon}", member_id,
            snapshot_id, direction, horizon, ret, ret, directional, net,
        ),
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
        if name == "aletheia.agent_shell.operator_store":
            return None
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


def test_opportunity_flow_pending_shadow_is_not_reported_as_resolved():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    db.execute(
        "INSERT INTO shadow_trades "
        "(id, opportunity_id, symbol, direction, scanner_family, trade_score, rank, entry_price) "
        "VALUES ('shadow-pending', 'opp-flow-1', 'BTC/USDT', 'long', 'momentum', 50, 1, 100)"
    )
    db.commit()

    trace = trace_opportunity_flow("opp-flow-1")

    assert trace["counterfactual"]["shadow"]["status"] == "pending"
    assert not any(event["stage"] == "shadow_resolved" for event in trace["timeline"])


def test_opportunity_flow_candidate_supply_uses_wide_event_window(monkeypatch):
    # Read-model purity (test_opportunity_flow_read_model_*): opportunity_flow must
    # NOT import aletheia.agent_shell, so it queries the agent_shell_events table
    # directly rather than calling operator_store.list_events. This test therefore
    # seeds the real table and asserts on the audit OUTPUT (same intent as the
    # master-side version that mocked list_events with limit=100).
    import aletheia.storage.sqlite as sq
    import aletheia.analytics.opportunity_flow as flow

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from aletheia.storage.migrations import run_migrations
    sq._db_conn = conn
    run_migrations()

    for eid, created_at, ec, count in [
        ("supply-1", "2026-07-04T12:01:00+00:00", 2, 5),
        ("supply-2", "2026-07-04T12:02:00+00:00", 1, 7),
    ]:
        conn.execute(
            "INSERT INTO agent_shell_events (id, event_type, payload, created_at) "
            "VALUES (?, 'candidate_supply_snapshot', ?, ?)",
            (
                eid,
                json.dumps({
                    "executable_count": ec,
                    "main_reason": "ttl_expired",
                    "excluded_candidates_summary": [{"reason": "ttl_expired", "count": count}],
                }),
                created_at,
            ),
        )
    conn.commit()

    monkeypatch.setattr(flow, "build_trade_funnel_audit", lambda **_: {})
    monkeypatch.setattr(flow, "build_debate_impact_audit", lambda **_: {})
    monkeypatch.setattr(flow, "build_thesis_lineage_audit", lambda **_: {})
    monkeypatch.setattr(flow, "compact_thesis_lineage_summary", lambda report: report)
    monkeypatch.setattr(flow, "build_sizing_trace_audit", lambda **_: {})
    monkeypatch.setattr(flow, "compact_sizing_trace_summary", lambda report: report)

    audits = flow._integrated_audits(hours=24)

    assert audits["candidate_supply"]["latest_snapshot"]["main_reason"] == "ttl_expired"
    assert audits["candidate_supply"]["drought"]["main_reason"] == "ttl_expired"


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
    debate = [event for event in trace["timeline"] if event["stage"] == "debate_recorded"][0]

    assert trace["final_stage"] == "operator_skip"
    assert debate["details"]["trace_status"] == "insufficient_data"
    assert "Legacy debate row has no trace fields." in debate["reason"]


def test_opportunity_flow_only_marks_debate_downgrade_when_debate_crossed_trade_threshold():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, opp_id="opp-direct-skip")
    _insert_decision(
        db,
        opp_id="opp-direct-skip",
        action="skip",
        conviction=70,
        reasoning="market context is conflicted",
        debate_ran=True,
        debate_initial=72,
        debate_adjustment=-2,
    )
    db.commit()

    trace = trace_opportunity_flow("opp-direct-skip")

    assert trace["final_stage"] == "operator_skip"
    assert not any(event["stage"] == "debate_downgraded" for event in trace["timeline"])
    assert any(event["stage"] == "debate_recorded" for event in trace["timeline"])


def test_opportunity_flow_does_not_invent_ranker_rejection_without_ranker_evidence():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, opp_id="opp-not-executable", confidence_score=20)
    db.execute("UPDATE opportunities SET execution_eligible = 0 WHERE id = ?", ("opp-not-executable",))
    db.commit()

    trace = trace_opportunity_flow("opp-not-executable")

    assert trace["final_state"] == "pending"
    assert trace["final_stage"] == "created"
    assert not any(event["stage"] == "ranker_excluded" for event in trace["timeline"])
    inferred = [event for event in trace["timeline"] if event["stage"] == "eligibility_inferred"]
    assert inferred and inferred[0]["status"] == "inferred"


def test_opportunity_flow_does_not_mislabel_snapshot_supersede_as_ttl_expiry():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, opp_id="opp-snapshot-superseded")
    db.execute(
        "UPDATE opportunities SET status='expired',valid_until=datetime('now','+2 hours') "
        "WHERE id='opp-snapshot-superseded'"
    )
    db.execute(
        "INSERT INTO filter_decisions "
        "(id,dedup_key,candidate_key,candidate_kind,opportunity_id,symbol,market_type,"
        "direction,filter_name,stage,sequence_no,decision,reason_code,reason_detail,"
        "input_snapshot,parameter_snapshot,filter_version,evaluated_at) "
        "VALUES ('snapshot-terminal','snapshot-terminal-key',"
        "'opportunity:opp-snapshot-superseded','opportunity',"
        "'opp-snapshot-superseded','BTC/USDT','usdt_futures','long',"
        "'universe_snapshot_status','opportunity_lifecycle',0,'rejected',"
        "'universe_snapshot_superseded','parent snapshot replaced','{}','{}',"
        "'universe_snapshot_status:v1',datetime('now'))"
    )
    _insert_decision(
        db,
        idx=99,
        opp_id="opp-snapshot-superseded",
        action="signal_pending",
        reasoning="queued before the parent snapshot was superseded",
        debate_ran=False,
    )
    db.commit()

    trace = trace_opportunity_flow("opp-snapshot-superseded")

    assert trace["final_state"] == "superseded"
    assert trace["final_stage"] == "snapshot_superseded"
    assert "parent snapshot replaced" in trace["final_reason"]
    assert not any(event["stage"] == "ttl_expired" for event in trace["timeline"])


def test_opportunity_flow_counterfactual_uses_opportunity_snapshot_not_latest_member_result():
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(
        db,
        opp_id="opp-snapshot-a",
        member_id="member-reused",
        snapshot_id="snap-a",
    )
    _insert_opp(
        db,
        opp_id="opp-snapshot-b",
        member_id="member-reused",
        snapshot_id="snap-b",
    )
    _insert_forward(db, member_id="member-reused", snapshot_id="snap-a", ret=-1.0)
    _insert_forward(db, member_id="member-reused", snapshot_id="snap-b", ret=2.0)
    db.commit()

    trace = trace_opportunity_flow("opp-snapshot-a")

    assert trace["counterfactual"]["forward"]["1h"]["direction_adjusted_return_pct"] == -1.0


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


def test_opportunity_flow_distinguishes_window_total_from_reconstruction_limit():
    from aletheia.analytics.opportunity_flow import build_opportunity_flow_ledger, render_flow_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for idx in range(3):
        _insert_opp(
            db,
            opp_id=f"opp-window-{idx}",
            member_id=f"member-window-{idx}",
            symbol="BTC/USDT",
        )
    db.commit()

    report = build_opportunity_flow_ledger(hours=24, limit=2)
    summary = report["summary"]
    rendered = render_flow_audit(report)

    assert summary["window_opportunities"] == 3
    assert summary["reconstructed_opportunities"] == 2
    assert summary["displayed_opportunities"] == 2
    assert "Window opportunities: 3" in rendered
    assert "Reconstructed: latest 2 (limit=2)" in rendered


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


def test_opportunity_cli_outputs_audit_and_trace():
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(db)

    runner = CliRunner()
    audit = runner.invoke(
        app,
        [
            "opportunity",
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
    trace = runner.invoke(app, ["opportunity", "trace", "--id", "opp-flow-1", "--json"])

    assert audit.exit_code == 0
    assert "Opportunity Flow Ledger" in audit.output
    assert "debate_downgraded" in audit.output
    assert "Filters:" in audit.output
    assert trace.exit_code == 0
    payload = json.loads(trace.output)
    assert payload["opportunity_id"] == "opp-flow-1"
    assert payload["final_stage"] == "debate_downgraded"


def test_legacy_flow_cli_remains_hidden_compatibility_alias():
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(db)

    runner = CliRunner()
    audit = runner.invoke(app, ["flow", "audit", "--hours", "24", "--limit", "5"])
    trace = runner.invoke(app, ["flow", "trace", "--opportunity-id", "opp-flow-1", "--json"])

    assert audit.exit_code == 0
    assert "Deprecated: use `uv run aletheia opportunity audit`" in audit.output
    assert "Opportunity Flow Ledger" in audit.output
    assert trace.exit_code == 0
    payload = json.loads(trace.output)
    assert payload["opportunity_id"] == "opp-flow-1"


def test_opportunity_cli_outputs_specialized_views():
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db)
    _insert_decision(db)

    runner = CliRunner()
    expected_titles = {
        "funnel": "Trade Funnel Audit",
        "sizing": "Sizing Trace Audit",
        "alpha": "Alpha Attribution Audit",
        "thesis": "Thesis Lineage Audit",
    }

    for view, title in expected_titles.items():
        result = runner.invoke(app, ["opportunity", "audit", "--view", view, "--hours", "24"])
        assert result.exit_code == 0
        assert title in result.output
