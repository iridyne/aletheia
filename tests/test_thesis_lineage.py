from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner


def test_thesis_lineage_read_model_does_not_call_llm_or_agents():
    src_path = Path(__file__).resolve().parents[1] / "aletheia" / "analytics" / "thesis_lineage.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden_import_roots = {
        "agents",
        "agent_shell",
        "aletheia.agents",
        "aletheia.agent_shell",
        "aletheia.model_provider",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "aiohttp",
    }
    forbidden_call_names = {
        "call_llm",
        "chat",
        "complete",
        "completion",
        "completions",
        "generate",
        "get_default_provider",
        "get_model_provider",
        "get_provider_for_tier",
        "llm_complete",
        "llm_stream",
        "llm_structured",
    }

    def forbidden_root(name: str) -> str | None:
        for root in forbidden_import_roots:
            if name == root or name.startswith(f"{root}."):
                return root
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = forbidden_root(alias.name)
                assert root is None, f"Thesis Lineage read model must not import {root}"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = forbidden_root(module)
            assert root is None, f"Thesis Lineage read model must not import from {root}"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in forbidden_call_names, (
                "Thesis Lineage v1 must consume upstream thesis artifacts only; "
                f"found forbidden call {name}()"
            )


def _iso(hours_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _insert_opp(db, idx: int, *, direction: str = "long", family: str = "momentum") -> str:
    oid = f"opp-lineage-{idx}"
    db.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, market_type, rank, total_score, opportunity_score, "
        "tradability_score, late_entry_penalty, scanner_family, regime_tag, risk_flags, expires_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'usdt_futures', ?, 71, 66, 80, 4, ?, 'bull', '[\"late_entry\"]', ?)",
        (f"um-lineage-{idx}", f"snap-lineage-{idx}", f"asset-lineage-{idx}", idx, family, _iso(-1)),
    )
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, thesis, confidence, confidence_score, source_agent, "
        "created_at, status, universe_snapshot_id, universe_member_id, scanner_family, key_observations, "
        "funding_rate, open_interest, oi_delta_1h) "
        "VALUES (?, 'BTC/USDT', 'usdt_futures', ?, ?, 'high', 78, 'market_observer', ?, 'open', "
        "?, ?, ?, ?, 0.0001, 1000, 1.5)",
        (
            oid,
            direction,
            f"Breakout thesis {idx}",
            _iso(),
            f"snap-lineage-{idx}",
            f"um-lineage-{idx}",
            family,
            json.dumps(["volume expansion", "trend aligned"]),
        ),
    )
    return oid


def _insert_decision(
    db,
    idx: int,
    *,
    action: str = "trade",
    proposal_id: str | None = None,
    source: str = "market_observer",
    opp_id: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, proposal_id, opportunity_id, signal_source, "
        "bear_case, key_assumptions, created_at) "
        "VALUES (?, 'BTC/USDT', 'long', ?, 74, 'decision reason', ?, ?, ?, 'bear case', '[\"assumption\"]', ?)",
        (f"pi-lineage-{idx}", action, proposal_id, opp_id or f"opp-lineage-{idx}", source, _iso()),
    )


def _insert_proposal(db, idx: int, *, rationale: str = "proposal thesis") -> str:
    pid = f"prop-lineage-{idx}"
    db.execute(
        "INSERT INTO trade_proposals "
        "(id, opportunity_id, symbol, market_type, direction, order_type, quantity, price, leverage, stop_price, "
        "take_profit, risk_reward, execution_mode, agent_rationale, status, trade_school, commit_hash, parent_hash, created_at) "
        "VALUES (?, ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', 0.2, 100, 2, 95, 112, 2.4, "
        "'bitget_demo', ?, 'executed', 'short_term', 'hash1', 'hash0', ?)",
        (pid, f"opp-lineage-{idx}", rationale, _iso()),
    )
    return pid


def _insert_risk(db, idx: int, *, approved: bool = True) -> None:
    db.execute(
        "INSERT INTO risk_decisions "
        "(id, proposal_id, approved, risk_score, violations, message, created_at) "
        "VALUES (?, ?, ?, 0.2, ?, ?, ?)",
        (
            f"risk-lineage-{idx}",
            f"prop-lineage-{idx}",
            1 if approved else 0,
            "[]" if approved else '["max_loss"]',
            "ok" if approved else "max loss",
            _iso(),
        ),
    )


def _insert_order_fill_position(db, idx: int, *, pnl: float, status: str = "closed") -> None:
    db.execute(
        "INSERT INTO orders "
        "(id, proposal_id, risk_decision_id, symbol, market_type, direction, order_type, quantity, price, "
        "execution_mode, status, created_at, filled_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'usdt_futures', 'long', 'limit', 0.2, 100, 'bitget_demo', 'filled', ?, ?)",
        (f"order-lineage-{idx}", f"prop-lineage-{idx}", f"risk-lineage-{idx}", _iso(), _iso()),
    )
    db.execute(
        "INSERT INTO fills (id, order_id, fill_price, fill_quantity, fee, filled_at) "
        "VALUES (?, ?, 100, 0.2, 0.01, ?)",
        (f"fill-lineage-{idx}", f"order-lineage-{idx}", _iso()),
    )
    db.execute(
        "INSERT INTO positions "
        "(id, symbol, market_type, direction, quantity, entry_price, current_price, unrealized_pnl, realized_pnl, "
        "execution_mode, strategy_id, proposal_id, order_id, opportunity_id, status, close_reason, opened_at, closed_at) "
        "VALUES (?, 'BTC/USDT', 'usdt_futures', 'long', 0.2, 100, 105, 0, ?, 'bitget_demo', NULL, ?, ?, ?, ?, "
        "'take_profit', ?, ?)",
        (
            f"pos-lineage-{idx}",
            pnl,
            f"prop-lineage-{idx}",
            f"order-lineage-{idx}",
            f"opp-lineage-{idx}",
            status,
            _iso(2),
            _iso(),
        ),
    )


def _insert_shadow(db, idx: int, *, pnl_pct: float, batch: str = "batch-lineage", rank: int = 1) -> None:
    db.execute(
        "INSERT INTO shadow_trades "
        "(id, batch_id, opportunity_id, symbol, direction, scanner_family, trade_score, rank, entry_price, "
        "was_selected, created_at, exit_price, pnl_pct, resolved_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'long', 'momentum', 80, ?, 100, 0, ?, 101, ?, ?)",
        (f"shadow-lineage-{idx}", batch, f"opp-lineage-{idx}", rank, _iso(), pnl_pct, _iso()),
    )


def test_reconstruct_market_observer_thesis_from_opportunity_only():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_records
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 1)
    db.commit()

    record = build_thesis_lineage_records(days=7)[0]

    assert record["lineage_id"] == "market_observer:opp-lineage-1"
    assert record["observation_thesis"] == "Breakout thesis 1"
    assert record["universe_rank"] == 1
    assert record["tradability_score"] == 80.0
    assert record["verdict"] == "pending"


def test_reconstruct_traded_thesis_through_proposal_and_position():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_records
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 2)
    pid = _insert_proposal(db, 2)
    _insert_decision(db, 2, proposal_id=pid)
    _insert_risk(db, 2)
    _insert_order_fill_position(db, 2, pnl=2.0)
    db.commit()

    record = build_thesis_lineage_records(days=7)[0]

    assert record["proposal_id"] == "prop-lineage-2"
    assert record["order_id"] == "order-lineage-2"
    assert record["position_id"] == "pos-lineage-2"
    assert record["was_traded"] is True
    assert record["actual_pnl_pct"] == 10.0
    assert record["verdict"] == "validated"


def test_parse_decision_meta_from_agent_rationale():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_records
    from aletheia.storage.sqlite import get_db

    meta = {
        "supporting_signals": ["volume"],
        "opposing_signals": ["funding"],
        "bear_case": "breakout fails",
        "key_assumptions": ["liquidity holds"],
    }
    rationale = "Human thesis text\n---\n" + json.dumps(meta)
    db = get_db()
    _insert_opp(db, 3)
    pid = _insert_proposal(db, 3, rationale=rationale)
    _insert_decision(db, 3, proposal_id=pid)
    db.commit()

    record = build_thesis_lineage_records(days=7)[0]

    assert record["human_thesis"] == "Human thesis text"
    assert record["supporting_signals"] == ["volume"]
    assert record["opposing_signals"] == ["funding"]
    assert record["bear_case"] == "bear case"
    assert record["key_assumptions"] == ["assumption"]


def test_detect_missed_alpha_from_shadow_without_proposal():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 4)
    _insert_decision(db, 4, action="skip")
    _insert_shadow(db, 4, pnl_pct=1.2)
    db.commit()

    report = build_thesis_lineage_audit(days=7)

    assert report["summary"]["missed_alpha_count"] == 1
    assert report["top_missed_alpha"][0]["verdict"] == "missed_alpha"


def test_detect_poor_expression_against_shadow_batch_average():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 5)
    _insert_opp(db, 50)
    pid = _insert_proposal(db, 5)
    _insert_decision(db, 5, proposal_id=pid)
    _insert_risk(db, 5)
    _insert_order_fill_position(db, 5, pnl=0.1)  # 0.5%
    _insert_shadow(db, 5, pnl_pct=2.0, batch="batch-poor")
    _insert_shadow(db, 50, pnl_pct=3.0, batch="batch-poor")
    db.commit()

    report = build_thesis_lineage_audit(days=7)
    records = {r["lineage_id"]: r for r in report["records"]}

    assert records["market_observer:opp-lineage-5"]["counterfactual_avg_pnl_pct"] == 2.5
    assert records["market_observer:opp-lineage-5"]["verdict"] == "poor_expression"
    assert report["summary"]["poor_expression_count"] == 1


def test_audit_reports_v1_1_data_completeness_gaps():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 70)
    _insert_opp(db, 71)
    _insert_decision(db, 71, action="skip")
    _insert_opp(db, 72)
    _insert_proposal(db, 72, rationale="proposal thesis without structured meta")
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, created_at) "
        "VALUES ('pi-lineage-missing-source', 'ETH/USDT', 'long', 'skip', 10, 'missing source row', ?)",
        (_iso(),),
    )
    db.commit()

    report = build_thesis_lineage_audit(days=7)
    completeness = report["data_completeness"]
    missing = completeness["missing_counts"]
    raw = report["raw_table_completeness"]

    assert completeness["total_records"] == 3
    assert completeness["chain_counts"]["with_decision"] == 1
    assert completeness["chain_counts"]["with_proposal"] == 1
    assert missing["missing_decision_action"] == 2
    assert missing["missing_decision_reasoning"] == 2
    assert missing["missing_decision_meta_on_proposed_records"] == 1
    assert missing["non_traded_missing_shadow_outcome"] == 2
    assert missing["non_traded_missing_forward_return"] == 2
    assert missing["traded_missing_position_id"] == 1
    assert missing["traded_missing_actual_pnl_pct"] == 1
    assert missing["parse_warning_count"] == 0
    assert raw["pi_decisions"]["total"] == 2
    assert raw["pi_decisions"]["missing_opportunity_id"] == 1
    assert raw["pi_decisions"]["missing_signal_source"] == 1
    assert raw["trade_proposals"]["missing_decision_meta_in_agent_rationale"] == 1


def test_resolve_serenity_thesis_via_signal_source():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_records
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO serenity_themes (id, name, created_at, updated_at) VALUES ('theme-lineage-1', 'Theme', ?, ?)",
        (_iso(), _iso()),
    )
    db.execute(
        "INSERT INTO serenity_opportunities "
        "(id, theme_id, symbol, direction, thesis, confidence_score, action_intent, stage, status, "
        "verdict_json, assessed_tier, researchability_grade, created_at, updated_at, market_type) "
        "VALUES ('ser-lineage-1', 'theme-lineage-1', 'ETH/USDT', 'long', 'Serenity thesis', 68, "
        "'watch', 'research', 'active', '{\"recommendation\":\"watch\"}', 'strong', 'A', ?, ?, 'spot')",
        (_iso(), _iso()),
    )
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, opportunity_id, signal_source, created_at) "
        "VALUES ('pi-ser-lineage-1', 'ETH/USDT', 'long', 'watch', 50, 'serenity decision', 'ser-lineage-1', 'serenity', ?)",
        (_iso(),),
    )
    db.commit()

    record = build_thesis_lineage_records(days=7)[0]

    assert record["root_type"] == "serenity"
    assert record["root_id"] == "ser-lineage-1"
    assert record["decision_action"] == "watch"
    assert record["serenity"]["researchability_grade"] == "A"


def test_cli_human_and_json_outputs_are_stable():
    from aletheia.main import app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 6)
    _insert_shadow(db, 6, pnl_pct=0.8)
    db.commit()
    runner = CliRunner()

    human = runner.invoke(app, ["thesis", "audit", "--days", "7"])
    machine = runner.invoke(app, ["thesis", "audit", "--days", "7", "--json"])
    records = runner.invoke(app, ["thesis", "records", "--days", "7", "--json"])

    assert human.exit_code == 0
    assert "Thesis Lineage Audit" in human.output
    assert "missed_alpha_count" in human.output
    assert "Data completeness:" in human.output
    assert machine.exit_code == 0
    assert json.loads(machine.output)["summary"]["total_thesis_count"] == 1
    assert "data_completeness" in json.loads(machine.output)
    assert records.exit_code == 0
    assert json.loads(records.output)[0]["lineage_id"] == "market_observer:opp-lineage-6"


def test_analytics_route_filters_thesis_lineage_by_position_id():
    from aletheia.server.routes.analytics import get_thesis_lineage
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 60)
    pid = _insert_proposal(db, 60)
    _insert_decision(db, 60, proposal_id=pid)
    _insert_risk(db, 60)
    _insert_order_fill_position(db, 60, pnl=1.0)
    db.commit()

    data = get_thesis_lineage(days=7, position_id="pos-lineage-60")

    assert len(data["records"]) == 1
    assert data["records"][0]["position_id"] == "pos-lineage-60"
    assert data["records"][0]["lineage_id"] == "market_observer:opp-lineage-60"


def test_missing_optional_tables_and_malformed_json_do_not_crash():
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opp(db, 7)
    _insert_proposal(db, 7, rationale="Raw thesis\n---\n{broken")
    db.execute("DROP TABLE shadow_trades")
    db.commit()

    report = build_thesis_lineage_audit(days=7)
    record = report["records"][0]

    assert report["summary"]["total_thesis_count"] == 1
    assert report["schema_errors"]
    assert record["parse_warning"] == "decision_meta_json_invalid"
