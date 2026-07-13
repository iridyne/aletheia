"""Bitget account-routing contracts for authoritative execution Orders."""

from __future__ import annotations

from types import SimpleNamespace
import sqlite3

import pytest

from tests.execution_authority import seed_order_authority
from tests.specs.spec_meta import SpecClaim, spec_claim


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


@spec_claim(
    SpecClaim(
        "PL-EAR-01",
        "pipeline_exchange_account_routing",
        "§1 authoritative Bitget account routing",
        "L2b",
        "A Bitget entry Order selects demo/live account mode from its persisted "
        "authoritative execution_mode, never from mutable exchange config.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "execution_mode,mutable_config_mode,expected_client_mode",
    (
        ("bitget_demo", "live", "demo"),
        ("bitget_live_limited", "demo", "live"),
    ),
)
def test_entry_client_mode_comes_from_authoritative_order(
    tmp_path,
    monkeypatch,
    execution_mode,
    mutable_config_mode,
    expected_client_mode,
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage import repositories as repo

    cfg = load_config()
    cfg.exchange.mode = mutable_config_mode
    save_config(cfg)

    proposal_id = f"proposal-account-{execution_mode}"
    risk_decision_id = f"risk-account-{execution_mode}"
    seed_order_authority(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode=execution_mode,
    )

    observed_clients: list[tuple[str, str | None]] = []

    class _FakeBitgetClient:
        def __init__(self, mode):
            orders = repo.list_orders(limit=10)
            persisted_mode = orders[0]["execution_mode"] if orders else None
            observed_clients.append((mode, persisted_mode))

        async def place_order(self, _request):
            return SimpleNamespace(
                order_id="EX-ACCOUNT-ROUTING",
                status="submitted",
                fill_price=None,
                filled_quantity=None,
            )

    monkeypatch.setattr(bitget_broker, "BitgetClient", _FakeBitgetClient)

    bitget_broker.execute_live_order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode=execution_mode,
    )

    assert observed_clients == [(expected_client_mode, execution_mode)]
