"""Focused regressions for B7-mini honesty batch (#58, #65, #87).

- #58: backup omitted data/logs/audit.jsonl (the hash-chained audit log).
       A restore of the archive couldn't verify audit integrity because the
       file itself was gone.
- #65: /trades/export claimed tax/PnL in its docstring but returned rows from
       the orders table — no realized PnL, no fees, no closed_at. Now returns
       closed positions with the columns a tax export actually needs.
- #87: POST /api/capital/events persisted the capital_events row but wrote no
       audit record. The equity-curve baseline shift was invisible in the audit
       hash chain, so investigating a baseline jump had no trace.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


# ---------------------------------------------------------------------------
# #58 — backup now includes data/logs/ (audit.jsonl)
# ---------------------------------------------------------------------------


def test_backup_captures_audit_jsonl_hash_chain(tmp_path, monkeypatch):
    """#58: audit.jsonl must round-trip through backup so verify_audit_chain
    can run after a restore."""
    _setup_env(tmp_path, monkeypatch)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "audit.jsonl").write_text(
        '{"event_type":"test","message":"seed row"}\n',
        encoding="utf-8",
    )

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup(label="audit-inclusion")

    with tarfile.open(archive, "r:gz") as tar:
        member_names = tar.getnames()
    assert any(
        n.endswith("logs/audit.jsonl") for n in member_names
    ), f"backup must include logs/audit.jsonl; got {member_names}"


# ---------------------------------------------------------------------------
# #65 — /trades/export exports closed positions, not orders
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_trades_export_returns_closed_positions_with_pnl_columns(tmp_path, monkeypatch):
    """#65: the CSV now carries realized_pnl / entry_fee / exit_fee / closed_at
    columns, not the orders schema."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    pos = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, current_price=110.0,
        realized_pnl=9.85, entry_fee=0.06, exit_fee=0.066,
        execution_mode="bitget_demo", status="closed",
        close_reason="take_profit",
        opened_at="2026-07-06T00:00:00Z", closed_at="2026-07-07T00:00:00Z",
    )
    insert_position(pos)

    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/trades/export?fmt=csv")

    assert r.status_code == 200
    text = r.text
    header = text.splitlines()[0].split(",")
    assert "realized_pnl" in header, f"tax export must include realized_pnl; got {header}"
    assert "closed_at" in header
    assert "entry_fee" in header
    assert "exit_fee" in header
    assert "BTC/USDT" in text
    assert "9.85" in text  # the actual realized PnL made it in


# ---------------------------------------------------------------------------
# #87 — capital events write an audit record
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_capital_deposit_writes_audit_event(tmp_path, monkeypatch):
    """#87: a deposit call must land in audit.jsonl so the equity-curve baseline
    jump has a trace. Previously the row went into capital_events with no audit."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/capital/events",
            json={
                "broker_id": "bitget_demo",
                "event_type": "deposit",
                "amount_usdt": 1000.0,
                "note": "seed capital",
            },
        )
    assert r.status_code == 200

    audit_path = tmp_path / "logs" / "audit.jsonl"
    assert audit_path.exists(), "audit.jsonl should be created on first capital event"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    # verify_audit format prefixes each row with a hash; the payload is JSON.
    import json
    entries = [json.loads(line) for line in lines if line.strip()]
    deposits = [e for e in entries if e.get("event_type") == "capital_deposit"]
    assert deposits, f"audit should have a capital_deposit event; got {entries}"
    e = deposits[0]
    assert e["entity_type"] == "capital_event"
    assert e["after"]["amount_usdt"] == 1000.0
    assert e["after"]["broker_id"] == "bitget_demo"


@pytest.mark.anyio
async def test_capital_withdraw_writes_audit_event(tmp_path, monkeypatch):
    """#87 symmetry: withdrawals also emit audit — event_type is capital_withdraw
    so an operator can grep either direction."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/capital/events",
            json={
                "broker_id": "bitget_demo",
                "event_type": "withdraw",
                "amount_usdt": 250.0,
                "note": "test withdrawal",
            },
        )
    assert r.status_code == 200

    import json
    lines = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    withdraws = [e for e in entries if e.get("event_type") == "capital_withdraw"]
    assert withdraws, f"audit should have capital_withdraw; got {entries}"
