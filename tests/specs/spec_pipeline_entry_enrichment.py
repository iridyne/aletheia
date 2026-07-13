"""Spec: Pipeline — Entry-time metadata enrichment + close routing (end-to-end)

Regression lock for the EC-stop sibling bug class: the three real-exchange paths
(okx/binance/bitget) historically materialized a Position WITHOUT the entry-time
metadata that paper_broker records — btc_entry_price, regime_tag,
entry_market_summary, decision_reasoning, entry_fee, slippage_cost. These fields
now survive the broker acknowledgement handoff and are applied only when exact
private fill evidence creates the Position.

A second claim covers the close side: a demo/live position MUST close through its
exchange broker (real reduce + real exit price), never a local-only close that
fabricates the exit and leaves a dangling exchange position — routed via the single
source of truth position_manager.close_position_routed.

Coverage is broker-PARALLEL so a future copy-paste into any one broker cannot
silently re-drop the metadata. Only submission and private-history boundaries
are mocked.

Contract: pipeline_entry_enrichment
Claim prefix: PL-ENRICH
"""

import json

import pytest

from tests.execution_authority import seed_order_authority
from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.specs.spec_pipeline_stop_propagation import (
    _BROKERS,
    _patch_filled_place_order,
    _patch_private_fill_history,
    _materialize_exchange_result,
    _setup_db,
    _stub_exchange_secrets,
)


def _patch_enrichment_lookups(monkeypatch, *, btc=60000.0, regime="risk_on"):
    """Pin the best-effort btc/regime lookups so the enrichment is deterministic.

    build_entry_enrichment resolves these as module globals at call time, so patching
    them on position_enrichment is what every broker sees.
    """
    import aletheia.execution.position_enrichment as pe
    monkeypatch.setattr(pe, "btc_price_now", lambda: btc)
    monkeypatch.setattr(pe, "current_regime", lambda: regime)
    from aletheia.execution import gateway

    monkeypatch.setattr(
        gateway,
        "capture_paper_attribution_snapshot",
        lambda symbol: {
            "btc_entry_price": btc,
            "regime_tag": regime,
            "market_context": {"symbol": symbol},
        },
    )


# ---------------------------------------------------------------------------
# PL-ENRICH-01  L2b  private fill stamps entry-time metadata (×3 brokers)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ENRICH-01", "pipeline_entry_enrichment", "§1 open_stamps_metadata", "L2b",
        "Order-matched private fill materialization stamps btc_entry_price, regime_tag, "
        "entry_fee and slippage_cost onto the new Position (okx/binance/bitget) — none NULL",
    ),
)
@pytest.mark.parametrize("label,broker_path,client_path,exec_mode", _BROKERS)
def test_broker_open_stamps_entry_metadata(
    tmp_path, monkeypatch, label, broker_path, client_path, exec_mode
):
    _setup_db(tmp_path, monkeypatch)
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, client_path, fill_price=60000.0)
    _patch_private_fill_history(
        monkeypatch, broker_path, client_path, fill_price=60000.0
    )
    _patch_enrichment_lookups(monkeypatch, btc=60000.0, regime="risk_on")

    from aletheia.storage import repositories as repo

    broker = __import__(broker_path, fromlist=["execute_live_order"])
    proposal_id = f"p-enrich-{label}"
    risk_decision_id = f"rd-enrich-{label}"
    seed_order_authority(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=59950.0,
        execution_mode=exec_mode,
        agent_rationale="direct enrichment rationale",
        attribution_snapshot=json.dumps({
            "btc_entry_price": 60000.0,
            "regime_tag": "risk_on",
            "market_context": {"source": "spec"},
        }),
    )
    # requested price 59950 vs fill 60000 → slippage_cost = 50 * 0.01 = 0.5
    result = broker.execute_live_order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=59950.0,
        execution_mode=exec_mode,
    )

    position_id = _materialize_exchange_result(result)
    pos = repo._select_one("positions", position_id)
    assert pos is not None, f"{label}: position row must exist"
    assert pos["btc_entry_price"] == 60000.0, (
        f"{label}: btc_entry_price dropped (got {pos['btc_entry_price']!r}) — "
        f"alpha/beta separation goes blind"
    )
    assert pos["regime_tag"] == "risk_on", (
        f"{label}: regime_tag dropped (got {pos['regime_tag']!r}) — per-regime learning collapses"
    )
    assert pos["entry_fee"] is not None and pos["entry_fee"] > 0, (
        f"{label}: entry_fee dropped (got {pos['entry_fee']!r})"
    )
    assert pos["slippage_cost"] == pytest.approx(0.5), (
        f"{label}: slippage_cost wrong/dropped (got {pos['slippage_cost']!r})"
    )


# ---------------------------------------------------------------------------
# PL-ENRICH-02  L2b  gateway path persists decision_reasoning from the proposal
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ENRICH-02", "pipeline_entry_enrichment", "§2 decision_reasoning_backlink", "L2b",
        "submit_proposal(okx_demo, agent_rationale=R) → the opened position has "
        "decision_reasoning == R (post-trade-review/UI explainability back-link)",
    ),
)
def test_gateway_persists_decision_reasoning(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, "aletheia.exchanges.okx.client", fill_price=1551.57)
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.okx_broker",
        "aletheia.exchanges.okx.client",
        fill_price=1551.57,
    )
    _patch_enrichment_lookups(monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    rationale = "ENRICH-TEST: momentum breakout, ADX rising, regime risk_on"
    insert_market_snapshot(MarketSnapshot(symbol="ETH/USDT", market_type="usdt_futures", price=1551.57))

    result = submit_proposal(
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        price=1551.57,
        stop_price=round(1551.57 * 0.98, 8),
        execution_mode="okx_demo",
        agent_rationale=rationale,
    )
    if result.get("status") != "materialization_missing":
        pytest.fail(
            "okx_demo proposal did not enter private-fill handoff: "
            f"{result.get('status')} / {result.get('risk_violations')}"
        )

    db = get_db()
    position_id = _materialize_exchange_result(result)
    pos = db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    assert pos is not None, "position row must exist"
    assert pos["decision_reasoning"] == rationale, (
        f"decision_reasoning not pulled from the originating proposal "
        f"(got {pos['decision_reasoning']!r})"
    )
    assert pos["btc_entry_price"] is not None, "btc_entry_price must be stamped on the gateway path too"


# ---------------------------------------------------------------------------
# PL-ENRICH-03  L2c  demo/live positions close via the exchange broker, not local-only
# ---------------------------------------------------------------------------


# Real-exchange DEMO modes whose close MUST go through the exchange broker (not a
# local-only close that would desync the demo exchange + fabricate the exit price).
# okx_demo is the active experiment path. (paper_internal is the only mode allowed to
# close purely locally; *_live_limited is human-only — covered separately in -04.)
_CLOSE_ROUTED = [
    ("bitget_demo", "aletheia.execution.bitget_broker", "aletheia.exchanges.bitget.client", "bitget_demo"),
    ("binance_testnet", "aletheia.execution.binance_broker", "aletheia.exchanges.binance.client", "binance_testnet"),
    ("okx_demo", "aletheia.execution.okx_broker", "aletheia.exchanges.okx.client", "okx_demo"),
]


@spec_claim(
    SpecClaim(
        "PL-ENRICH-03", "pipeline_entry_enrichment", "§3 close_routed_to_exchange", "L2c",
        "close_position_routed on a demo/live position places a REAL exchange close "
        "order (not a local-only close), so the exchange book never desyncs",
    ),
)
@pytest.mark.parametrize("label,broker_path,client_path,exec_mode", _CLOSE_ROUTED)
def test_close_routes_through_exchange(
    tmp_path, monkeypatch, label, broker_path, client_path, exec_mode
):
    _setup_db(tmp_path, monkeypatch)
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, client_path, fill_price=100.0)
    _patch_private_fill_history(
        monkeypatch, broker_path, client_path, fill_price=100.0
    )
    _patch_enrichment_lookups(monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.position_manager import close_position_routed

    broker = __import__(broker_path, fromlist=["execute_live_order"])
    proposal_id = f"p-close-{label}"
    risk_decision_id = f"rd-close-{label}"
    seed_order_authority(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=100.0,
        execution_mode=exec_mode,
    )
    opened = broker.execute_live_order(
        proposal_id=proposal_id, risk_decision_id=risk_decision_id,
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        order_type="market", quantity=0.01, price=100.0,
        execution_mode=exec_mode,
    )
    position_id = _materialize_exchange_result(opened)

    # Route the close. A local-only close would NOT create a second (close) order row.
    close_position_routed(position_id, reason="spec_close", fallback_exit_price=100.0)
    from aletheia.execution.reconciliation import reconcile

    reconcile()

    pos = repo._select_one("positions", position_id)
    assert pos["status"] == "closed", f"{label}: position must be closed"

    db = get_db()
    # The exchange close path writes a SECOND order row (the reducing/close order) tagged
    # with an exchange_order_id. A local-only close (the bug) would leave only the open order.
    orders = db.execute(
        "SELECT * FROM orders WHERE exchange_order_id IS NOT NULL AND exchange_order_id != ''"
    ).fetchall()
    assert len(orders) >= 2, (
        f"{label}: expected an open AND a close order on the exchange "
        f"(local-only close would skip the exchange leg) — got {len(orders)}"
    )


# ---------------------------------------------------------------------------
# PL-ENRICH-04  L2c  the close router REFUSES to autonomously close a live position
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-ENRICH-04", "pipeline_entry_enrichment", "§4 live_close_human_only", "L2c",
        "close_position_routed refuses a *_live_limited position by default (autonomous "
        "live close is forbidden); routes to the exchange broker only with allow_live=True",
    ),
)
def test_router_refuses_autonomous_live_close(tmp_path, monkeypatch):
    """Governance: LIVE positions are human-only — NEVER auto-closed. The router is the
    chokepoint; an autonomous caller (allow_live=False default) must NOT place a real
    live exchange order (CLAUDE.md: never place live orders autonomously)."""
    _setup_db(tmp_path, monkeypatch)
    _stub_exchange_secrets(monkeypatch)
    _patch_filled_place_order(monkeypatch, "aletheia.exchanges.okx.client", fill_price=100.0)
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.okx_broker",
        "aletheia.exchanges.okx.client",
        fill_price=100.0,
    )
    _patch_enrichment_lookups(monkeypatch)

    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.execution.okx_broker import execute_live_order

    seed_order_authority(
        proposal_id="p-live-guard",
        risk_decision_id="rd-live-guard",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=100.0,
        execution_mode="okx_live_limited",
    )
    opened = execute_live_order(
        proposal_id="p-live-guard", risk_decision_id="rd-live-guard",
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        order_type="market", quantity=0.01, price=100.0,
        execution_mode="okx_live_limited",
    )
    position_id = _materialize_exchange_result(opened)

    # Default (autonomous) path MUST refuse — no exchange close order placed.
    with pytest.raises(RuntimeError, match="live"):
        close_position_routed(position_id, reason="auto_should_refuse", fallback_exit_price=100.0)
    assert repo._select_one("positions", position_id)["status"] == "open", (
        "live position must remain open after a refused autonomous close"
    )

    # Explicit human override routes to the real exchange close.
    close_position_routed(position_id, reason="human_close", fallback_exit_price=100.0, allow_live=True)
    from aletheia.execution.reconciliation import reconcile

    reconcile()
    assert repo._select_one("positions", position_id)["status"] == "closed"
    db = get_db()
    orders = db.execute(
        "SELECT * FROM orders WHERE exchange_order_id IS NOT NULL AND exchange_order_id != ''"
    ).fetchall()
    assert len(orders) >= 2, "human-authorized live close must place an exchange close order"
