from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner


def _write_guard(tmp_path: Path, *, guard_id: str = "manual_momentum_long_test") -> None:
    # A manual (owner-authored) blocking guard: learned/auto guards no longer block at
    # enforcement (P0 2026-07-12 shadow-only regime), so the impact audit — which
    # measures the counterfactual of ACTUAL blocks — is exercised with a real blocker.
    guards = tmp_path / "knowledge" / "guards"
    guards.mkdir(parents=True, exist_ok=True)
    (guards / f"{guard_id}.md").write_text(
        """---
guard_id: manual_momentum_long_test
title: manual momentum long test
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
        "liquidity_score, estimated_cost, crowding_risk, late_entry_penalty, scanner_family, expires_at, risk_flags, "
        "learning_candidate_key) "
        "VALUES (?, 'snap-guard-impact', ?, ?, 1, 80, 70, 90, 90, 1, 0, 5, 'momentum', datetime('now','+1 hour'), '[]', ?)",
        (member_id, f"asset-{member_id}", symbol, f"candidate-{member_id}"),
    )


def _expire_member(db: sqlite3.Connection, member_id: str = "member-1") -> None:
    db.execute(
        "UPDATE universe_members SET expires_at = datetime('now','-1 hour') WHERE id = ?",
        (member_id,),
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
    directional = -ret if direction == "short" else ret
    net = directional - 0.2
    exit_price = 100.0 * (1.0 + ret / 100.0)
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, scanner_family, direction, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, gross_return_pct, "
        "directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, "
        "estimated_funding_pct, net_return_pct, min_tradable_edge_pct, settlement_price_source, "
        "settlement_lag_sec, cost_model_version, status) "
        "VALUES (?, ?, 'bitget', ?, 'momentum', ?, ?, 100, datetime('now','-5 hours'), "
        "datetime('now','-1 hour'), datetime('now'), ?, ?, ?, 0.1, 0.1, 0, ?, 0.05, "
        "'horizon_ohlcv', 0, 'v1', 'settled')",
        (
            f"fwd-{member_id}-{horizon}", f"candidate-{member_id}", symbol, direction,
            horizon, exit_price, ret, directional, net,
        ),
    )


def _insert_keyed_shadow_with_canonical(
    db: sqlite3.Connection,
    *,
    opportunity_id: str = "opp-guard-impact",
    symbol: str = "SEED/USDT",
    shadow_venue: str = "bitget",
    canonical_venue: str = "bitget",
) -> None:
    db.execute(
        "INSERT INTO shadow_trades "
        "(id, batch_id, opportunity_id, symbol, venue, market_type, direction, scanner_family, "
        "entry_price, outcome_candidate_key, was_selected, created_at, exit_price, pnl_pct, "
        "gross_return_pct, net_pnl_pct, estimated_fee_pct, estimated_spread_slippage_pct, "
        "estimated_funding_pct, min_tradable_edge_pct, settlement_price_source, settlement_lag_sec, "
        "cost_model_version, resolved_at) "
        "VALUES (?, 'batch-guard-impact', ?, ?, ?, 'perp', 'long', 'momentum', 100, ?, 1, "
        "datetime('now','-1 hour'), 102, 2, 2, 1.8, 0.1, 0.1, 0, 0.05, 'horizon_ohlcv', 0, 'v1', datetime('now'))",
        (
            "shadow-guard-impact",
            opportunity_id,
            symbol,
            shadow_venue,
            f"opportunity:{opportunity_id}",
        ),
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, scanner_family, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, gross_return_pct, "
        "directional_return_pct, estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
        "net_return_pct, min_tradable_edge_pct, settlement_price_source, settlement_lag_sec, "
        "cost_model_version, status) "
        "VALUES ('fco-guard-impact', ?, ?, ?, 'perp', 'long', 'momentum', '24h', 100, "
        "datetime('now','-1 hour'), datetime('now'), datetime('now'), 102, 2, 2, 0.1, 0.1, 0, "
        "1.8, 0.05, 'horizon_ohlcv', 0, 'v1', 'settled')",
        (f"opportunity:{opportunity_id}", canonical_venue, symbol),
    )


def test_guard_impact_read_model_has_no_llm_http_or_exchange_calls():
    # Resolved from the module rather than a hardcoded path. #3134 moved this
    # read model from core/ into extensions/, and the literal path then raised
    # FileNotFoundError — so this audit silently stopped checking its invariant
    # instead of failing loudly about the move.
    from extensions.analytics import guard_impact

    src_path = Path(guard_impact.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden_import_roots = {
        "aletheia.agents",
        "aletheia.agent_shell",
        "aletheia.exchanges",
        "extensions.exchanges",
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
        "refresh_demo_capabilities",
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


@pytest.mark.parametrize("exists", [False, True])
def test_guard_impact_file_access_is_readonly(tmp_path, monkeypatch, exists):
    import extensions.analytics.guard_impact as report_module
    import aletheia.storage.sqlite as sq

    path = tmp_path / "guard-history.db"
    if exists:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sentinel (value TEXT)")
            connection.execute("INSERT INTO sentinel VALUES ('preserved')")
    sq.reset_db()
    sq._db_conn = None
    monkeypatch.setattr(sq, "db_path", lambda: path)
    original_get_db = sq.get_db
    observations = []

    def checked_get_db():
        connection = original_get_db()
        observations.append(connection.execute("PRAGMA query_only").fetchone()[0])
        assert observations[-1] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE sentinel SET value='must not persist'")
        return connection

    monkeypatch.setattr(report_module, "get_db", checked_get_db)
    try:
        report = report_module.build_guard_impact_audit(hours=24)
        assert report["verdict"] == "insufficient_data"
        assert path.exists() is exists
        if exists:
            assert observations == [1]
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT value FROM sentinel").fetchall() == [("preserved",)]
    finally:
        sq.reset_db()
        sq._db_conn = None


def test_guard_impact_empty_db_does_not_crash(monkeypatch):
    import aletheia.storage.sqlite as sq
    from extensions.analytics.guard_impact import build_guard_impact_audit

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn

    report = build_guard_impact_audit(hours=24)

    assert report["active_blocking_guards"]["total"] == 0
    assert report["candidate_flow"]["recent_open_opportunities"] == 0
    assert report["blocked_candidates"] == []
    assert report["verdict"] == "insufficient_data"


def test_guard_impact_no_active_guards_returns_clean_report():
    from extensions.analytics.guard_impact import build_guard_impact_audit

    report = build_guard_impact_audit(hours=24)

    assert report["active_blocking_guards"]["total"] == 0
    assert report["blocked_candidates"] == []
    assert report["verdict"] == "insufficient_data"


def test_guard_impact_flags_positive_blocked_outcome_as_overbroad_suspect(tmp_path):
    from extensions.analytics.guard_impact import build_guard_impact_audit
    from aletheia.storage.sqlite import get_db

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db, symbol="SEED/USDT")
    _insert_opportunity(db, symbol="SEED/USDT")
    _insert_forward(db, symbol="SEED/USDT", ret=2.5)

    before = db.total_changes
    report = build_guard_impact_audit(hours=24)
    after = db.total_changes

    assert after == before
    assert report["active_blocking_guards"]["total"] == 1
    assert report["candidate_flow"]["guard_blocked_candidates"] == 1
    row = report["blocked_candidates"][0]
    assert row["matched_guards"][0]["guard_id"] == "manual_momentum_long_test"
    assert row["matched_guards"][0]["classification"] == ["symbol-specific", "family-wide", "direction-wide"]
    assert row["matched_guards"][0]["evidence_symbol"] == "SEED/USDT"
    assert row["outcomes"]["forward"]["4h"]["direction_adjusted_return_pct"] == 2.5
    assert row["verdict"] == "overbroad_suspect"
    assert report["verdict"] == "overbroad_suspect"


def test_guard_impact_negative_blocked_outcome_is_protective(tmp_path):
    from extensions.analytics.guard_impact import build_guard_impact_audit
    from aletheia.storage.sqlite import get_db

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db, symbol="SEED/USDT")
    _insert_opportunity(db, symbol="SEED/USDT")
    _insert_forward(db, symbol="SEED/USDT", ret=-3.0)

    report = build_guard_impact_audit(hours=24)

    assert report["blocked_candidates"][0]["verdict"] == "protective"
    assert report["verdict"] == "protective"


def test_guard_impact_excludes_keyed_shadow_with_canonical_identity_mismatch(tmp_path):
    from extensions.analytics.guard_impact import build_guard_impact_audit
    from aletheia.storage.sqlite import get_db

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db, symbol="SEED/USDT")
    _insert_opportunity(db, symbol="SEED/USDT")
    _insert_keyed_shadow_with_canonical(
        db,
        shadow_venue="bitget",
        canonical_venue="other-venue",
    )

    report = build_guard_impact_audit(hours=24)

    assert report["blocked_candidates"][0]["outcomes"]["shadow"] is None
    assert report["blocked_candidates"][0]["verdict"] == "insufficient_data"
    assert report["verdict"] == "insufficient_data"


def test_guard_impact_excludes_local_pre_guard_skips(tmp_path):
    from extensions.analytics.guard_impact import build_guard_impact_audit
    from aletheia.storage.sqlite import get_db

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db)
    _expire_member(db)
    _insert_opportunity(db)

    report = build_guard_impact_audit(hours=24)

    assert report["candidate_flow"]["recent_open_opportunities"] == 1
    assert report["candidate_flow"]["pre_guard_skipped"] == {"ttl_expired": 1}
    assert report["candidate_flow"]["guard_blocked_candidates"] == 0
    assert report["blocked_candidates"] == []


def test_guard_impact_cli_outputs_human_and_json(tmp_path):
    from aletheia.cli.inspect_cmd import inspect_app
    from aletheia.storage.sqlite import get_db

    _write_guard(tmp_path)
    db = get_db()
    _insert_member(db, symbol="SEED/USDT")
    _insert_opportunity(db, symbol="SEED/USDT")

    runner = CliRunner()
    human = runner.invoke(inspect_app, ["guard-impact", "--hours", "24"])
    raw = runner.invoke(inspect_app, ["guard-impact", "--hours", "24", "--json"])

    assert human.exit_code == 0
    assert "Guard Impact Audit" in human.output
    assert "guard_blocked_candidates: 1" in human.output
    assert "Also summarized in the opportunity ledger:" in human.output
    assert "uv run aletheia flow audit --hours 24" in human.output
    assert raw.exit_code == 0
    assert '"guard_blocked_candidates": 1' in raw.output
    assert "opportunity ledger" not in raw.output
