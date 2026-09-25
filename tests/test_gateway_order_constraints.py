"""Gateway-level evidence that legal exchange quantities reach Risk Engine."""

import sqlite3
from types import SimpleNamespace


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
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()


def _approved():
    return SimpleNamespace(approved=True, allowed_execution_mode="bitget_demo", decision_id="risk-1")


def test_adjusted_exchange_quantity_is_rechecked_by_risk_then_submitted(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position, RiskDecision

    seen_by_risk = []
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda p, *, decision_role: (
            seen_by_risk.append((decision_role, p.quantity)) or _approved()
        ),
    )
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda *args: (0.003, None))
    monkeypatch.setattr(gateway, "_check_symbol_availability", lambda *args: None)
    import aletheia.execution.coordinator as broker
    sent = []

    def _persisted_exchange_result(**kw):
        sent.append(kw["quantity"])
        proposal = repo.get_trade_proposal(kw["proposal_id"])
        repo.insert_risk_decision(
            RiskDecision(
                id="risk-1",
                proposal_id=kw["proposal_id"],
                approved=True,
                allowed_execution_mode=kw["execution_mode"],
                proposal_execution_mode=proposal["execution_mode"],
                decision_role="authoritative",
                evaluated_quantity=kw["quantity"],
                proposal_commit_hash=proposal["commit_hash"],
            )
        )
        repo.insert_order(
            Order(
                id="o1",
                proposal_id=kw["proposal_id"],
                risk_decision_id="risk-1",
                client_order_id=kw["proposal_id"],
                position_id="p1",
                symbol=kw["symbol"],
                market_type=kw["market_type"],
                direction=kw["direction"],
                order_type=kw["order_type"],
                quantity=kw["quantity"],
                price=kw["price"],
                execution_mode=kw["execution_mode"],
                exchange_order_id="x1",
                status="filled",
            )
        )
        repo.insert_fill(
            Fill(
                id="f1",
                order_id="o1",
                fill_price=kw["price"],
                fill_quantity=kw["quantity"],
            )
        )
        repo.insert_position(
            Position(
                id="p1",
                proposal_id=kw["proposal_id"],
                order_id="o1",
                symbol=kw["symbol"],
                market_type=kw["market_type"],
                direction=kw["direction"],
                quantity=kw["quantity"],
                entry_price=kw["price"],
                execution_mode=kw["execution_mode"],
            )
        )
        return {
            "order_id": "o1",
            "exchange_order_id": "x1",
            "order_status": "filled",
            "fill_id": "f1",
            "position_id": "p1",
        }

    monkeypatch.setattr(
        broker,
        "execute_order",
        _persisted_exchange_result,
    )

    result = gateway.submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0011, price=2000, execution_mode="bitget_demo",
    )

    assert result["status"] == "executed"
    assert seen_by_risk == [("preflight", 0.0011), ("authoritative", 0.003)]
    assert sent == [0.003]


def test_unavailable_rules_reject_before_any_exchange_order(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway

    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _p, *, decision_role: _approved(),
    )
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda *args: (0.001, "Executable venue order rules are unavailable or do not support this order. No order was sent."))
    import aletheia.execution.coordinator as broker
    monkeypatch.setattr(broker, "execute_order", lambda **kw: (_ for _ in ()).throw(AssertionError("exchange must not be called")))

    result = gateway.submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, price=2000, execution_mode="bitget_demo",
    )

    assert result["status"] == "rejected"
    assert "No order was sent" in result["message"]


def test_registry_symbol_availability_speaks_the_execution_vocabulary(e2e_db=None):
    """A synced venue must not report "no usdt_futures products" for its perps.

    `universe_registry.market_type` is free TEXT and production populates it with
    the universe vocabulary ("perp"/"spot") — verified on the paper runtime, whose
    registry holds binance perp=723 / spot=490. Every consumer of
    `load_registry_symbols` indexes that cache with the *execution* vocabulary
    ("usdt_futures"/"spot") carried by Proposals and Orders, so without a
    translation `_check_symbol_availability` rejects every futures entry on any
    venue that has been synced at all.

    This was latent rather than visible: a venue with no registry rows returns
    None and fails open, so the trap only springs once that venue is wired — which
    is exactly what pointing the runtime at a registry-based (live) mode does.
    """
    from aletheia.execution import gateway, venue_capabilities
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseAsset

    venue_capabilities._registry_symbol_cache.clear()
    repo.upsert_universe_asset(
        UniverseAsset(
            id="asset-reg-perp",
            venue="bitget",
            symbol="BTC/USDT",
            market_type="perp",
            min_notional=1.0,
            price_precision=4,
            amount_precision=6,
        )
    )

    registry = venue_capabilities.load_registry_symbols("bitget")
    assert registry is not None
    assert "usdt_futures" in registry, registry
    assert "BTC/USDT" in registry["usdt_futures"]

    # bitget_live_limited is registry-based (non-sandbox); bitget_demo now reads
    # the profile-aware demo capability cache instead of the universe registry.
    assert gateway._check_symbol_availability(
        "BTC/USDT", "bitget_live_limited", "usdt_futures"
    ) is None

    # A symbol the venue does not list is still rejected: the translation must not
    # turn the gate into a rubber stamp.
    venue_capabilities._registry_symbol_cache.clear()
    assert gateway._check_symbol_availability(
        "DOGE/USDT", "bitget_live_limited", "usdt_futures"
    ) is not None
