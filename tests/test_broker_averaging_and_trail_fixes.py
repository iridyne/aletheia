"""Focused regressions for B7-broker batch (#139, #140, #141).

- #140: averaging-in on the three live brokers used to overwrite entry_fee with
  the new leg's fee (or leave it null when the row was legacy). Realized PnL
  after the eventual close was re-derived from the fee schedule (single leg)
  and fed to the learning loop as if fees were smaller than they really were.
- #141: liquidation_price was NOT recomputed when averaging in shifts the
  weighted-average entry — the row kept the FIRST leg's liquidation. Display
  and LLM context saw the wrong level; the real risk-gate uses the exchange's
  own liquidation so this is a display/analytics bug, not a fund-loss bug, but
  the ledger has it recorded and the paper_broker parity fix is trivial.
- #139: exchange-side stop replacement (used by the trail loop when the DB
  stop_price tightens) was Bitget-only. On OKX the exchange bracket kept the
  ORIGINAL stop while the DB tightened, so a gateway outage would let the
  looser bracket close first. Now dispatched broker-agnostically through
  execution.stops. Binance TPSL surface not implemented yet — logs+skips.

The batch is gated behind cfg.trading.exchange_tpsl_enabled (default False),
so this is dormant in production until the operator opts in — but the code
paths must be correct so that opt-in doesn't silently under-protect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    """Minimal isolation. Mirrors other B7 regression tests' shape."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()



def _make_open_position(
    *,
    execution_mode: str,
    symbol: str = "BTC/USDT",
    market_type: str = "usdt_futures",
    direction: str = "long",
    quantity: float = 1.0,
    entry_price: float = 100.0,
    entry_fee: float = 0.06,
    leverage: int = 10,
    liquidation_price: float = 91.0,
    stop_price: float | None = 95.0,
) -> str:
    """Insert a Position row directly so we can assert what averaging-in updates."""
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position

    pos = Position(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        current_price=entry_price,
        execution_mode=execution_mode,
        strategy_id=None,
        proposal_id=None,
        order_id=None,
        opportunity_id=None,
        leverage=leverage,
        liquidation_price=liquidation_price,
        stop_price=stop_price,
        take_profit_price=None,
        entry_fee=entry_fee,
        status="open",
    )
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(pos)
    return pos.id


# ---------------------------------------------------------------------------
# #140 + #141 — averaging-in entry_fee accumulation + liquidation recompute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("execution_mode,fee_rate", [("bitget_live_limited", 0.0006), ("okx_demo", 0.0005), ("binance_testnet", 0.0004)])
def test_live_broker_averaging_in_accumulates_entry_fee_and_recomputes_liquidation(
    tmp_path, monkeypatch, execution_mode, fee_rate,
):
    """#140 + #141: the three live brokers now match paper_broker's averaging-in
    invariants — entry_fee accumulates across legs, liquidation_price is
    recomputed from the weighted-average entry (unchanged leverage)."""
    _setup_env(tmp_path, monkeypatch)

    from tests.specs.spec_pipeline_stop_propagation import _stub_uta_execution, _patch_filled_place_order, _patch_private_fill_history
    _stub_uta_execution(monkeypatch)

    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import (
        _select_one,
        insert_risk_decision,
        insert_trade_proposal,
    )

    # Seed a Position with a KNOWN entry_fee so the accumulation delta is clear.
    entry_price_leg1 = 100.0
    qty_leg1 = 1.0
    fee_leg1 = round(entry_price_leg1 * qty_leg1 * fee_rate, 8)
    leverage = 10
    from aletheia.execution.position_enrichment import liquidation_price_for
    liq_leg1 = liquidation_price_for(entry_price_leg1, "long", leverage, "BTC/USDT")

    position_id = _make_open_position(
        execution_mode=execution_mode,
        entry_price=entry_price_leg1,
        quantity=qty_leg1,
        entry_fee=fee_leg1,
        leverage=leverage,
        liquidation_price=liq_leg1,
    )

    # A minimal proposal — the broker itself will insert the order row.
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=110.0,
        execution_mode=execution_mode,
        agent_rationale="averaging-in leg 2",
        status="approved",
    )
    insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=execution_mode,
        proposal_execution_mode=execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    insert_risk_decision(decision)

    from aletheia.execution import coordinator
    fill_price_leg2 = 110.0
    fill_qty_leg2 = 1.0
    _patch_filled_place_order(monkeypatch, fill_price=fill_price_leg2)
    _patch_private_fill_history(monkeypatch, fill_price=fill_price_leg2, fee_rate=fee_rate)
    submission = coordinator.execute_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=fill_qty_leg2,
        price=fill_price_leg2,
        execution_mode=execution_mode,
        leverage=leverage,
    )
    assert submission["order_status"] == "materialization_missing"

    # Broker acknowledgement does not update accounting state. The averaging
    # leg becomes durable only after order-matched private fills are reconciled.
    from aletheia.execution.reconciliation import reconcile

    reconciled = reconcile()
    assert reconciled["filled"] == 1

    # The Position row should now reflect BOTH legs.
    updated = _select_one("positions", position_id)
    assert updated is not None
    assert updated["quantity"] == pytest.approx(qty_leg1 + fill_qty_leg2)
    expected_avg_entry = (
        (entry_price_leg1 * qty_leg1 + fill_price_leg2 * fill_qty_leg2)
        / (qty_leg1 + fill_qty_leg2)
    )
    assert updated["entry_price"] == pytest.approx(expected_avg_entry)

    # #140: entry_fee accumulates (leg1 + leg2), does NOT get replaced.
    fee_leg2 = round(fill_price_leg2 * fill_qty_leg2 * fee_rate, 8)
    assert updated["entry_fee"] == pytest.approx(fee_leg1 + fee_leg2)

    # #141: liquidation_price is recomputed for the new average entry (leverage unchanged).
    expected_liq = liquidation_price_for(expected_avg_entry, "long", leverage, "BTC/USDT")
    assert updated["liquidation_price"] == pytest.approx(expected_liq)
    assert updated["liquidation_price"] != liq_leg1  # actually moved


# ---------------------------------------------------------------------------
# #139 — exchange stop replacement dispatches per broker (not bitget-only)
# ---------------------------------------------------------------------------


def test_replace_exchange_protective_stop_dispatches_to_okx_when_okx_demo(
    tmp_path, monkeypatch,
):
    """#139: a tightened trailed stop on an okx_demo position now reaches
    OKX via replace_okx_exchange_stop — the trail loop previously fell through
    to a Bitget-only branch and OKX stayed at the ORIGINAL exchange bracket."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.exchange_tpsl_enabled = True
    save_config(cfg)

    position_id = _make_open_position(execution_mode="okx_demo", stop_price=95.0)

    called = {}

    def fake_replace_okx(pos_id, new_stop_price):
        called["pos_id"] = pos_id
        called["new_stop_price"] = new_stop_price
        return True
    monkeypatch.setattr(
        "aletheia.execution.coordinator.replace_protective_stop", fake_replace_okx
    )

    from aletheia.execution.stops import replace_exchange_protective_stop

    assert replace_exchange_protective_stop(position_id, 97.5) is True

    assert called == {"pos_id": position_id, "new_stop_price": 97.5}


def test_replace_exchange_protective_stop_still_dispatches_bitget(
    tmp_path, monkeypatch,
):
    """#139: bitget path unchanged — the dispatcher preserves the pre-existing
    behaviour instead of accidentally regressing it."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.exchange_tpsl_enabled = True
    save_config(cfg)

    position_id = _make_open_position(execution_mode="bitget_demo", stop_price=95.0)

    called = {}

    def fake_replace_bitget(pos_id, new_stop_price):
        called["pos_id"] = pos_id
        called["new_stop_price"] = new_stop_price
        return True
    monkeypatch.setattr(
        "aletheia.execution.coordinator.replace_protective_stop", fake_replace_bitget
    )

    from aletheia.execution.stops import replace_exchange_protective_stop

    assert replace_exchange_protective_stop(position_id, 97.5) is True

    assert called == {"pos_id": position_id, "new_stop_price": 97.5}


def test_replace_exchange_protective_stop_is_noop_when_flag_disabled(
    tmp_path, monkeypatch,
):
    """The whole feature is gated on cfg.trading.exchange_tpsl_enabled (default
    False). With the flag off the dispatcher must be a total no-op — even a
    Bitget position must not touch the exchange."""
    _setup_env(tmp_path, monkeypatch)
    # Config left at defaults → exchange_tpsl_enabled=False.
    position_id = _make_open_position(execution_mode="bitget_demo")

    called = []

    def _fail(*args, **kwargs):
        called.append(("bitget", args, kwargs))

    monkeypatch.setattr("aletheia.execution.coordinator.replace_protective_stop", _fail)

    from aletheia.execution.stops import replace_exchange_protective_stop
    assert replace_exchange_protective_stop(position_id, 97.5) is False

    assert called == []


def test_replace_exchange_protective_stop_binance_logs_and_skips(
    tmp_path, monkeypatch, caplog,
):
    """#139: Binance TPSL surface isn't implemented yet — the dispatcher logs a
    warning and skips rather than crashing on a missing import. The DB stop is
    already tightened by the outer caller; this test asserts we don't blow up."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.exchange_tpsl_enabled = True
    save_config(cfg)

    position_id = _make_open_position(execution_mode="binance_testnet")
    from tests.specs.spec_pipeline_stop_propagation import _stub_uta_execution, _patch_filled_place_order
    _stub_uta_execution(monkeypatch)
    calls = _patch_filled_place_order(monkeypatch, fill_price=100.0)
    from aletheia.execution.stops import replace_exchange_protective_stop
    import logging

    with caplog.at_level(logging.WARNING, logger="aletheia.execution.stops"):
        assert replace_exchange_protective_stop(position_id, 97.5) is False
    assert calls == []
