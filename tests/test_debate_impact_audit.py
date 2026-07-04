from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from typer.testing import CliRunner


def _insert_opportunity(
    db: sqlite3.Connection,
    *,
    opp_id: str = "opp-debate-1",
    member_id: str = "member-debate-1",
    symbol: str = "TRX/USDT",
    direction: str = "short",
    family: str = "mean_reversion",
) -> None:
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, thesis, confidence, confidence_score, source_agent, "
        "created_at, status, linked_strategy_id, asset_class, universe_member_id, scanner_family, execution_eligible) "
        "VALUES (?, ?, 'usdt_futures', ?, 'test thesis', 'high', 62, 'market_observer', "
        "datetime('now','-2 hours'), 'open', NULL, 'crypto', ?, ?, 1)",
        (opp_id, symbol, direction, member_id, family),
    )


def _insert_decision(
    db: sqlite3.Connection,
    *,
    idx: int = 1,
    opp_id: str = "opp-debate-1",
    action: str = "skip",
    conviction: int = 45,
    debate_ran: bool = True,
    reasoning: str = "TRX/USDT short setup debated and skipped",
) -> None:
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, symbol, direction, action, conviction, reasoning, opportunity_id, debate_ran, created_at) "
        "VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, datetime('now','-1 hour'))",
        (f"pi-debate-{idx}", action, conviction, reasoning, opp_id, 1 if debate_ran else 0),
    )


def _insert_forward(
    db: sqlite3.Connection,
    *,
    member_id: str = "member-debate-1",
    symbol: str = "TRX/USDT",
    horizon: str = "1h",
    ret: float = -0.3,
    direction: str = "short",
) -> None:
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, status) "
        "VALUES (?, ?, 'snap-debate', ?, 'mean_reversion', ?, ?, 100, datetime('now','-2 hours'), "
        "datetime('now','-1 hour'), datetime('now'), 99, ?, 'settled')",
        (f"fwd-{member_id}-{horizon}", member_id, symbol, direction, horizon, ret),
    )


def test_debate_impact_read_model_has_no_llm_http_exchange_or_ranker_calls():
    src_path = Path(__file__).resolve().parents[1] / "aletheia" / "analytics" / "debate_impact.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden_import_roots = {
        "aletheia.agents",
        "aletheia.agent_shell",
        "aletheia.exchanges",
        "aletheia.execution",
        "aletheia.model_provider",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "aiohttp",
    }
    forbidden_call_names = {
        "build_paper_candidates",
        "call_llm",
        "chat",
        "complete",
        "completion",
        "completions",
        "generate",
        "get_model_provider",
        "llm_structured",
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


def test_debate_impact_empty_db_does_not_crash(monkeypatch):
    import aletheia.storage.sqlite as sq
    from aletheia.analytics.debate_impact import build_debate_impact_audit

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn

    report = build_debate_impact_audit(hours=24)

    assert report["decision_flow"]["total_decisions"] == 0
    assert report["debated_opportunities"] == []
    assert report["verdict"] == "insufficient_data"


def test_debate_impact_flags_positive_direction_outcome_as_missed_alpha_suspect():
    from aletheia.analytics.debate_impact import build_debate_impact_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opportunity(db)
    _insert_decision(db, idx=1, conviction=45)
    _insert_decision(db, idx=2, conviction=49)
    _insert_forward(db, horizon="1h", ret=-0.3)

    before = db.total_changes
    report = build_debate_impact_audit(hours=24)
    after = db.total_changes

    assert after == before
    assert report["decision_flow"]["debated_skips"] == 2
    assert report["decision_flow"]["trade_like_decisions"] == 0
    assert len(report["debated_opportunities"]) == 1
    row = report["debated_opportunities"][0]
    assert row["decision_count"] == 2
    assert row["avg_final_conviction"] == 47.0
    assert row["outcomes"]["1h"]["direction_adjusted_return_pct"] == 0.3
    assert row["verdict"] == "missed_alpha_suspect"
    assert report["verdict"] == "missed_alpha_suspect"


def test_debate_impact_counts_unsupported_mentions_from_persisted_reasoning_only():
    from aletheia.analytics.debate_impact import build_debate_impact_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_decision(
        db,
        idx=1,
        opp_id=None,
        debate_ran=False,
        conviction=0,
        reasoning="All candidates blocked by okx_demo_symbol_not_supported.",
    )

    report = build_debate_impact_audit(hours=24)

    assert report["decision_flow"]["unsupported_mentions"] == 1
    assert report["debated_opportunities"] == []


def test_debate_impact_cli_outputs_human_and_json():
    from aletheia.cli.inspect_cmd import inspect_app
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_opportunity(db)
    _insert_decision(db)

    runner = CliRunner()
    human = runner.invoke(inspect_app, ["debate-impact", "--hours", "24"])
    raw = runner.invoke(inspect_app, ["debate-impact", "--hours", "24", "--json"])

    assert human.exit_code == 0
    assert "Debate Impact Audit" in human.output
    assert "debated_skips: 1" in human.output
    assert raw.exit_code == 0
    assert '"debated_skips": 1' in raw.output
