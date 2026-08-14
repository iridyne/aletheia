from __future__ import annotations

import sqlite3

import pytest

import aletheia.analytics.opportunity_lifecycle_story as lifecycle


_PRE_CLOSE = "2026-08-13T00:02:30+00:00"
_POST_CLOSE = "2026-08-13T00:06:00+00:00"


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE opportunities (
            id TEXT PRIMARY KEY, symbol TEXT, market TEXT,
            direction_bias TEXT, thesis TEXT, created_at TEXT
        );
        CREATE TABLE pi_decisions (
            id TEXT PRIMARY KEY, opportunity_id TEXT, proposal_id TEXT,
            action TEXT, created_at TEXT
        );
        CREATE TABLE trade_proposals (
            id TEXT PRIMARY KEY, opportunity_id TEXT, symbol TEXT,
            direction TEXT, attribution_snapshot TEXT, created_at TEXT
        );
        CREATE TABLE risk_decisions (
            id TEXT PRIMARY KEY, proposal_id TEXT, approved INTEGER,
            created_at TEXT
        );
        CREATE TABLE orders (
            id TEXT PRIMARY KEY, proposal_id TEXT, risk_decision_id TEXT,
            position_id TEXT, close_reason TEXT, status TEXT,
            created_at TEXT, filled_at TEXT
        );
        CREATE TABLE fills (
            id TEXT PRIMARY KEY, order_id TEXT, fill_price REAL,
            fill_quantity REAL, filled_at TEXT
        );
        CREATE TABLE positions (
            id TEXT PRIMARY KEY, opportunity_id TEXT, proposal_id TEXT,
            order_id TEXT, opened_at TEXT, closed_at TEXT, status TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO opportunities VALUES (?,?,?,?,?,?)",
        ("opp-1", "BTC/USDT", "perp", "long", "momentum", "2026-08-13T00:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO trade_proposals VALUES (?,?,?,?,?,?)",
        ("proposal-1", "opp-1", "BTC/USDT", "long", "{}", "2026-08-13T00:00:10+00:00"),
    )
    db.executemany(
        "INSERT INTO pi_decisions VALUES (?,?,?,?,?)",
        [
            ("decision-1", "opp-1", "proposal-1", "trade", "2026-08-13T00:00:20+00:00"),
            ("decision-2", "opp-1", "proposal-1", "adjust_position", "2026-08-13T00:03:00+00:00"),
        ],
    )
    db.execute(
        "INSERT INTO risk_decisions VALUES (?,?,?,?)",
        ("risk-1", "proposal-1", 1, "2026-08-13T00:00:30+00:00"),
    )
    db.executemany(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                "entry-order",
                "proposal-1",
                "risk-1",
                "position-1",
                None,
                "filled",
                "2026-08-13T00:00:40+00:00",
                "2026-08-13T00:00:50+00:00",
            ),
            (
                "close-order",
                None,
                None,
                "position-1",
                "stop_loss",
                "filled",
                "2026-08-13T00:04:40+00:00",
                "2026-08-13T00:04:50+00:00",
            ),
        ],
    )
    db.executemany(
        "INSERT INTO fills VALUES (?,?,?,?,?)",
        [
            ("entry-fill", "entry-order", 100.0, 1.0, "2026-08-13T00:00:50+00:00"),
            ("close-fill", "close-order", 98.5, 1.0, "2026-08-13T00:04:50+00:00"),
        ],
    )
    db.execute(
        "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
        (
            "position-1",
            "opp-1",
            "proposal-1",
            "entry-order",
            "2026-08-13T00:00:50+00:00",
            "2026-08-13T00:04:50+00:00",
            "closed",
        ),
    )
    db.commit()
    return db


def _story(position_id, as_of_at, db):
    return {
        "contract_version": "position_lifecycle_story/v1",
        "position_id": position_id,
        "as_of_at": as_of_at.isoformat(),
        "read_only": True,
        "availability": "available",
        "timeline": [],
        "economic_path": {
            "mark_to_market_curve": {
                "status": "available",
                "points": [],
            }
        },
        "evidence_gaps": [],
        "observed_mechanisms": [],
    }


def test_exact_chain_preserves_all_ids_fills_and_position_story(monkeypatch):
    db = _db()
    calls = []

    def build(position_id, *, as_of_at, db):
        calls.append((position_id, as_of_at))
        return _story(position_id, as_of_at, db)

    monkeypatch.setattr(lifecycle, "build_position_lifecycle_story", build)
    story = lifecycle.build_opportunity_lifecycle_story(
        "opp-1", as_of_at=_POST_CLOSE, db=db
    )

    assert story["contract_version"] == "opportunity_lifecycle/v1"
    assert story["read_only"] is True
    assert story["primary_cause"] is None
    assert story["multiplicity"] == {
        "decisions": 2,
        "proposals": 1,
        "risk_decisions": 1,
        "orders": 2,
        "fills": 2,
        "positions": 1,
        "zero_position": False,
        "multiple_positions": False,
    }
    proposal = story["lifecycle"]["proposals"][0]
    assert [row["id"] for row in proposal["pi_decisions"]] == ["decision-1", "decision-2"]
    assert [row["id"] for row in proposal["risk_decisions"]] == ["risk-1"]
    assert [row["id"] for row in proposal["orders"]] == ["entry-order"]
    assert [row["id"] for row in story["lifecycle"]["orders"]] == ["entry-order", "close-order"]
    assert [row["id"] for row in story["lifecycle"]["orders"][1]["fills"]] == ["close-fill"]
    assert story["lifecycle"]["positions"][0]["linkage"]["matched_edges"] == [
        "position.opportunity_id",
        "position.proposal_id",
        "position.order_id",
    ]
    assert calls[0][0] == "position-1"


def test_as_of_hides_future_close_order_fill_and_position_story_knowledge(monkeypatch):
    db = _db()
    db.execute(
        "UPDATE orders SET created_at=? WHERE id='close-order'",
        ("2026-08-13T00:02:00+00:00",),
    )
    db.execute(
        "CREATE TABLE shadow_trades ("
        "id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT, "
        "resolved_at TEXT, pnl_pct REAL, entry_price REAL, exit_price REAL)"
    )
    db.execute(
        "INSERT INTO shadow_trades VALUES (?,?,?,?,?,?,?)",
        (
            "shadow-1",
            "opp-1",
            "2026-08-13T00:01:00+00:00",
            "2026-08-13T00:05:00+00:00",
            -1.2,
            100.0,
            98.8,
        ),
    )
    db.commit()
    captured = []
    monkeypatch.setattr(
        lifecycle,
        "build_position_lifecycle_story",
        lambda position_id, *, as_of_at, db: captured.append(as_of_at)
        or _story(position_id, as_of_at, db),
    )
    story = lifecycle.build_opportunity_lifecycle_story(
        "opp-1", as_of_at=_PRE_CLOSE, db=db
    )

    assert [row["id"] for row in story["lifecycle"]["orders"]] == ["entry-order", "close-order"]
    close_order = story["lifecycle"]["orders"][1]
    assert close_order["filled_at"] is None
    assert close_order["status"] is None
    assert close_order["status_evidence"] == "unavailable"
    assert story["multiplicity"]["fills"] == 1
    assert captured[0].isoformat() == _PRE_CLOSE
    assert story["counterfactual"]["shadow"]["status"] == "pending"
    assert story["counterfactual"]["shadow"]["pnl_pct"] is None
    assert story["counterfactual"]["shadow"]["exit_price"] is None
    assert not story["counterfactual"]["forward"]


def test_shadow_multiplicity_is_preserved(monkeypatch):
    db = _db()
    db.execute(
        "CREATE TABLE shadow_trades ("
        "id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT, "
        "resolved_at TEXT, pnl_pct REAL, entry_price REAL, exit_price REAL)"
    )
    db.executemany(
        "INSERT INTO shadow_trades VALUES (?,?,?,?,?,?,?)",
        [
            ("shadow-1", "opp-1", "2026-08-13T00:01:00+00:00", None, None, 100.0, None),
            ("shadow-2", "opp-1", "2026-08-13T00:01:30+00:00", None, None, 101.0, None),
        ],
    )
    db.commit()

    story = lifecycle.build_opportunity_lifecycle_story(
        "opp-1", as_of_at=_PRE_CLOSE, db=db
    )

    assert story["counterfactual"]["shadow_multiplicity"] == 2
    assert [row["id"] for row in story["counterfactual"]["shadow_observations"]] == [
        "shadow-2",
        "shadow-1",
    ]
    assert "shadow_trades:multiple_observations_not_collapsed" in story["evidence"]["evidence_gaps"]


def test_zero_position_is_valid_and_multiple_positions_are_explicit(monkeypatch):
    db = _db()
    db.execute("DELETE FROM positions")
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_lifecycle_story", lambda *args, **kwargs: {})
    zero = lifecycle.build_opportunity_lifecycle_story("opp-1", as_of_at=_POST_CLOSE, db=db)
    assert zero["availability"] == "available"
    assert zero["multiplicity"]["zero_position"] is True

    db.executemany(
        "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
        [
            ("position-a", "opp-1", "proposal-1", "entry-order", "2026-08-13T00:01:00+00:00", None, "open"),
            ("position-b", "opp-1", "proposal-1", "entry-order", "2026-08-13T00:02:00+00:00", None, "open"),
        ],
    )
    db.commit()
    multiple = lifecycle.build_opportunity_lifecycle_story("opp-1", as_of_at=_POST_CLOSE, db=db)
    assert multiple["multiplicity"]["multiple_positions"] is True
    assert multiple["multiplicity"]["positions"] == 2


def test_contradictory_position_linkage_is_reported_without_symbol_time_repair(monkeypatch):
    db = _db()
    db.execute(
        "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
        (
            "position-mismatch",
            "opp-1",
            "other-proposal",
            "other-order",
            "2026-08-13T00:01:00+00:00",
            None,
            "open",
        ),
    )
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_lifecycle_story", lambda *args, **kwargs: {})
    story = lifecycle.build_opportunity_lifecycle_story("opp-1", as_of_at=_POST_CLOSE, db=db)
    assert any(
        item["entity_id"] == "position-mismatch"
        for item in story["linkage_audit"]["mismatches"]
    )
    mismatch_position = next(
        item
        for item in story["lifecycle"]["positions"]
        if item["position_id"] == "position-mismatch"
    )
    assert mismatch_position["linkage"]["matched_edges"] == ["position.opportunity_id"]


def test_invalid_as_of_raises_the_contract_error():
    with pytest.raises(ValueError, match="^invalid as_of_at$"):
        lifecycle.build_opportunity_lifecycle_story("opp-1", as_of_at="not-a-time", db=_db())


def test_human_renderer_keeps_linkage_and_zero_position_evidence_visible():
    db = _db()
    db.execute("DELETE FROM positions")
    db.commit()
    story = lifecycle.build_opportunity_lifecycle_story("opp-1", as_of_at=_POST_CLOSE, db=db)
    rendered = lifecycle.render_opportunity_lifecycle_story(story)
    assert "zero exact linked Positions" in rendered
    assert "Linkage and evidence limits" in rendered
    assert "Primary cause: not claimed" in rendered
