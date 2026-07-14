"""Spec: paper attribution replay stability (QR-2026-07-01-04).

Paper fill prices are proposal-price based and already stable; the defect was
the attribution fields on Position re-reading BTC/regime/context at execution
time. These claims lock proposal/decision-time attribution snapshots.

Claim prefix: PA
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

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
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


@spec_claim(
    SpecClaim(
        "PA-S01", "paper_attribution_replay", "schema_snapshot_field", "L2a",
        "TradeProposal and trade_proposals expose attribution_snapshot so paper "
        "position attribution can be replay-stable",
    ),
)
def test_trade_proposal_attribution_snapshot_schema(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    from aletheia.storage.models import TradeProposal

    assert "attribution_snapshot" in TradeProposal.model_fields
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(trade_proposals)").fetchall()}
    assert "attribution_snapshot" in cols


@spec_claim(
    SpecClaim(
        "PA-S02", "paper_attribution_replay", "gateway_threads_snapshot", "L2b",
        "Gateway must pass a stored proposal attribution_snapshot into "
        "execute_paper_order instead of forcing the broker to read live context",
    ),
)
def test_gateway_threads_proposal_snapshot_to_paper_broker(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    snapshot = {
        "btc_entry_price": 61000.0,
        "regime_tag": "proposal_regime",
        "market_context": {"data_completeness": {"BTCUSDT": "complete"}},
    }
    proposal = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        attribution_snapshot=json.dumps(snapshot),
    )
    repo.insert_trade_proposal(proposal)

    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _proposal, *, decision_role: SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id="rd-pa-s02",
            violations=[],
            message="ok",
        ),
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    captured = {}

    def _fake_execute_paper_order(**kwargs):
        captured.update(kwargs)
        return {"order_id": "o1", "fill_id": "f1", "position_id": "pos1"}

    monkeypatch.setattr(gateway, "execute_paper_order", _fake_execute_paper_order)

    result = gateway._execute_proposal(proposal)

    assert result["status"] == "executed"
    assert captured["attribution_snapshot"] == snapshot


@spec_claim(
    SpecClaim(
        "PA-S03", "paper_attribution_replay", "broker_uses_snapshot", "L2b",
        "execute_paper_order must stamp Position attribution from the supplied "
        "snapshot; shifted live BTC/regime/context must not change it",
    ),
)
def test_paper_broker_uses_snapshot_for_position_attribution(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.execution import paper_broker
    from aletheia.storage import repositories as repo

    class _Trading:
        slippage_bps = 0
        execution_fee_pct = 0.0
        default_take_profit_pct = 0.0

    class _Config:
        trading = _Trading()

    monkeypatch.setattr(loader, "load_config", lambda: _Config())
    monkeypatch.setattr(paper_broker, "_btc_price_now", lambda: 999999.0)
    monkeypatch.setattr(paper_broker, "_current_regime", lambda: "live_drift")

    snapshot = {
        "btc_entry_price": 61000.0,
        "regime_tag": "proposal_regime",
        "market_context": {
            "data_completeness": {"BTCUSDT": "proposal_complete"},
            "tradability": {"score": 0.91},
            "leverage": {"max": 3},
        },
    }

    seed_order_authority(
        proposal_id="prop-pa-s03",
        risk_decision_id="rd-pa-s03",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    result = paper_broker.execute_paper_order(
        proposal_id="prop-pa-s03",
        risk_decision_id="rd-pa-s03",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        attribution_snapshot=snapshot,
    )

    pos = repo._select_one("positions", result["position_id"])
    assert pos["btc_entry_price"] == pytest.approx(61000.0)
    assert pos["regime_tag"] == "proposal_regime"

    summary = json.loads(pos["entry_market_summary"])
    assert summary["regime"] == "proposal_regime"
    assert summary["price"] == 100.0
    assert summary["data_completeness"]["BTCUSDT"] == "proposal_complete"
    assert summary["tradability"]["score"] == 0.91
