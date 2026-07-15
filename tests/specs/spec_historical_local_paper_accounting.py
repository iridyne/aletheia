"""Historical local-paper accounting must remain auditable and fail-closed."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from tests.specs.spec_meta import SpecClaim, spec_claim


def _setup(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    sq._db_conn = conn
    run_migrations()
    return conn


def _iso(hour: int, minute: int = 0) -> str:
    return datetime(2026, 7, 14, hour, minute, tzinfo=timezone.utc).isoformat()


def _legacy_closed_future(conn, *, position_id: str = "legacy-position", complete_schedule: bool = True):
    conn.execute("INSERT INTO trade_proposals (id,symbol,market_type,direction,order_type,quantity,price,execution_mode,status,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("legacy-proposal", "BTC/USDT", "usdt_futures", "long", "market", 2.0, 100.0, "paper_internal", "executed", "native", _iso(0, 30)))
    # Intentionally legacy: this is what historical recovery must not upgrade.
    conn.execute("INSERT INTO risk_decisions (id,proposal_id,approved,allowed_execution_mode,proposal_execution_mode,decision_role,created_at) VALUES (?,?,?,?,?,?,?)", ("legacy-risk", "legacy-proposal", 1, "paper_internal", "paper_internal", "legacy", _iso(0, 30)))
    conn.execute("INSERT INTO orders (id,proposal_id,risk_decision_id,symbol,market_type,direction,order_type,quantity,price,execution_mode,status,source,created_at,filled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("legacy-order", "legacy-proposal", "legacy-risk", "BTC/USDT", "usdt_futures", "long", "market", 2.0, 100.0, "paper_internal", "filled", "native", _iso(0, 30), _iso(0, 30)))
    conn.execute("INSERT INTO fills (id,order_id,fill_price,fill_quantity,fee,filled_at) VALUES (?,?,?,?,?,?)", ("legacy-fill", "legacy-order", 100.0, 2.0, 0.0, _iso(0, 30)))
    conn.execute("INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,current_price,realized_pnl,execution_mode,proposal_id,order_id,status,opened_at,closed_at,entry_fee,exit_fee) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (position_id, "BTC/USDT", "usdt_futures", "long", 2.0, 100.0, 110.0, 20.0, "paper_internal", "legacy-proposal", "legacy-order", "closed", _iso(0, 30), _iso(9), 0.0, 0.0))
    snapshots = [("a", 4, 0, 20, 0.0008), ("b", 4, 3, 59, 0.0010), ("c", 8, 4, 1, -0.0004), ("d", 8, 7, 59, -0.0005), ("e", 12, 8, 1, 0.0002), ("f", 12, 8, 55, 0.0002)]
    if not complete_schedule:
        snapshots = [item for item in snapshots if item[0] != "d"]
    for ident, target, observed, minute, rate in snapshots:
        conn.execute("INSERT INTO funding_rate_snapshots (id,symbol,exchange,funding_rate,funding_rate_annualized,next_funding_time,observed_at) VALUES (?,?,?,?,?,?,?)", (f"snap-{ident}", "BTC/USDT", "okx", rate, rate * 3 * 365, _iso(target), _iso(observed, minute)))


@spec_claim(SpecClaim("HLPA-01", "historical_local_paper_accounting", "separate historical layer", "L2b", "An exact missing historical Order-to-Position backlink may be repaired, but its legacy economics remain separate from verified strategy PnL and raw Position PnL."))
def test_historical_repair_is_dry_run_first_idempotent_and_separate(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _legacy_closed_future(conn)
    from aletheia.maintenance.historical_accounting import reconcile_historical_local_paper_accounting, historical_accounting_summary

    dry = reconcile_historical_local_paper_accounting()
    assert dry["summary"] == {"link_repairs_planned": 1, "reconstructed": 1, "unavailable": 0}
    assert conn.execute("SELECT position_id FROM orders WHERE id='legacy-order'").fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM historical_accounting_reconciliations").fetchone()[0] == 0

    applied = reconcile_historical_local_paper_accounting(dry_run=False)
    assert applied["summary"]["link_repairs_applied"] == 1
    assert applied["summary"]["reconciliations_applied"] == 1
    assert conn.execute("SELECT position_id FROM orders WHERE id='legacy-order'").fetchone()[0] == "legacy-position"
    # Funding is +0.2 then -0.1, so the separate reconstructed result is 19.9.
    assert conn.execute("SELECT reconstructed_realized_pnl FROM historical_accounting_reconciliations").fetchone()[0] == 19.9
    assert conn.execute("SELECT realized_pnl FROM positions WHERE id='legacy-position'").fetchone()[0] == 20.0
    assert conn.execute("SELECT decision_role FROM risk_decisions WHERE id='legacy-risk'").fetchone()[0] == "legacy"
    assert historical_accounting_summary()["historical_reconciled_pnl"] == 19.9
    # The normal close path still rejects the legacy Risk authority row even
    # after its deterministic backlink is restored.
    from aletheia.portfolio.local_funding import compute_local_funding
    position = dict(conn.execute("SELECT * FROM positions WHERE id='legacy-position'").fetchone())
    strict = compute_local_funding(position, closed_at=position["closed_at"])
    assert strict.complete is False
    assert strict.evidence["reason"] == "entry_identity_mismatch"
    assert reconcile_historical_local_paper_accounting(dry_run=False)["summary"]["reconciliations_applied"] == 0


@spec_claim(SpecClaim("HLPA-02", "historical_local_paper_accounting", "unavailable remains explicit", "L2b", "A missing historical funding schedule must produce an unavailable reconciliation, never an estimated number."))
def test_historical_repair_fails_closed_when_schedule_is_incomplete(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _legacy_closed_future(conn, complete_schedule=False)
    from aletheia.maintenance.historical_accounting import reconcile_historical_local_paper_accounting

    result = reconcile_historical_local_paper_accounting(dry_run=False)
    assert result["summary"]["reconstructed"] == 0
    row = conn.execute("SELECT status,reconstructed_realized_pnl,reason FROM historical_accounting_reconciliations").fetchone()
    assert tuple(row) == ("unavailable", None, "missing_event_observation")
