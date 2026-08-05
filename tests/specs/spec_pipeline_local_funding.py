"""Local-paper futures funding is complete, signed, and auditable.

These claims cover the simulator-only producer.  Exchange-backed futures keep
their separate broker-ledger requirement and are deliberately out of scope.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

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


def _iso(hour: int, minute: int = 0) -> str:
    return datetime(2026, 7, 14, hour, minute, tzinfo=timezone.utc).isoformat()


def _iso_ms(hour: int, minute: int = 0, millisecond: int = 0) -> str:
    return datetime(
        2026, 7, 14, hour, minute, tzinfo=timezone.utc,
        microsecond=millisecond * 1000,
    ).isoformat()


def _snapshot(*, rate: float, target_hour: int, observed_hour: int, observed_minute: int):
    from aletheia.storage import repositories as repo

    repo.insert_funding_rate_snapshot({
        "symbol": "BTC/USDT",
        "exchange": "okx",
        "funding_rate": rate,
        "funding_rate_annualized": rate * 3 * 365,
        "next_funding_time": _iso(target_hour),
        "observed_at": _iso(observed_hour, observed_minute),
    })


def _snapshot_at(*, rate: float, target: str, observed: str):
    from aletheia.storage import repositories as repo

    repo.insert_funding_rate_snapshot({
        "symbol": "BTC/USDT",
        "exchange": "okx",
        "funding_rate": rate,
        "funding_rate_annualized": rate * 3 * 365,
        "next_funding_time": target,
        "observed_at": observed,
    })


def _position(direction: str = "long") -> dict:
    position_id = f"position-{direction}"
    return {
        "id": position_id,
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "paper_internal",
        "direction": direction,
        "quantity": 2.0,
        "entry_price": 100.0,
        "proposal_id": f"proposal-{position_id}-0",
        "order_id": f"order-{position_id}-0",
        "opened_at": _iso(0, 30),
    }


def _insert_entry_lineage(
    position: dict,
    *,
    legs: list[tuple[float, float, str]] | None = None,
) -> None:
    """Persist the exact local entry Orders/Fills represented by a Position."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, RiskDecision, TradeProposal

    legs = legs or [(float(position["entry_price"]), float(position["quantity"]), _iso(0, 30))]
    for index, (fill_price, fill_quantity, filled_at) in enumerate(legs):
        proposal = TradeProposal(
            id=f"proposal-{position['id']}-{index}",
            symbol=str(position["symbol"]),
            market_type=str(position["market_type"]),
            direction=str(position["direction"]),
            order_type="market",
            quantity=fill_quantity,
            price=fill_price,
            execution_mode=str(position["execution_mode"]),
            status="executed",
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            id=f"risk-{position['id']}-{index}",
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode=proposal.execution_mode,
            proposal_execution_mode=proposal.execution_mode,
            decision_role="authoritative",
            evaluated_quantity=fill_quantity,
            proposal_commit_hash=proposal.commit_hash,
        )
        repo.insert_risk_decision(decision)
        order = Order(
            id=f"order-{position['id']}-{index}",
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            position_id=str(position["id"]),
            symbol=str(position["symbol"]),
            market_type=str(position["market_type"]),
            direction=str(position["direction"]),
            order_type="market",
            quantity=fill_quantity,
            price=fill_price,
            execution_mode=str(position["execution_mode"]),
            status="filled",
            filled_at=filled_at,
        )
        repo.insert_order(order)
        repo.insert_fill(Fill(
            id=f"fill-{position['id']}-{index}",
            order_id=order.id,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            fee=0.0,
            filled_at=filled_at,
        ))


def _complete_funding_evidence(position: dict, *, funding_cost: float = 0.1) -> dict:
    return {
        "status": "complete",
        "source": "local_simulation",
        "method": "active_entry_fill_notional_x_scheduled_rate",
        "position_id": position["id"],
        "symbol": position["symbol"],
        "opened_at": position["opened_at"],
        "closed_at": _iso(9),
        "events": [
            {
                "scheduled_at": _iso(4),
                "funding_rate": 0.0005,
                "active_notional": 200.0,
                "funding_cost": funding_cost,
            }
        ],
    }


@spec_claim(
    SpecClaim(
        "PL-LF-01",
        "pipeline_local_funding",
        "signed scheduled funding",
        "L2b",
        "Local paper futures aggregate each scheduled settlement after entry and "
        "through close using entry notional; a positive rate is a long cost and "
        "a short credit, with the exact snapshot events retained as evidence.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("direction", "expected"),
    [("long", 0.1), ("short", -0.1)],
)
def test_local_funding_aggregates_scheduled_events_with_directional_sign(
    tmp_path, monkeypatch, direction, expected,
):
    _setup_db(tmp_path, monkeypatch)

    # Opening schedule anchor, then the last observation before each settlement.
    _snapshot(rate=0.0008, target_hour=4, observed_hour=0, observed_minute=20)
    _snapshot(rate=0.0010, target_hour=4, observed_hour=3, observed_minute=59)
    # The collector sees the next schedule immediately after 04:00 and refreshes
    # its rate before 08:00.
    _snapshot(rate=-0.0004, target_hour=8, observed_hour=4, observed_minute=1)
    _snapshot(rate=-0.0005, target_hour=8, observed_hour=7, observed_minute=59)
    # This future target is the proof that no further settlement belongs to a
    # position closed at 09:00.
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=1)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=55)

    from aletheia.portfolio.local_funding import compute_local_funding

    position = _position(direction)
    _insert_entry_lineage(position)
    result = compute_local_funding(position, closed_at=_iso(9))

    assert result.complete is True
    assert result.funding_source == "local_simulation"
    assert result.funding_cost == pytest.approx(expected)
    assert [event["scheduled_at"] for event in result.evidence["events"]] == [
        _iso(4),
        _iso(8),
    ]
    assert [event["funding_rate"] for event in result.evidence["events"]] == [
        pytest.approx(0.0010),
        pytest.approx(-0.0005),
    ]
    assert result.evidence["next_settlement_anchor"]["scheduled_at"] == _iso(12)
    assert result.evidence["method"] == "active_entry_fill_notional_x_scheduled_rate"
    assert [event["active_notional"] for event in result.evidence["events"]] == [
        pytest.approx(200.0),
        pytest.approx(200.0),
    ]


@spec_claim(
    SpecClaim(
        "PFA-LF-01",
        "pipeline_local_funding",
        "canonical settlement identity",
        "L2c",
        "Sub-second exchange timestamp skew cannot split one funding settlement; "
        "one settled-history rate supersedes the pre-settlement observation.",
        status="implemented",
    )
)
def test_local_funding_canonicalizes_subsecond_settlement_identity(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    position = _position()
    _insert_entry_lineage(position)

    # Live observations identify 04:00.000 and 08:00.000. Historical settlement
    # rows preserve venue timestamps a few milliseconds after those boundaries.
    _snapshot_at(rate=0.0008, target=_iso_ms(4), observed=_iso(0, 20))
    _snapshot_at(rate=0.0010, target=_iso_ms(4), observed=_iso(3, 59))
    _snapshot_at(rate=0.0009, target=_iso_ms(4, millisecond=4),
                 observed=_iso_ms(4, millisecond=4))
    _snapshot_at(rate=0.0009, target=_iso_ms(8, millisecond=2),
                 observed=_iso_ms(4, millisecond=4))
    _snapshot_at(rate=-0.0004, target=_iso_ms(8, millisecond=2),
                 observed=_iso_ms(8, millisecond=2))
    _snapshot_at(rate=-0.0004, target=_iso_ms(12),
                 observed=_iso_ms(8, millisecond=2))
    _snapshot_at(rate=0.0002, target=_iso_ms(12), observed=_iso(8, 55))

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(position, closed_at=_iso(9))

    assert result.complete is True
    assert result.funding_cost == pytest.approx(0.1)
    assert len(result.evidence["events"]) == 2
    assert [event["funding_rate"] for event in result.evidence["events"]] == [
        pytest.approx(0.0009),
        pytest.approx(-0.0004),
    ]
    assert result.evidence["accounting_version"] == "local-paper-funding-v2"
    assert result.evidence["coverage_mode"] == "live_coverage"
    assert result.evidence["events"][0]["raw_scheduled_at"].endswith(
        "04:00:00.004000+00:00"
    )


@spec_claim(
    SpecClaim(
        "PFA-LF-04",
        "pipeline_local_funding",
        "conflicting settlement containment",
        "L2c",
        "Conflicting settled rates inside one canonical settlement bucket remain "
        "unavailable instead of selecting a convenient value.",
        status="implemented",
    )
)
def test_local_funding_rejects_conflicting_canonical_settlement_rates(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    position = _position()
    _insert_entry_lineage(position)
    _snapshot_at(rate=0.0010, target=_iso_ms(4), observed=_iso(0, 20))
    _snapshot_at(rate=0.0010, target=_iso_ms(4, millisecond=1),
                 observed=_iso_ms(4, millisecond=1))
    _snapshot_at(rate=0.0020, target=_iso_ms(4, millisecond=4),
                 observed=_iso_ms(4, millisecond=4))
    _snapshot_at(rate=0.0010, target=_iso_ms(8),
                 observed=_iso_ms(4, millisecond=1))
    _snapshot_at(rate=0.0002, target=_iso_ms(8), observed=_iso(7, 59))
    _snapshot_at(rate=0.0002, target=_iso_ms(12), observed=_iso(8, 1))
    _snapshot_at(rate=0.0002, target=_iso_ms(12), observed=_iso(8, 55))

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(position, closed_at=_iso(9))

    assert result.complete is False
    assert result.funding_cost is None
    assert result.evidence["reason"] == "conflicting_settlement_rates"


@spec_claim(
    SpecClaim(
        "PFA-LF-05",
        "pipeline_local_funding",
        "distinct settlement boundaries",
        "L2c",
        "Funding events at least one second apart retain distinct canonical "
        "settlement identities.",
        status="implemented",
    )
)
def test_local_funding_keeps_one_second_settlements_distinct():
    from aletheia.portfolio.local_funding import _canonical_settlement

    first = datetime(2026, 7, 14, 4, 0, 0, 4000, tzinfo=timezone.utc)
    second = first + timedelta(seconds=1)

    assert _canonical_settlement(first) != _canonical_settlement(second)


@spec_claim(
    SpecClaim(
        "PFA-LF-07",
        "pipeline_local_funding",
        "historical settlement coverage",
        "L2c",
        "A contiguous settled-history sequence that brackets the complete holding "
        "window can recover funding without pretending to be live coverage.",
        status="implemented",
    )
)
def test_local_funding_accepts_complete_historical_settlement_bracket(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    position = _position()
    _insert_entry_lineage(position)

    # Settled events before entry, through the holding window, and after close.
    historical = [
        (_iso_ms(0, millisecond=1), 0.0002),
        (_iso_ms(4, millisecond=4), 0.0009),
        (_iso_ms(8, millisecond=2), -0.0004),
        (_iso_ms(12, millisecond=1), 0.0003),
    ]
    for index, (settled_at, rate) in enumerate(historical):
        _snapshot_at(rate=rate, target=settled_at, observed=settled_at)
        if index + 1 < len(historical):
            _snapshot_at(
                rate=rate,
                target=historical[index + 1][0],
                observed=settled_at,
            )

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(
        position,
        closed_at=_iso(9),
        allow_historical_ledger=True,
        as_of=datetime(2026, 7, 14, 12, 1, tzinfo=timezone.utc),
    )

    assert result.complete is True
    assert result.funding_cost == pytest.approx(0.1)
    assert result.evidence["coverage_mode"] == "historical_ledger"
    assert [event["scheduled_at"] for event in result.evidence["events"]] == [
        _iso(4),
        _iso(8),
    ]


@spec_claim(
    SpecClaim(
        "PFA-LF-08",
        "pipeline_local_funding",
        "truncated historical coverage",
        "L2c",
        "Historical funding without a settlement at or before entry cannot claim "
        "complete holding-window coverage.",
        status="implemented",
    )
)
def test_local_funding_rejects_truncated_historical_settlement_window(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    position = _position()
    _insert_entry_lineage(position)
    historical = [
        (_iso_ms(4, millisecond=4), 0.0009),
        (_iso_ms(8, millisecond=2), -0.0004),
        (_iso_ms(12, millisecond=1), 0.0003),
    ]
    for index, (settled_at, rate) in enumerate(historical):
        _snapshot_at(rate=rate, target=settled_at, observed=settled_at)
        if index + 1 < len(historical):
            _snapshot_at(
                rate=rate,
                target=historical[index + 1][0],
                observed=settled_at,
            )

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(
        position,
        closed_at=_iso(9),
        allow_historical_ledger=True,
        as_of=datetime(2026, 7, 14, 12, 1, tzinfo=timezone.utc),
    )

    assert result.complete is False
    assert result.funding_cost is None
    assert result.evidence["reason"] == "incomplete_historical_funding_ledger"


@spec_claim(
    SpecClaim(
        "PL-LF-07",
        "pipeline_local_funding",
        "time-segmented entry notional",
        "L2c",
        "Each funding settlement uses only exact entry Fill notional already active "
        "before that instant. Entry identity, quantity closure, and a Fill exactly "
        "on the settlement boundary are all fail-closed.",
        status="implemented",
    )
)
def test_local_funding_uses_only_entry_fills_active_before_each_settlement(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    position = {
        **_position(),
        "id": "position-multileg",
        "proposal_id": "proposal-position-multileg-0",
        "order_id": "order-position-multileg-0",
        "quantity": 3.0,
        "entry_price": 500.0 / 3.0,
    }
    _insert_entry_lineage(
        position,
        legs=[
            (100.0, 1.0, _iso(0, 30)),
            (200.0, 2.0, _iso(6)),
        ],
    )
    _snapshot(rate=0.0010, target_hour=4, observed_hour=0, observed_minute=20)
    _snapshot(rate=0.0010, target_hour=4, observed_hour=3, observed_minute=59)
    _snapshot(rate=0.0010, target_hour=8, observed_hour=4, observed_minute=1)
    _snapshot(rate=0.0010, target_hour=8, observed_hour=7, observed_minute=59)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=1)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=55)

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(position, closed_at=_iso(9))

    assert result.complete is True
    assert result.funding_cost == pytest.approx(0.6)
    assert [event["active_notional"] for event in result.evidence["events"]] == [
        pytest.approx(100.0),
        pytest.approx(500.0),
    ]
    assert result.evidence["entry_fill_count"] == 2


@pytest.mark.parametrize(
    ("corruption", "expected_reason"),
    [
        ("settlement_boundary", "entry_fill_on_settlement_boundary"),
        ("quantity", "entry_quantity_mismatch"),
        ("identity", "entry_identity_mismatch"),
        ("before_open", "entry_fill_before_open"),
        ("primary_time", "primary_entry_time_mismatch"),
    ],
)
def test_local_funding_fails_closed_on_ambiguous_or_broken_entry_lineage(
    tmp_path, monkeypatch, corruption, expected_reason,
):
    conn = _setup_db(tmp_path, monkeypatch)
    position = {
        **_position(),
        "id": f"position-broken-{corruption}",
        "proposal_id": f"proposal-position-broken-{corruption}-0",
        "order_id": f"order-position-broken-{corruption}-0",
        "quantity": 3.0,
        "entry_price": 500.0 / 3.0,
    }
    second_fill_at = _iso(4) if corruption == "settlement_boundary" else _iso(6)
    _insert_entry_lineage(
        position,
        legs=[(100.0, 1.0, _iso(0, 30)), (200.0, 2.0, second_fill_at)],
    )
    if corruption == "quantity":
        conn.execute(
            "UPDATE fills SET fill_quantity=1.9 WHERE id=?",
            (f"fill-{position['id']}-1",),
        )
    elif corruption == "identity":
        conn.execute(
            "UPDATE orders SET symbol='ETH/USDT' WHERE id=?",
            (f"order-{position['id']}-1",),
        )
    elif corruption == "before_open":
        conn.execute(
            "UPDATE fills SET filled_at=? WHERE id=?",
            (_iso(0, 29), f"fill-{position['id']}-0"),
        )
    elif corruption == "primary_time":
        conn.execute(
            "UPDATE fills SET filled_at=? WHERE id=?",
            (_iso(0, 31), f"fill-{position['id']}-0"),
        )
    _snapshot(rate=0.0010, target_hour=4, observed_hour=0, observed_minute=20)
    _snapshot(rate=0.0010, target_hour=4, observed_hour=3, observed_minute=59)
    _snapshot(rate=0.0010, target_hour=8, observed_hour=4, observed_minute=1)
    _snapshot(rate=0.0010, target_hour=8, observed_hour=7, observed_minute=59)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=1)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=55)

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(position, closed_at=_iso(9))

    assert result.complete is False
    assert result.funding_cost is None
    assert result.funding_source == "unavailable"
    assert result.evidence["reason"] == expected_reason


@spec_claim(
    SpecClaim(
        "PL-LF-02",
        "pipeline_local_funding",
        "complete schedule coverage",
        "L2c",
        "The simulator never turns partial funding observations into a numeric PnL: "
        "every elapsed event must lead to the next observed schedule and a target "
        "strictly after close must anchor the end of the window.",
        status="implemented",
    )
)
@pytest.mark.parametrize("missing", ["transition", "close_anchor"])
def test_local_funding_fails_closed_when_schedule_coverage_is_incomplete(
    tmp_path, monkeypatch, missing,
):
    _setup_db(tmp_path, monkeypatch)
    position = _position()
    _insert_entry_lineage(position)
    _snapshot(rate=0.0010, target_hour=4, observed_hour=0, observed_minute=20)
    if missing != "transition":
        _snapshot(rate=-0.0005, target_hour=8, observed_hour=4, observed_minute=1)
        _snapshot(rate=-0.0005, target_hour=8, observed_hour=7, observed_minute=59)
    if missing != "close_anchor":
        _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=55)

    from aletheia.portfolio.local_funding import compute_local_funding

    result = compute_local_funding(position, closed_at=_iso(9))

    assert result.complete is False
    assert result.funding_cost is None
    assert result.funding_source == "unavailable"
    assert result.evidence["status"] == "incomplete"
    assert result.evidence["reason"] in {
        "missing_schedule_transition",
        "missing_event_observation",
        "missing_close_anchor",
    }


@spec_claim(
    SpecClaim(
        "PL-LF-03",
        "pipeline_local_funding",
        "open-position collection coverage",
        "L2b",
        "The two-minute derivatives collector continues observing every open local "
        "paper futures symbol even after it leaves the active universe.",
        status="implemented",
    )
)
def test_scheduler_observed_instruments_include_all_open_paper_futures(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime import scheduler
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(repo, "get_active_universe_members", lambda limit: [])
    monkeypatch.setattr(
        "aletheia.trading.instruments.observed_instruments_from_config",
        lambda _cfg: [],
    )
    monkeypatch.setattr(
        repo,
        "list_all_open_positions",
        lambda: [
            {
                "symbol": "HYPE/USDT",
                "market_type": "usdt_futures",
                "execution_mode": "paper_internal",
                "status": "open",
            },
            {
                "symbol": "LIT/USDT",
                "market_type": "usdt_futures",
                "execution_mode": "paper_internal",
                "status": "open",
            },
            {
                "symbol": "ETH/USDT",
                "market_type": "spot",
                "execution_mode": "paper_internal",
                "status": "open",
            },
            {
                "symbol": "SOL/USDT",
                "market_type": "usdt_futures",
                "execution_mode": "okx_demo",
                "status": "open",
            },
        ],
    )
    symbols = scheduler._get_derivative_symbols([])

    assert symbols == ["HYPE/USDT", "LIT/USDT"]
    import inspect

    collector_source = inspect.getsource(scheduler._async_data_collectors)
    assert "_get_derivative_symbols(instruments)" in collector_source
    assert "deriv.collect(derivative_symbols)" in collector_source


@spec_claim(
    SpecClaim(
        "PL-LF-06",
        "pipeline_local_funding",
        "entry schedule preflight",
        "L2b",
        "A new local futures entry is fundable only when a primary-venue next "
        "settlement schedule was durably observed within the collection freshness "
        "window; predicted cross-venue rows and stale rows cannot authorize it.",
        status="implemented",
    )
)
def test_local_funding_entry_schedule_requires_fresh_primary_snapshot(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.local_funding import has_fresh_local_funding_schedule
    from aletheia.storage import repositories as repo

    repo.insert_funding_rate_snapshot({
        "symbol": "BTC/USDT",
        "exchange": "hyperliquid_predicted",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095,
        "next_funding_time": _iso(4),
        "observed_at": _iso(0, 29),
    })
    repo.insert_funding_rate_snapshot({
        "symbol": "BTC/USDT",
        "exchange": "okx",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095,
        "next_funding_time": _iso(4),
        "observed_at": _iso(0, 10),
    })

    assert has_fresh_local_funding_schedule("BTC/USDT", as_of=_iso(0, 30)) is False

    _snapshot(rate=0.0001, target_hour=4, observed_hour=0, observed_minute=29)
    assert has_fresh_local_funding_schedule("BTC/USDT", as_of=_iso(0, 30)) is True


@spec_claim(
    SpecClaim(
        "PL-LF-04",
        "pipeline_local_funding",
        "atomic close evidence handoff",
        "L2c",
        "A local paper close passes validated simulated funding into the central close "
        "transaction, where the close Fill and funding-event evidence remain together.",
        status="implemented",
    )
)
def test_close_position_accepts_explicit_local_funding_and_merges_evidence(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        id="position-explicit-funding",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        entry_fee=0.12,
        entry_price_source="local_fill",
        entry_fill_verified=True,
        opened_at=_iso(0, 30),
    )
    repo.insert_position(position)
    monkeypatch.setattr("aletheia.portfolio.pnl._btc_price_at_close", lambda: 60_000.0)
    monkeypatch.setattr("aletheia.notifications.notify_operator", lambda **_kw: None)

    result = close_position(
        position.id,
        exit_price=110.0,
        reason="take_profit",
        actual_exit_fee=0.132,
        closed_at=_iso(9),
        funding_cost=0.1,
        funding_source="local_simulation",
        funding_evidence=_complete_funding_evidence(position.model_dump()),
    )

    stored = repo._select_one("positions", position.id)
    assert stored["funding_cost"] == pytest.approx(0.1)
    assert stored["funding_source"] == "local_simulation"
    assert result["realized_pnl"] == pytest.approx(20.0 - 0.12 - 0.132 - 0.1)
    evidence = json.loads(stored["reconciliation_evidence"])
    assert evidence["funding"]["status"] == "complete"
    assert evidence["funding"]["events"][0]["scheduled_at"] == _iso(4)
    fingerprint_payload = json.dumps(
        evidence["funding"], sort_keys=True, separators=(",", ":"), default=str,
    )
    expected_fingerprint = (
        "sha256:"
        + hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
    )
    assert evidence["funding_accounting_version"] == "local-paper-funding-v2"
    assert evidence["funding_economics_fingerprint"] == expected_fingerprint


@spec_claim(
    SpecClaim(
        "PL-LF-08",
        "pipeline_local_funding",
        "authoritative close handoff",
        "L2c",
        "Incomplete or failed local funding computation explicitly clears stale "
        "Position economics. Complete evidence must match source, Position, symbol, "
        "holding window, and the exact sum handed to the central close transaction.",
        status="implemented",
    )
)
def test_close_position_incomplete_funding_evidence_clears_stale_position_value(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        id="position-stale-funding",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        entry_fee=0.12,
        funding_cost=9.0,
        funding_source="local_simulation",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        opened_at=_iso(0, 30),
    )
    repo.insert_position(position)
    monkeypatch.setattr("aletheia.portfolio.pnl._btc_price_at_close", lambda: 60_000.0)
    monkeypatch.setattr("aletheia.notifications.notify_operator", lambda **_kw: None)

    result = close_position(
        position.id,
        exit_price=110.0,
        reason="take_profit",
        actual_exit_fee=0.132,
        closed_at=_iso(9),
        funding_evidence={
            "status": "incomplete",
            "reason": "missing_event_observation",
            "source": "local_simulation",
            "position_id": position.id,
            "symbol": position.symbol,
            "opened_at": position.opened_at,
            "closed_at": _iso(9),
            "events": [],
        },
    )

    stored = repo._select_one("positions", position.id)
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["realized_pnl"] is None
    assert result["outcome_eligible"] is False
    assert result["outcome_exclusion_reason"] == "incomplete_funding_economics"
    evidence = json.loads(stored["reconciliation_evidence"])
    assert evidence["funding"]["status"] == "incomplete"
    assert "funding_accounting_version" not in evidence
    assert "funding_economics_fingerprint" not in evidence


@pytest.mark.parametrize(
    "mutation",
    ["source", "position", "symbol", "opened_at", "closed_at", "event_window", "event_sum"],
)
def test_close_position_rejects_mismatched_complete_funding_evidence(
    tmp_path, monkeypatch, mutation,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        id=f"position-invalid-evidence-{mutation}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        entry_fee=0.12,
        entry_price_source="local_fill",
        entry_fill_verified=True,
        opened_at=_iso(0, 30),
    )
    repo.insert_position(position)
    evidence = _complete_funding_evidence(position.model_dump())
    if mutation == "source":
        evidence["source"] = "broker_ledger"
    elif mutation == "position":
        evidence["position_id"] = "another-position"
    elif mutation == "symbol":
        evidence["symbol"] = "ETH/USDT"
    elif mutation == "opened_at":
        evidence["opened_at"] = _iso(1)
    elif mutation == "closed_at":
        evidence["closed_at"] = _iso(8)
    elif mutation == "event_window":
        evidence["events"][0]["scheduled_at"] = _iso(10)
    elif mutation == "event_sum":
        evidence["events"][0]["funding_cost"] = 0.2

    with pytest.raises(ValueError, match="funding evidence"):
        close_position(
            position.id,
            exit_price=110.0,
            reason="take_profit",
            actual_exit_fee=0.132,
            closed_at=_iso(9),
            funding_cost=0.1,
            funding_source="local_simulation",
            funding_evidence=evidence,
        )

    assert repo._select_one("positions", position.id)["status"] == "open"


@spec_claim(
    SpecClaim(
        "PL-LF-05",
        "pipeline_local_funding",
        "real paper broker close",
        "L2c",
        "The production paper close creates its Order and Fill, computes the complete "
        "scheduled funding window, and stores signed funding and event evidence with "
        "the resulting net PnL.",
        status="implemented",
    )
)
def test_paper_broker_close_materializes_funding_order_fill_and_net_pnl(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import paper_broker
    from aletheia.portfolio.paper_account import record_position_opened
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    _snapshot(rate=0.0010, target_hour=4, observed_hour=0, observed_minute=20)
    _snapshot(rate=0.0010, target_hour=4, observed_hour=3, observed_minute=59)
    _snapshot(rate=-0.0005, target_hour=8, observed_hour=4, observed_minute=1)
    _snapshot(rate=-0.0005, target_hour=8, observed_hour=7, observed_minute=59)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=1)
    _snapshot(rate=0.0002, target_hour=12, observed_hour=8, observed_minute=55)

    position = Position(
        id="position-paper-funding-chain",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        proposal_id="proposal-position-paper-funding-chain-0",
        order_id="order-position-paper-funding-chain-0",
        entry_fee=0.12,
        entry_price_source="local_fill",
        entry_fill_verified=True,
        opened_at=_iso(0, 30),
    )
    _insert_entry_lineage(position.model_dump())
    repo.insert_position(position)
    record_position_opened(position.id)

    from types import SimpleNamespace

    cfg = SimpleNamespace(
        trading=SimpleNamespace(slippage_bps=0.0, execution_fee_pct=0.06),
    )
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: cfg)
    monkeypatch.setattr("aletheia.portfolio.pnl._btc_price_at_close", lambda: 60_000.0)
    monkeypatch.setattr("aletheia.notifications.notify_operator", lambda **_kw: None)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(paper_broker, "datetime", _FixedDatetime)

    result = paper_broker.close_paper_position(
        position.id,
        mark_price=110.0,
        reason="take_profit",
        source="funding_spec",
    )

    stored = repo._select_one("positions", position.id)
    close_order = repo.get_order(result["order_id"])
    close_fill = conn.execute(
        "SELECT * FROM fills WHERE id=?", (result["fill_id"],)
    ).fetchone()
    assert close_order["position_id"] == position.id
    assert close_fill["order_id"] == close_order["id"]
    assert stored["funding_cost"] == pytest.approx(0.1)
    assert stored["funding_source"] == "local_simulation"
    expected = 20.0 - 0.12 - float(close_fill["fee"]) - 0.1
    assert stored["realized_pnl"] == pytest.approx(expected)
    evidence = __import__("json").loads(stored["reconciliation_evidence"])
    assert evidence["source"] == "local_paper_fill"
    assert len(evidence["funding"]["events"]) == 2
    assert evidence["funding"]["next_settlement_anchor"]["scheduled_at"] == _iso(12)


@pytest.mark.parametrize("failure_mode", ["incomplete", "exception"])
def test_paper_broker_funding_failure_overwrites_stale_position_economics(
    tmp_path, monkeypatch, failure_mode,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import paper_broker
    from aletheia.portfolio.local_funding import LocalFundingResult
    from aletheia.portfolio.paper_account import record_position_opened
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from types import SimpleNamespace

    position = Position(
        id=f"position-broker-{failure_mode}",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        entry_fee=0.12,
        funding_cost=9.0,
        funding_source="local_simulation",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        opened_at=_iso(0, 30),
    )
    repo.insert_position(position)
    record_position_opened(position.id)
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(
            trading=SimpleNamespace(slippage_bps=0.0, execution_fee_pct=0.06),
        ),
    )
    monkeypatch.setattr("aletheia.portfolio.pnl._btc_price_at_close", lambda: 60_000.0)
    monkeypatch.setattr("aletheia.notifications.notify_operator", lambda **_kw: None)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(paper_broker, "datetime", _FixedDatetime)
    if failure_mode == "incomplete":
        monkeypatch.setattr(
            "aletheia.portfolio.local_funding.compute_local_funding",
            lambda *_args, **_kwargs: LocalFundingResult(
                complete=False,
                funding_cost=None,
                funding_source="unavailable",
                evidence={
                    "status": "incomplete",
                    "reason": "missing_event_observation",
                    "source": "local_simulation",
                    "position_id": position.id,
                    "symbol": position.symbol,
                    "opened_at": position.opened_at,
                    "closed_at": _iso(9),
                    "events": [],
                },
            ),
        )
    else:
        def _raise_funding(*_args, **_kwargs):
            raise RuntimeError("collector unavailable")

        monkeypatch.setattr(
            "aletheia.portfolio.local_funding.compute_local_funding",
            _raise_funding,
        )

    result = paper_broker.close_paper_position(
        position.id,
        mark_price=110.0,
        reason="take_profit",
        source="funding_spec",
    )

    stored = repo._select_one("positions", position.id)
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["realized_pnl"] is None
    assert result["realized_pnl"] is None
    evidence = __import__("json").loads(stored["reconciliation_evidence"])
    assert evidence["funding"]["status"] == "incomplete"
    expected_reason = (
        "missing_event_observation"
        if failure_mode == "incomplete"
        else "funding_read_failed"
    )
    assert evidence["funding"]["reason"] == expected_reason
