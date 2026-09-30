"""Private evidence reconciles account-bound orders; open-list absence proves nothing."""

import sqlite3

import pytest

from tests.helpers.position_fixture import force_update_position

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, Position
from aletheia.uta.models import UTAAccountIdentity
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry

ACCOUNT = UTAAccountIdentity(
    account_id="bitget_live_limited", execution_mode="bitget_live_limited",
    venue="bitget", environment="live", profile="classic_v2",
    credential_generation=1, adapter_implementation="CCXTUTAAdapter", adapter_version="1",
)

def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    registry = UTARegistry()
    registry.register(UTAAdapterConfig(identity=ACCOUNT), lambda _config: None)
    monkeypatch.setattr("aletheia.execution.reconciliation.build_uta_registry", lambda: registry)

def _patch_open_orders(monkeypatch, open_ids, fill_rows=()):
    from aletheia.execution import reconciliation as recon
    from tests.helpers.fill_reports import reports

    def typed_rows(market_type):
        rows = []
        for index, row in enumerate(fill_rows):
            item = dict(row)
            item.setdefault("tradeId", f"test-fill-{index}")
            item.setdefault("fillPrice", "60000")
            item.setdefault("fee", "0")
            item.setdefault("feeCoin", "USDT")
            item.setdefault("cTime", "1789171200000")
            rows.extend(
                reports(
                    [item],
                    identity=ACCOUNT,
                    market_type=market_type,
                )
            )
        return rows

    async def fake_open_orders(self, market_type="spot"):
        return [{"orderId": oid} for oid in open_ids]

    async def fake_fills(self, symbol=None, market_type="spot", start_at=None):
        return typed_rows(market_type)

    async def fake_order_fills(self, **kwargs):
        return typed_rows(kwargs.get("market_type", "spot"))

    monkeypatch.setattr(
        recon._UTAReconciliationProjection, "get_open_orders", fake_open_orders
    )
    monkeypatch.setattr(recon._UTAReconciliationProjection, "get_fills", fake_fills)
    monkeypatch.setattr(
        recon._UTAReconciliationProjection,
        "get_order_fills",
        fake_order_fills,
    )


def test_submitted_order_no_longer_open_marked_filled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{
            "orderId": "EX-1",
            "fillQuantity": "0.001",
        }],  # private history directly confirms the complete fill
    )

    order = Order(source="adopted",
    symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
    quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-1", status="submitted",)
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=60000, fill_quantity=0.001, fee=0.036,
    ))
    repo.insert_position(Position(
        proposal_id=order.proposal_id,
        order_id=order.id,
        symbol=order.symbol,
        market_type=order.market_type,
        direction=order.direction,
        quantity=order.quantity,
        entry_price=order.price,
        current_price=order.price,
        execution_mode=order.execution_mode,
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    assert result["filled"] == 1

    orders = repo.list_orders()
    assert orders[0]["status"] == "filled"


def test_missing_open_order_without_private_fill_stays_unresolved(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=[], fill_rows=[])

    repo.insert_order(Order(source="adopted",
    symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
    quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-NO-EVIDENCE", status="submitted",))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.list_orders()[0]["status"] == "submitted"


def test_exchange_fill_without_local_materialization_stays_unresolved(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-REMOTE-ONLY", "fillQuantity": "0.001"}],
    )

    order = Order(source="adopted",
    symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
    quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-REMOTE-ONLY", status="materialization_missing",)
    repo.insert_order(order)

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "materialization_missing"
    audit = repo.list_audit_events(limit=1)[0]
    assert audit["event_type"] == "order_reconciliation_inconclusive"
    assert "exchange_fill_seen_local_materialization_missing" in audit["after_state"]


def test_incomplete_private_fill_does_not_invent_full_fill(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-PARTIAL-HISTORY", "fillQuantity": "0.0004"}],
    )

    repo.insert_order(Order(source="adopted",
    symbol="BTC/USDT", market_type="spot", direction="long", order_type="limit",
    quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-PARTIAL-HISTORY", status="partially_filled",))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.list_orders()[0]["status"] == "partially_filled"


def test_remote_full_fill_does_not_hide_partial_local_materialization(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-LOCAL-PARTIAL", "fillQuantity": "0.001"}],
    )
    order = Order(source="adopted",
    symbol="BTC/USDT", market_type="spot", direction="long", order_type="limit",
    quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-LOCAL-PARTIAL", status="partially_filled",)
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=60000, fill_quantity=0.0004, fee=0.0144,
    ))
    repo.insert_position(Position(
        order_id=order.id,
        symbol=order.symbol,
        market_type=order.market_type,
        direction=order.direction,
        quantity=0.0004,
        entry_price=order.price,
        current_price=order.price,
        execution_mode=order.execution_mode,
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "partially_filled"


def test_averaging_entry_uses_direct_position_identity(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-AVERAGING", "fillQuantity": "0.001"}],
    )
    position = Position(
        order_id="original-entry-order",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.011,
        entry_price=60000,
        current_price=60000,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(position)
    order = Order(
        source="adopted",
        position_id=position.id,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=0.001,
        price=60000,
        execution_mode=position.execution_mode,
        uta_account_identity_json=ACCOUNT.model_dump_json(),
        exchange_order_id="EX-AVERAGING",
        status="materialization_missing",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=60000, fill_quantity=0.001, fee=0.036,
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 1
    assert repo.get_order(order.id)["status"] == "filled"


def test_close_direct_position_identity_requires_terminal_position(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-CLOSE-DIRECT", "fillQuantity": "0.01"}],
    )
    position = Position(
        order_id="entry-order",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100,
        current_price=101,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(position)
    order = Order(
        source="adopted",
        client_order_id=f"close-{position.id}",
        position_id=position.id,
        close_reason="stop_loss",
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=position.quantity,
        price=position.current_price,
        execution_mode=position.execution_mode,
        uta_account_identity_json=ACCOUNT.model_dump_json(),
        exchange_order_id="EX-CLOSE-DIRECT",
        status="materialization_missing",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=101, fill_quantity=0.01, fee=0.000606,
    ))

    from aletheia.execution.reconciliation import reconcile
    first = reconcile()

    assert first["filled"] == 0
    assert first["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "materialization_missing"

    force_update_position(position.id, status="closed")
    second = reconcile()

    assert second["filled"] == 1
    assert repo.get_order(order.id)["status"] == "filled"


def test_still_open_order_left_alone(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=["EX-2"])  # still open on exchange

    repo.insert_order(Order(source="adopted",
    symbol="ETH/USDT", market_type="spot", direction="long", order_type="limit",
    quantity=1.0, price=3000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-2", status="submitted",))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    assert result["filled"] == 0
    assert result["inconclusive"] == 0
    assert repo.list_orders()[0]["status"] == "submitted"


def test_archived_orders_are_ignored_without_mutation(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=[])

    order = Order(
        source="adopted",
        symbol="SOL/USDT", market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=150, execution_mode="paper_internal", status="filled",
    )
    repo._insert("orders", order.model_dump())
    before = repo.get_order(order.id)
    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    # Historical rows are readable but excluded from exchange reconciliation.
    assert result["filled"] == 0
    assert result["checked"] == 0
    assert repo.get_order(order.id) == before


def test_reconciliation_scans_all_unresolved_orders_without_fixed_limit(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    order_ids = [f"EX-BULK-{index}" for index in range(501)]
    _patch_open_orders(monkeypatch, open_ids=order_ids)
    for exchange_order_id in order_ids:
        repo.insert_order(Order(source="adopted",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="limit", quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id=exchange_order_id, status="submitted",))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["checked"] == 501

@pytest.mark.parametrize("stored_identity", [None, ACCOUNT.model_copy(update={"credential_generation": 0}).model_dump_json()])
def test_unmapped_or_changed_account_preserves_order_without_private_reads(tmp_path, monkeypatch, stored_identity):
    setup_env(tmp_path, monkeypatch)
    from aletheia.execution import reconciliation as recon

    order = Order(source="adopted", symbol="BTC/USDT", market_type="spot", direction="long",
                  order_type="market", quantity=1, execution_mode="bitget_live_limited",
                  exchange_order_id="historical", status="submission_unknown",
                  uta_account_identity_json=stored_identity)
    repo.insert_order(order)
    async def unexpected_read(*_args, **_kwargs):
        pytest.fail("unmapped order must not query the current private account")
    monkeypatch.setattr(recon, "query_uta", unexpected_read)
    result = recon.reconcile()
    assert result["checked"] == 1
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "submission_unknown"
    assert repo.get_order(order.id)["uta_account_identity_json"] == stored_identity


@pytest.mark.parametrize("complete", [True, False])
def test_typed_open_order_recovers_venue_identity(tmp_path, monkeypatch, complete):
    from datetime import datetime, timezone
    from aletheia.execution import reconciliation as recon
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import (
        FillListReport, OrderListReport, OrderStatusReport, ReadCompleteness,
    )

    setup_env(tmp_path, monkeypatch)
    order = Order(
        source="adopted", symbol="BTC/USDT", market_type="spot",
        direction="long", order_type="limit", quantity=0.001, price=60000,
        execution_mode=ACCOUNT.execution_mode,
        uta_account_identity_json=ACCOUNT.model_dump_json(),
        client_order_id="lost-ack-client", status="submitted",
    )
    repo.insert_order(order)
    now = datetime.now(timezone.utc)

    async def query(_mode, operation, request, **_kwargs):
        envelope = dict(identity=ACCOUNT, scope=request.scope, received_at=now,
                        profile_version="1", completeness=ReadCompleteness.COMPLETE)
        if operation == "get_open_orders":
            if not complete:
                envelope.update(completeness=ReadCompleteness.PARTIAL,
                                reason_code="page_limit")
            return OrderListReport(**envelope, orders=(OrderStatusReport(
                identity=ACCOUNT, instrument=instrument_from_local("BTC/USDT", "spot"),
                client_order_id=order.client_order_id, venue_order_id="VENUE-OPEN",
                side="buy", order_type="limit", original_quantity="0.001",
                filled_quantity="0", state="open", received_at=now,
            ),))
        assert operation == "get_fills"
        return FillListReport(**envelope)

    monkeypatch.setattr(recon, "query_uta", query)
    result = recon.reconcile()
    stored = repo.get_order(order.id)
    assert stored["exchange_order_id"] == ("VENUE-OPEN" if complete else None)
    assert stored["status"] == "submitted"
    assert result["inconclusive"] == (0 if complete else 1)
    assert result["filled"] == 0
    assert bool(result["errors"]) is not complete


def test_materialization_missing_transition_failure_shape_is_pinned():
    """Static regression: `transition_order()` failure in the
    materialization-missing branch must NOT go on to write a
    `local_materialization_failed` audit event.

    Prior behaviour was ``except Exception: pass`` followed unconditionally
    by ``_write_reconcile_event(order, state="materialization_missing",
    reason="local_materialization_failed")`` — that split audit truth from
    database truth (audit claimed the state was reached, DB rejected the
    transition).

    Runtime pinning requires an end-to-end reconcile fixture (Bitget
    fill-history path + monkeypatched ``transition_order``) that this test
    file's helpers do not yet expose; the follow-up integration test is
    out of scope here. This static shape check catches any regression that
    reintroduces the silent ``except: pass`` pattern in the two known
    sites.
    """
    from pathlib import Path
    source = Path(__file__).resolve().parent.parent.joinpath(
        "core/aletheia/execution/reconciliation.py"
    ).read_text(encoding="utf-8")

    # The two sibling blocks both wrap `transition_order(... status=
    # "materialization_missing" ...)`. After PR #3XXX they name the exception
    # (`except Exception as transition_exc`) and record it in `errors` +
    # only write the audit event inside an `else:` branch.
    assert source.count("except Exception as transition_exc") >= 2, (
        "both materialization_missing transition sites must name their "
        "exception (regression: silent `except: pass` is back)"
    )
    assert source.count("materialization_missing_transition_failed") >= 2, (
        "both sites must append a stable error code to `errors` when the "
        "transition fails"
    )
    # The audit-write failure branch itself is caught, so an audit-sink
    # exception cannot mutate order state or bubble up.
    assert source.count(
        "reconcile: materialization failure audit write failed"
    ) >= 2, (
        "both sites must catch audit-sink failure separately (not roll back "
        "the transition, per execution_contract.md §7)"
    )


def test_order_lookup_start_uses_order_creation_with_skew():
    """#3564: a clientOid history read needs a window start, or the venue profile
    answers with a structural PARTIAL and the order never converges."""
    from datetime import datetime, timedelta, timezone

    from aletheia.execution.reconciliation import _ORDER_LOOKUP_SKEW, _order_lookup_start

    created = "2026-09-27T12:00:00+00:00"
    assert _order_lookup_start({"created_at": created}) == (
        datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc) - _ORDER_LOOKUP_SKEW
    )
    # Naive timestamps are read as UTC rather than silently dropped.
    naive = _order_lookup_start({"created_at": "2026-09-27T12:00:00"})
    assert naive == datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc) - _ORDER_LOOKUP_SKEW
    # No usable timestamp: return None so the adapter reports its OWN structural
    # PARTIAL instead of this code inventing a window.
    assert _order_lookup_start({}) is None
    assert _order_lookup_start({"created_at": "not-a-timestamp"}) is None


async def test_client_oid_detail_lookup_passes_a_window_start(monkeypatch):
    """#3564: the call site must thread the order's own creation into the scope."""
    from aletheia.execution import reconciliation as recon

    captured = {}

    async def fake_query_uta(execution_mode, method, query, **kwargs):
        captured["query"] = query
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr(recon, "query_uta", fake_query_uta)
    projection = recon._UTAReconciliationProjection("bitget_demo", expected_account=ACCOUNT)
    with pytest.raises(RuntimeError):
        await projection.get_order_detail_by_client_oid(
            "client-oid-1", "UNI/USDT", start_at=recon._order_lookup_start(
                {"created_at": "2026-09-27T12:00:00+00:00"}
            )
        )
    assert captured["query"].scope.start_at is not None, (
        "without a window start the venue returns private_order_history_start_required "
        "forever (#3564)"
    )


def _patch_symbol_only_fill_venue(monkeypatch, *, symbol_rows, symbol_read_fails=False):
    """A venue whose adapter answers account-wide fill reads UNSUPPORTED (#3564)."""
    from aletheia.execution import reconciliation as recon
    from aletheia.execution.errors import IncompleteReadError
    from aletheia.uta.models import ReadCompleteness
    from tests.helpers.fill_reports import reports

    calls = []

    async def fake_open_orders(self, market_type="spot"):
        return []

    async def fake_fills(self, symbol=None, market_type="spot", start_at=None):
        calls.append(symbol)
        if symbol is None:
            raise IncompleteReadError(
                "private_fill_symbol_required", "get_fills", self.execution_mode,
                market_type, completeness=ReadCompleteness.UNSUPPORTED,
            )
        if symbol_read_fails:
            raise IncompleteReadError(
                "private_fills_unavailable", "get_fills", self.execution_mode,
                market_type, symbol, completeness=ReadCompleteness.UNAVAILABLE,
            )
        rows = [
            {"tradeId": f"t-{i}", "fillPrice": "60000", "fee": "0", "feeCoin": "USDT",
             "cTime": "1789171200000", **row}
            for i, row in enumerate(symbol_rows)
        ]
        return reports(rows, identity=ACCOUNT, market_type=market_type)

    monkeypatch.setattr(recon._UTAReconciliationProjection, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(recon._UTAReconciliationProjection, "get_fills", fake_fills)
    return calls


def _materialized_filled_order():
    order = Order(source="adopted",
    symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
    quantity=0.001, price=60000, execution_mode="bitget_live_limited", uta_account_identity_json=ACCOUNT.model_dump_json(), exchange_order_id="EX-SYM", status="submitted",)
    repo.insert_order(order)
    repo.insert_fill(Fill(order_id=order.id, fill_price=60000, fill_quantity=0.001, fee=0.036))
    repo.insert_position(Position(
        proposal_id=order.proposal_id, order_id=order.id, symbol=order.symbol,
        market_type=order.market_type, direction=order.direction, quantity=order.quantity,
        entry_price=order.price, current_price=order.price, execution_mode=order.execution_mode,
    ))
    return order


def test_unsupported_account_wide_fills_resolve_through_symbol_read_quietly(
    tmp_path, monkeypatch, caplog,
):
    """#3564: a venue that cannot read fills account-wide (Bitget) is not a failing
    read; the order is proven by its own symbol-scoped read, with no per-pass warning."""
    import logging

    setup_env(tmp_path, monkeypatch)
    calls = _patch_symbol_only_fill_venue(
        monkeypatch, symbol_rows=[{"orderId": "EX-SYM", "fillQuantity": "0.001"}],
    )
    order = _materialized_filled_order()

    from aletheia.execution.reconciliation import reconcile
    with caplog.at_level(logging.WARNING, logger="aletheia.reconciliation"):
        result = reconcile()

    assert calls[0] is None and calls[1:] == ["BTCUSDT"]
    assert result["filled"] == 1
    assert repo.get_order(order.id)["status"] == "filled"
    assert not [r for r in caplog.records if "default private fills failed" in r.getMessage()]


def test_unsupported_account_wide_fills_with_failed_symbol_read_stay_unresolved(
    tmp_path, monkeypatch,
):
    """Skipping the unsupported read never turns 'not read' into 'no fills'."""
    setup_env(tmp_path, monkeypatch)
    _patch_symbol_only_fill_venue(monkeypatch, symbol_rows=[], symbol_read_fails=True)
    order = _materialized_filled_order()

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "submitted"
