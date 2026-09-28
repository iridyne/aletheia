"""Pipeline: exchange-mode position → protective trigger → broker close → PnL.

Offline by construction, following the established e2e convention: the autouse
`_mock_exchange` fixture blocks `net.make_ccxt`, and the venue is represented by
the same UTA adapter double the coordinator tests use
(`tests/uta/test_execution_coordinator._Adapter`). No network, no recorded
fixtures, no credentials.

Routing under test — `bitget_demo`:

    evaluate_open_positions()
      → close_position_routed()            # single source of truth for routing
      → gateway.close_exchange_position()  # exchange modes only
      → coordinator.close_position()       # claims, submits, materializes
"""

from tests.helpers.repo_test_helpers import list_outcome_hook_jobs

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.helpers.fill_reports import reports
_MODE = "bitget_demo"


def _prepare_venue(monkeypatch, *, fill_price: float, evidence=None, reason_code=None):
    """Wire an adapter double that accepts the close and reports one exact fill."""
    from datetime import datetime as _dt

    from aletheia.execution import coordinator
    from aletheia.uta.models import CommandEvidence, SubmissionReport
    from tests.uta.test_execution_coordinator import _Adapter, _identity

    submitted: list[object] = []

    async def submit(command):
        submitted.append(command)
        return SubmissionReport(
            identity=command.expected_account,
            command_id=command.command_id,
            # ACKNOWLEDGED is the only evidence value that means "the venue took
            # it and gave us a stable identity". ACCEPTED belongs to
            # OrderLifecycle, not CommandEvidence — using it would leave the claim
            # at `submission_unknown` with no exchange_order_id, and the private
            # fill could then never be matched to the order.
            evidence=evidence or CommandEvidence.ACKNOWLEDGED,
            reason_code=reason_code,
            received_at=_dt.now(timezone.utc),
            client_order_id=command.client_order_id,
            venue_order_id=f"venue-close-{len(submitted)}",
            profile_version="1",
        )

    adapter = _Adapter(submit, _MODE)
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)
    monkeypatch.setattr(
        "aletheia.execution.order_constraints.prepare_exchange_quantity",
        lambda mode, symbol, market, qty, price: qty,
    )

    def private_fill(order_row: dict, *, fee="0.01", fee_coin="USDT"):
        row = {
            "orderId": order_row["exchange_order_id"],
            "clientOid": order_row["client_order_id"],
            "tradeId": f"private-fill-{order_row['id']}",
            "fillPrice": str(fill_price),
            "fillQuantity": str(order_row["quantity"]),
            "feeCoin": fee_coin,
            "cTime": str(int(datetime.now(timezone.utc).timestamp() * 1000) - 1_000),
        }
        if fee is not None:
            row["fee"] = fee
        return reports([row], symbol=order_row["symbol"], market_type=order_row["market_type"])[0]

    return adapter, submitted, private_fill, _identity


def _seed_exchange_position(
    *,
    direction: str,
    entry_price: float,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    opened_hours_ago: float = 2.0,
) -> Position:
    """Seed an open exchange position bound to a filled, account-proven entry Order.

    The entry Order must carry `uta_account_identity_json`: the coordinator
    derives the close's account from the entry, and reconciliation fail-closes an
    Order that cannot prove its account
    (execution_contract.md — 提交前持久化 exact `uta_account_identity_json`).
    """
    from aletheia.storage.models import Order
    from tests.uta.test_execution_coordinator import _identity

    entry = Order(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction=direction,
        order_type="market",
        quantity=1.0,
        price=entry_price,
        execution_mode=_MODE,
        status="filled",
        source="adopted",
        uta_account_identity_json=_identity(_MODE).model_dump_json(),
    )
    repo.insert_order(entry)
    position = Position(
        order_id=entry.id,
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction=direction,
        quantity=1.0,
        entry_price=entry_price,
        current_price=entry_price,
        execution_mode=_MODE,
        status="open",
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        opened_at=(
            datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)
        ).isoformat(),
    )
    repo.insert_position(position)
    return position


def _patch_protective_mark(monkeypatch, price: float):
    """Inject the fresh mark at the seam the close path actually reads.

    `position_manager` reads a provenance-bearing mark for the exact venue,
    symbol and market; inject that same evidence into protective-close reads.
    """
    import aletheia.data.price_feed as price_feed
    import aletheia.portfolio.position_manager as pm

    def mark(symbol, market_type, *, venue, not_before=None):
        return {
            "price": price,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "age_seconds": 0.0,
            "source": "e2e_stub",
            "venue": venue,
            "symbol": symbol,
            "market_type": market_type,
        }

    for module in (price_feed, pm):
        monkeypatch.setattr(
            module, "get_fresh_mark_for_protective_close", mark, raising=False
        )
        monkeypatch.setattr(
            module,
            "get_fresh_price_for_protective_close",
            lambda symbol, market_type, *, venue: price,
            raising=False,
        )


def _settle_pending_close(position_id: str, private_fill) -> None:
    """Materialize the claimed close from exact private fill evidence."""
    from aletheia.execution.exchange_materialization import materialize_bitget_close

    pending = [
        row for row in repo.list_orders(limit=50)
        if row["position_id"] == position_id and row.get("close_reason")
    ]
    assert len(pending) == 1, f"expected exactly one close claim, got {pending}"
    result = materialize_bitget_close(pending[0]["id"], [private_fill(pending[0])])
    assert result.state == "filled", result


@pytest.mark.parametrize(
    ("direction", "entry", "stop", "mark", "reason"),
    (
        pytest.param("long", 100.0, 98.0, 97.5, "stop_loss", id="long-stop-loss"),
        pytest.param("short", 100.0, 102.0, 102.5, "stop_loss", id="short-stop-loss"),
    ),
)
def test_exchange_stop_loss_closes_through_the_broker(
    e2e_db, monkeypatch, direction, entry, stop, mark, reason
):
    """A breached stop on an exchange position closes via the broker, not locally.

    The local-only path would leave a dangling real venue position and fabricate
    the exit from a local mark; `close_position_routed` exists to make that
    impossible. This asserts the routing actually holds for `bitget_demo`.
    """
    _, submitted, private_fill, _ = _prepare_venue(monkeypatch, fill_price=mark)
    position = _seed_exchange_position(
        direction=direction, entry_price=entry, stop_price=stop
    )
    _patch_protective_mark(monkeypatch, mark)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    result = evaluate_open_positions()

    # The broker was asked to reduce the real position — the whole point of
    # routing exchange modes away from the local close.
    assert submitted, (
        "no close command reached the venue adapter — the exchange position would "
        f"be left dangling. result={result}"
    )
    claim = repo.get_position(position.id)
    assert claim["pending_close_order_id"], "close was not claimed durably"

    _settle_pending_close(position.id, private_fill)

    closed = repo.get_position(position.id)
    assert closed["status"] == "closed"
    assert closed["close_reason"] == reason
    # Exit economics come from the venue fill, never from the local mark.
    assert closed["exit_price_source"] == "broker_fill"
    assert closed["exit_fill_verified"] == 1
    assert closed["current_price"] == pytest.approx(mark)


def test_exchange_take_profit_closes_through_the_broker(e2e_db, monkeypatch):
    _, submitted, private_fill, _ = _prepare_venue(monkeypatch, fill_price=110.0)
    position = _seed_exchange_position(
        direction="long", entry_price=100.0, stop_price=95.0, take_profit_price=108.0
    )
    _patch_protective_mark(monkeypatch, 110.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()
    assert submitted, "take-profit did not reach the venue adapter"

    _settle_pending_close(position.id, private_fill)

    closed = repo.get_position(position.id)
    assert closed["status"] == "closed"
    assert closed["close_reason"] == "take_profit"
    assert closed["exit_price_source"] == "broker_fill"
    assert closed["current_price"] == pytest.approx(110.0)
    # The exit fee is a real venue fact and is recorded.
    assert closed["exit_fee"] == pytest.approx(0.01)
    # A usdt_futures close with no funding ledger keeps `realized_pnl` NULL rather
    # than treating unknown funding as zero. This is the contracted honesty rule
    # (DECISIONS.md 2026-07-10, "Opportunity 结果以固定时点净收益为学习事实":
    # 永续合约缺入场时资金费时，会显示证据不完整，而不是把资金费当成 0), and it is
    # why this test does NOT assert a positive PnL — doing so would demand the
    # fabrication the rule forbids.
    assert closed["funding_source"] == "unavailable"
    assert closed["realized_pnl"] is None, (
        "realized_pnl was computed without funding evidence — unknown funding must "
        "not be settled as zero"
    )


def test_live_limited_position_is_never_auto_closed(e2e_db, monkeypatch):
    """The autonomous loop must refuse a live position even when the stop is breached.

    TWO independent defences, asserted separately, because the first one hides the
    second and an earlier version of this test only proved the hiding:

    1. `evaluate_open_positions` filters on `_DEMO_MODES`
       (`{bitget_demo, binance_testnet, okx_demo}`), so a
       `*_live_limited` position is never even *checked* — `checked == 0`.
       Asserting only "no command reached the venue" therefore passes trivially
       and would keep passing with the second defence removed.
    2. `close_position_routed` raises for a `*_live_limited` mode unless an
       explicit human caller passes `allow_live=True`. This is the real
       chokepoint, and it is reached only by direct invocation — which is what
       the second half of this test does.

    Verified by mutation: flipping `allow_live`'s default to True must make this
    test fail. It did not, before defence 2 was asserted directly.
    """
    from aletheia.storage.models import Order
    from tests.uta.test_execution_coordinator import _identity

    live_mode = "bitget_live_limited"
    entry = Order(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        order_type="market", quantity=1.0, price=100.0,
        execution_mode=live_mode, status="filled", source="adopted",
        uta_account_identity_json=_identity(live_mode).model_dump_json(),
    )
    repo.insert_order(entry)
    position = Position(
        order_id=entry.id, symbol="SOL/USDT", market_type="usdt_futures",
        direction="long", quantity=1.0, entry_price=100.0, current_price=100.0,
        execution_mode=live_mode, status="open", stop_price=98.0,
        opened_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    repo.insert_position(position)
    _patch_protective_mark(monkeypatch, 97.0)

    submitted: list[object] = []

    from aletheia.execution import coordinator

    def forbidden(_mode):
        submitted.append(_mode)
        raise AssertionError("autonomous loop must never reach a live venue")

    monkeypatch.setattr(coordinator, "create_uta_adapter", forbidden)

    from aletheia.portfolio.position_manager import (
        close_position_routed,
        evaluate_open_positions,
    )

    # Defence 1: the loop does not even consider a live position.
    result = evaluate_open_positions()
    assert result["checked"] == 0, (
        "a live position entered the auto-exit working set; _PAPER_MODES should "
        f"have excluded it. result={result}"
    )
    assert submitted == [], "a live close command reached the venue"
    assert repo.get_position(position.id)["status"] == "open"

    # Defence 2: the router itself refuses, so any future caller that reaches it
    # without explicit human intent still cannot place a REAL order. Asserted by
    # direct invocation because defence 1 makes this unreachable from the loop.
    with pytest.raises(RuntimeError, match="live closes are human-only"):
        close_position_routed(position.id, "stop_loss")

    assert submitted == [], "the refused live close still reached the venue"
    assert repo.get_position(position.id)["status"] == "open"


@pytest.mark.parametrize(
    ("evidence_name", "expected_status", "expected_reason"),
    (
        pytest.param(
            "AMBIGUOUS",
            "submission_unknown",
            "command_acknowledgement_unknown",
            id="acknowledgement-unknown",
        ),
        pytest.param("NOT_SENT", "error", "command_not_sent", id="pre-submit-failure"),
    ),
)
def test_unacknowledged_close_records_its_own_uncertainty(
    e2e_db, monkeypatch, evidence_name, expected_status, expected_reason
):
    """The claim must record *which* kind of not-closed this is.

    Asserting only "the Position stayed open" does not distinguish a correct
    refusal from a broken one: a non-acknowledged close leaves no fill evidence
    either way, so the Position stays open even if the outcome were misread as an
    acknowledgement. The distinguishing fact lives on the claim — `error` means the
    command never left, `submission_unknown` means we cannot tell and nothing may
    retry — so that is what this asserts.
    """
    from aletheia.uta.models import CommandEvidence

    _, submitted, _, _ = _prepare_venue(
        monkeypatch, fill_price=97.5, evidence=getattr(CommandEvidence, evidence_name)
    )
    position = _seed_exchange_position(
        direction="long", entry_price=100.0, stop_price=98.0
    )
    _patch_protective_mark(monkeypatch, 97.5)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()

    stored = repo.get_position(position.id)
    assert stored["status"] == "open"
    assert stored["closed_at"] is None
    # The mark that triggered the close must never become a verified exit price.
    assert stored["exit_price_source"] != "broker_fill"
    assert stored["exit_fill_verified"] in (0, None)

    claim_id = stored["pending_close_order_id"]
    assert claim_id, "the attempt must stay durable so reconciliation can own it"
    claim = repo.get_order(claim_id)
    assert claim["status"] == expected_status
    assert claim["error_reason"] == expected_reason
    assert repo.list_fills(order_id=claim_id) == []


def test_close_refuses_a_position_whose_entry_cannot_prove_its_account(
    e2e_db, monkeypatch
):
    """A Position whose entry Order proves no account is not closable.

    Driven through `close_position_routed` directly rather than through
    `evaluate_open_positions`: the protective-trigger path declines such a Position
    earlier for its own reasons, so routing through it would assert nothing about
    the account proof. `coordinator._position_account` is the gate under test — it
    derives the close's account from the entry Order and must refuse rather than
    guess (execution_contract.md:235 — 历史 NULL 不按当前配置补造身份).

    The refusal that actually fires is the identity *parse* of a NULL carrier,
    ahead of the explicit `uta_position_account_unavailable` raise. Recorded here
    because mutating that later raise leaves this test green, while making
    `_position_account` synthesise an identity from current configuration for a
    NULL carrier fails it — which is exactly what the contract line forbids.
    """
    from datetime import datetime as _dt

    from aletheia.storage.models import Order, Position

    _, submitted, _, _ = _prepare_venue(monkeypatch, fill_price=97.5)

    orphan_entry = Order(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode=_MODE,
        status="filled",
        source="adopted",
    )
    repo.insert_order(orphan_entry)
    orphan = Position(
        order_id=orphan_entry.id,
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode=_MODE,
        status="open",
        opened_at=(_dt.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    repo.insert_position(orphan)
    assert repo.get_order(orphan_entry.id)["uta_account_identity_json"] is None

    from aletheia.portfolio.position_manager import close_position_routed

    with pytest.raises(Exception) as raised:
        close_position_routed(orphan.id, reason="stop_loss")
    # Either the parse refuses the NULL carrier or the explicit guard fires;
    # what must never happen is an identity conjured from current configuration.
    message = str(raised.value)
    assert "UTAAccountIdentity" in message or "account" in message.lower(), message

    unchanged = repo.get_position(orphan.id)
    assert unchanged["status"] == "open"
    assert unchanged["pending_close_order_id"] is None
    assert submitted == [], "a close was sent for an account we cannot prove"


def test_venue_rejected_close_records_the_rejection_and_keeps_the_position(
    e2e_db, monkeypatch
):
    """A close the venue explicitly refused is recorded as refused, not as closed."""
    from aletheia.uta.models import CommandEvidence

    _, submitted, _, _ = _prepare_venue(
        monkeypatch,
        fill_price=97.5,
        evidence=CommandEvidence.VENUE_REJECTED,
        reason_code="reduce_only_rejected",
    )
    position = _seed_exchange_position(
        direction="long", entry_price=100.0, stop_price=98.0
    )
    _patch_protective_mark(monkeypatch, 97.5)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()

    stored = repo.get_position(position.id)
    assert stored["status"] == "open"
    assert stored["exit_price_source"] != "broker_fill"
    # Unconditional: a claim that vanished would skip every assertion below and
    # let the test pass while the venue's reason and the durable retry/audit state
    # were lost. The attempt has to stay durable for reconciliation to own it.
    claim_id = stored["pending_close_order_id"]
    assert claim_id, "the rejected attempt must stay durable"
    claim = repo.get_order(claim_id)
    assert claim is not None
    # The venue's own reason must survive; "we tried and it said no" is a
    # different fact from "we could not tell".
    assert claim["error_reason"] == "reduce_only_rejected"
    assert claim["status"] != "filled"
    assert repo.list_fills(order_id=claim_id) == []


@pytest.mark.parametrize(
    ("fee", "fee_coin"),
    (
        pytest.param(None, "USDT", id="fee-absent"),
        pytest.param("0.01", "BGB", id="fee-third-currency"),
        pytest.param("0.01", "", id="fee-currency-absent"),
    ),
)
def test_close_with_uncostable_fee_is_not_settled_as_complete(
    e2e_db, monkeypatch, fee, fee_coin
):
    """Accepted #3135: verified execution closes; unresolved costs are not final PnL."""
    from aletheia.execution.exchange_materialization import materialize_bitget_close

    _, submitted, private_fill, _ = _prepare_venue(monkeypatch, fill_price=97.5)
    position = _seed_exchange_position(
        direction="long", entry_price=100.0, stop_price=98.0
    )
    _patch_protective_mark(monkeypatch, 97.5)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()
    claim_id = repo.get_position(position.id)["pending_close_order_id"]
    assert claim_id

    row = private_fill(repo.get_order(claim_id), fee=fee, fee_coin=fee_coin)

    result = materialize_bitget_close(claim_id, [row])
    stored = repo.get_position(position.id)
    assert result.state == "filled"
    assert stored["status"] == "closed"
    assert stored["closed_at"] is not None
    assert stored["exit_fill_verified"] == 1
    assert stored["pending_close_order_id"] is None
    assert stored["exit_fee"] is None
    assert stored["realized_pnl"] is None
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_realized_economics"
    [fill] = repo.list_fills(order_id=claim_id)
    assert fill["fee"] == (float(fee) if fee is not None else None)
    assert fill["fee_currency"] == fee_coin
    assert fill["fee_source"] == ("reconciled" if fee is not None else "unavailable")
    assert list_outcome_hook_jobs(position_id=position.id) == []
    repeated = materialize_bitget_close(claim_id, [row])
    assert repeated.new_fill_count == 0
    assert len(repo.list_fills(order_id=claim_id)) == 1
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_close_events WHERE position_id=?", (position.id,),
    ).fetchone()[0] == 1
