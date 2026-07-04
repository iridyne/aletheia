from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from typer.testing import CliRunner


def _write_guard(tmp_path: Path, *, guard_id: str = "auto_momentum_long_test") -> None:
    guards = tmp_path / "knowledge" / "guards"
    guards.mkdir(parents=True, exist_ok=True)
    (guards / f"{guard_id}.md").write_text(
        """---
guard_id: auto_momentum_long_test
title: auto momentum long test
status: active
scope: paper_entry
enforcement: blocking
block_candidate: true
score_penalty: 10.0
scanner_family: momentum
direction: long
symbol: SEED/USDT
horizon: 4h
forward_return_pct: -4.2
predicates:
- field: scanner_family
  op: eq
  value: momentum
---

test guard
""",
        encoding="utf-8",
    )


def _insert_member(db: sqlite3.Connection, *, member_id: str = "member-1", symbol: str = "BTC/USDT") -> None:
    db.execute(
        "INSERT INTO universe_snapshots (id, created_at, status, expires_at) "
        "VALUES ('snap-guard-impact', datetime('now'), 'active', datetime('now','+1 hour'))"
    )
    db.execute(
        "INSERT INTO universe_registry (id, venue, market_type, symbol, status) "
        "VALUES (?, 'okx', 'usdt_futures', ?, 'active')",
        (f"asset-{member_id}", symbol),
    )
    db.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, rank, total_score, opportunity_score, tradability_score, "
        "liquidity_score, estimated_cost, crowding_risk, late_entry_penalty, scanner_family, expires_at, risk_flags) "
        "VALUES (?, 'snap-guard-impact', ?, ?, 1, 80, 70, 90, 90, 1, 0, 5, 'momentum', datetime('now','+1 hour'), '[]')",
        (member_id, f"asset-{member_id}", symbol),
    )


def _insert_opportunity(
    db: sqlite3.Connection,
    *,
    opp_id: str = "opp-guard-impact",
    member_id: str = "member-1",
    symbol: str = "BTC/USDT",
    family: str = "momentum",
    direction: str = "long",
    hours_ago: int = 1,
) -> None:
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, direction_bias, thesis, confidence, confidence_score, source_agent, "
        "created_at, status, linked_strategy_id, asset_class, universe_member_id, scanner_family, execution_eligible) "
        "VALUES (?, ?, 'usdt_futures', ?, 'test thesis', 'high', 72, 'market_observer', "
        "datetime('now', ?), 'open', NULL, 'crypto', ?, ?, 1)",
        (opp_id, symbol, direction, f"-{hours_ago} hours", member_id, family),
    )


def _insert_forward(
    db: sqlite3.Connection,
    *,
    member_id: str = "member-1",
    symbol: str = "BTC/USDT",
    horizon: str = "4h",
    ret: float = 2.0,
    direction: str = "long",
) -> None:
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, status) "
        "VALUES (?, ?, 'snap-guard-impact', ?, 'momentum', ?, ?, 100, datetime('now','-5 hours'), "
        "datetime('now','-1 hour'), datetime('now'), 102, ?, 'settled')",
        (f"fwd-{member_id}-{horizon}", member_id, symbol, direction, horizon, ret),
    )


def test_guard_impact_read_model_has_no_llm_http_or_exchange_calls():
    src_path = Path(__file__).resolve().parents[1] / "aletheia" / "analytics" / "guard_impact.py"
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


def test_guard_impact_empty_db_does_not_crash(monkeypatch):
    import aletheia.storage.sqlite as sq
    from aletheia.analytics.guard_impact import build_guard_impact_audit

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn

    report = build_guard_impact_audit(hours=24)

    assert report["active_blocking_guards"]["total"] == 0
    assert report["candidate_flow"]["recent_open_opportunities"] == 0
    assert report["blocked_candidates"] == []
    assert report["verdict"] == "insufficient_data"


def test_guard_impact_no_active_guards_returns_clean_report():
    from aletheia.analytics.guard_impact import build_guard_impact_audit

    report = build_guard_impact_audit(hours=24)

    assert report["active_blocking_guards"]["total"] == 0
    assert report["blocked_candidates"] == []
    assert report["verdict"] == "insufficient_data"


def test_guard_impact_flags_positive_blocked_outcome_as_overbroad_suspect(tmp_path):
    from aletheia.storage.sqlite import get_db
    from aletheia.analytics.guard_impact import build_guard_impact_audit

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db)
    _insert_opportunity(db)
    _insert_forward(db, ret=2.5)

    before = db.total_changes
    report = build_guard_impact_audit(hours=24)
    after = db.total_changes

    assert after == before
    assert report["active_blocking_guards"]["total"] == 1
    assert report["candidate_flow"]["guard_blocked_candidates"] == 1
    row = report["blocked_candidates"][0]
    assert row["matched_guards"][0]["guard_id"] == "auto_momentum_long_test"
    assert row["matched_guards"][0]["classification"] == ["family-wide", "direction-wide"]
    assert row["matched_guards"][0]["evidence_symbol"] == "SEED/USDT"
    assert row["outcomes"]["forward"]["4h"]["direction_adjusted_return_pct"] == 2.5
    assert row["verdict"] == "overbroad_suspect"
    assert report["verdict"] == "overbroad_suspect"


def test_guard_impact_negative_blocked_outcome_is_protective(tmp_path):
    from aletheia.storage.sqlite import get_db
    from aletheia.analytics.guard_impact import build_guard_impact_audit

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db)
    _insert_opportunity(db)
    _insert_forward(db, ret=-3.0)

    report = build_guard_impact_audit(hours=24)

    assert report["blocked_candidates"][0]["verdict"] == "protective"
    assert report["verdict"] == "protective"


def test_guard_impact_cli_outputs_human_and_json(tmp_path):
    from aletheia.storage.sqlite import get_db
    from aletheia.cli.inspect_cmd import inspect_app

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db)
    _insert_opportunity(db)

    runner = CliRunner()
    human = runner.invoke(inspect_app, ["guard-impact", "--hours", "24"])
    raw = runner.invoke(inspect_app, ["guard-impact", "--hours", "24", "--json"])

    assert human.exit_code == 0
    assert "Guard Impact Audit" in human.output
    assert "guard_blocked_candidates: 1" in human.output
    assert raw.exit_code == 0
    assert '"guard_blocked_candidates": 1' in raw.output
