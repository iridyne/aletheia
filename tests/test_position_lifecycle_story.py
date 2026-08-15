from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

import aletheia.analytics.position_lifecycle_story as lifecycle
from aletheia.cli.inspect_cmd import inspect_app


_ENTRY = "2026-08-13T00:00:00+00:00"
_CUTOFF = "2026-08-13T00:02:30+00:00"
_EXIT = "2026-08-13T00:05:00+00:00"


def _hypothesis() -> dict:
    return {
        "contract_version": "position_hypothesis/v1",
        "frozen_at": _ENTRY,
        "thesis": "momentum continuation",
        "expected_horizon_hours": 0.05,
        "supporting_signals": ["breakout"],
        "invalidation_conditions": [{"kind": "price_at_or_below", "threshold": 99.0}],
        "opening_decision_context": {
            "reasoning": "breakout with trend support",
            "bear_case": "failed breakout",
            "key_assumptions": ["liquidity remains available"],
        },
        "initial_risk": {"quote_amount": 1.0, "stop_price": 99.0},
        "target_price": 104.0,
    }


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE positions (
            id TEXT PRIMARY KEY, opportunity_id TEXT, proposal_id TEXT, order_id TEXT,
            symbol TEXT, market_type TEXT, direction TEXT, quantity REAL,
            entry_price REAL, current_price REAL, realized_pnl REAL,
            entry_fee REAL, exit_fee REAL, funding_cost REAL, funding_source TEXT,
            entry_price_source TEXT, entry_fill_verified INTEGER,
            exit_price_source TEXT, exit_fill_verified INTEGER, outcome_eligible INTEGER,
            outcome_exclusion_reason TEXT, status TEXT, execution_mode TEXT,
            stop_price REAL, take_profit_price REAL, close_reason TEXT,
            exit_plan_effective_at TEXT, exit_reasoning TEXT,
            runtime_session_id TEXT, producer_commit TEXT, behavior_fingerprint TEXT,
            opened_at TEXT, closed_at TEXT
        );
        CREATE TABLE trade_proposals (
            id TEXT PRIMARY KEY, opportunity_id TEXT, symbol TEXT, direction TEXT,
            stop_price REAL, take_profit REAL, attribution_snapshot TEXT,
            created_at TEXT, runtime_session_id TEXT, producer_commit TEXT,
            behavior_fingerprint TEXT
        );
        CREATE TABLE opportunities (
            id TEXT PRIMARY KEY, universe_member_id TEXT, created_at TEXT
        );
        CREATE TABLE universe_members (id TEXT PRIMARY KEY, asset_id TEXT);
        CREATE TABLE universe_registry (
            id TEXT PRIMARY KEY, venue TEXT, symbol TEXT, market_type TEXT
        );
        CREATE TABLE pi_decisions (
            id TEXT PRIMARY KEY, proposal_id TEXT, action TEXT, created_at TEXT,
            reasoning TEXT, evidence_manifest_json TEXT
        );
        CREATE TABLE risk_decisions (
            id TEXT PRIMARY KEY, proposal_id TEXT, approved INTEGER,
            decision_role TEXT, policy_id TEXT, created_at TEXT
        );
        CREATE TABLE orders (
            id TEXT PRIMARY KEY, proposal_id TEXT, risk_decision_id TEXT,
            position_id TEXT, close_reason TEXT, created_at TEXT, filled_at TEXT
        );
        CREATE TABLE fills (
            id TEXT PRIMARY KEY, order_id TEXT, fill_price REAL,
            fill_quantity REAL, filled_at TEXT
        );
        CREATE TABLE position_exit_plan_versions (
            position_id TEXT, version INTEGER, effective_at TEXT,
            stop_price REAL, take_profit_price REAL, plan_json TEXT,
            provenance TEXT
        );
        CREATE TABLE position_protection_state (
            position_id TEXT PRIMARY KEY, activated_at TEXT
        );
        CREATE TABLE position_protection_triggers (
            trigger_id TEXT PRIMARY KEY, position_id TEXT, detected_at TEXT,
            stop_hit INTEGER, target_hit INTEGER, reason TEXT, bar_open_ms INTEGER
        );
        CREATE TABLE position_path_observations (
            position_id TEXT, bucket_open_ms INTEGER, observed_at TEXT,
            evidence_status TEXT
        );
        CREATE TABLE market_ohlcv (
            ts INTEGER, open REAL, high REAL, low REAL, close REAL,
            volume REAL, provider TEXT, exchange TEXT, symbol TEXT,
            market_type TEXT, timeframe TEXT
        );
        """
    )
    hypothesis = _hypothesis()
    db.execute(
        "INSERT INTO universe_registry VALUES ('asset-1','bitget','BTC/USDT','perp')"
    )
    db.execute("INSERT INTO universe_members VALUES ('member-1','asset-1')")
    db.execute(
        "INSERT INTO opportunities VALUES ('opp-1','member-1','2026-08-12T23:59:00+00:00')"
    )
    db.execute(
        "INSERT INTO trade_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "proposal-1",
            "opp-1",
            "BTC/USDT",
            "long",
            99.0,
            104.0,
            json.dumps({"position_hypothesis": hypothesis}),
            "2026-08-12T23:59:30+00:00",
            "session-1",
            "commit-a",
            "behavior-a",
        ),
    )
    db.execute(
        "INSERT INTO pi_decisions VALUES (?,?,?,?,?,?)",
        ("decision-1", "proposal-1", "trade", "2026-08-12T23:59:40+00:00", "trade", "{}"),
    )
    db.execute(
        "INSERT INTO pi_decisions VALUES (?,?,?,?,?,?)",
        (
            "review-1",
            "proposal-1",
            "adjust_position",
            "2026-08-13T00:03:00+00:00",
            "reviewed",
            json.dumps({"position_review": {"target_position_id": "position-1", "trigger": "horizon_due"}}),
        ),
    )
    db.execute(
        "INSERT INTO risk_decisions VALUES (?,?,?,?,?,?)",
        ("risk-1", "proposal-1", 1, "authoritative", "risk-v1", "2026-08-12T23:59:50+00:00"),
    )
    db.execute(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
        ("entry-order", "proposal-1", "risk-1", "position-1", None, _ENTRY, _ENTRY),
    )
    db.execute(
        "INSERT INTO fills VALUES (?,?,?,?,?)",
        ("entry-fill", "entry-order", 100.0, 1.0, _ENTRY),
    )
    db.execute(
        "INSERT INTO positions VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?21,?22,?23,?24,?25,?26,?27,?28,?29,?30,?31,?32,?33)",
        (
            "position-1", "opp-1", "proposal-1", "entry-order", "BTC/USDT", "usdt_futures",
            "long", 1.0, 100.0, 98.5, -1.7, 0.1, 0.1, 0.0, "local_simulation",
            "local_fill", 1, "local_fill", 1, 1, "eligible", "closed", "paper_internal",
            100.5, 104.0, "stop_loss", "2026-08-13T00:02:00+00:00", "stop",
            "session-1", "commit-a", "behavior-a", _ENTRY, _EXIT,
        ),
    )
    db.execute(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
        ("close-order", None, None, "position-1", "stop_loss", "2026-08-13T00:04:50+00:00", _EXIT),
    )
    db.execute(
        "INSERT INTO fills VALUES (?,?,?,?,?)",
        ("close-fill", "close-order", 98.5, 1.0, _EXIT),
    )
    db.execute(
        "INSERT INTO position_exit_plan_versions VALUES (?,?,?,?,?,?,?)",
        ("position-1", 1, _ENTRY, 99.0, 104.0, '{"trailing":{"enabled":false}}', "operator"),
    )
    db.execute(
        "INSERT INTO position_exit_plan_versions VALUES (?,?,?,?,?,?,?)",
        ("position-1", 2, "2026-08-13T00:02:00+00:00", 100.5, 104.0, '{"trailing":{"enabled":true}}', "operator"),
    )
    db.execute(
        "INSERT INTO position_protection_state VALUES (?,?)",
        ("position-1", "2026-08-13T00:02:00+00:00"),
    )
    bars = [
        (0, 100.0, 100.5, 99.5, 100.2),
        (60_000, 100.2, 101.5, 100.0, 101.0),
        (120_000, 101.0, 103.5, 100.5, 102.0),
        (180_000, 102.0, 102.5, 98.5, 99.0),
        (240_000, 99.0, 99.5, 98.0, 98.5),
    ]
    for timestamp, opening, high, low, close in bars:
        db.execute(
            "INSERT INTO market_ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                1_786_579_200_000 + timestamp,
                opening,
                high,
                low,
                close,
                10.0,
                "ccxt",
                "bitget",
                "BTC/USDT",
                "perp",
                "1m",
            ),
        )
    db.commit()
    return db


def _fact() -> dict:
    bars = [
        [1_786_579_200_000 + offset, opening, high, low, close, 10.0, "ccxt"]
        for offset, opening, high, low, close in [
            (0, 100.0, 100.5, 99.5, 100.2),
            (60_000, 100.2, 101.5, 100.0, 101.0),
            (120_000, 101.0, 103.5, 100.5, 102.0),
            (180_000, 102.0, 102.5, 98.5, 99.0),
            (240_000, 99.0, 99.5, 98.0, 98.5),
        ]
    ]
    return {
        "availability": "available",
        "identity": {"risk_decision_id": "risk-1"},
        "producer": {
            "status": "available",
            "runtime_session_ids": ["session-1"],
            "producer_commits": ["commit-a"],
            "behavior_fingerprints": ["behavior-a"],
        },
        "market_data_identity": {"exchange": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "timeframe": "1m"},
        "economics": {
            "availability": "available",
            "gross_pnl": -1.5,
            "net_pnl": -1.7,
            "entry_fee": 0.1,
            "exit_fee": 0.1,
            "funding_cost": 0.0,
        },
        "stages": {
            "decision": {
                "proposal": {"attribution_snapshot": {"position_hypothesis": json.loads(json.dumps(_hypothesis()))}},
            },
            "holding": {
                "trade_path": {
                    "path_evidence_status": "available",
                    "path_evidence_reason": "complete_exact_1m_path",
                    "mfe_pct": 3.5,
                    "mae_pct": -2.0,
                    "mfe_lower_bound_pct": 3.5,
                    "mfe_upper_bound_pct": 3.5,
                    "mae_lower_bound_pct": -2.0,
                    "mae_upper_bound_pct": -2.0,
                },
                "ohlcv": {"availability": "available", "rows": bars},
            },
        },
    }


def test_story_is_deterministic_read_only_and_reports_multiple_mechanisms(monkeypatch):
    db = _db()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())
    before = db.total_changes
    first = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    second = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    assert first == second
    assert db.total_changes == before
    assert first["read_only"] is True
    assert first["economic_path"]["verified_net_pnl"] == -1.7
    assert first["economic_path"]["giveback_from_peak_to_exit_pct"] > 0
    assert {"profit_giveback", "hypothesis_invalidated_before_exit", "expected_horizon_exceeded"}.issubset(
        first["observed_mechanisms"]
    )
    assert first["primary_cause"] is None
    assert any(event["event_type"] == "position_reviewed" for event in first["timeline"])
    assert any(event["event_type"] == "exit_plan_revised" for event in first["timeline"])
    assert any(event["event_type"] == "profit_protection_activated" for event in first["timeline"])
    assert first["audit"]["position_management_linkage"]["exact_position_id_used"] is True
    horizon = next(
        item for item in first["management_opportunities"]
        if item["node_type"] == "expected_horizon_reached"
    )
    assert horizon["exit_plan_changed"] is False

    curve = first["economic_path"]["mark_to_market_curve"]
    assert curve["status"] == "available"
    assert curve["kind"] == "price_return_pct_to_verified_exit"
    assert curve["point_interval_seconds"] == 60
    assert curve["points"][-1]["source_entity_type"] == "fill"
    assert curve["points"][-1]["source_entity_id"] == "close-fill"

def test_story_as_of_prefix_does_not_expose_eventual_close(monkeypatch):
    db = _db()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())
    story = lifecycle.build_position_lifecycle_story("position-1", as_of_at=_CUTOFF, db=db)

    assert story["as_of_at"] == "2026-08-13T00:02:30+00:00"
    assert not any(event["event_type"] == "exit_filled" and event["evidence_status"] == "available" for event in story["timeline"])
    assert story["economic_path"]["verified_net_pnl"] is None
    assert story["opening"]["original_thesis"] == "momentum continuation"
    assert all(
        event["occurred_at"] is None or event["occurred_at"] <= story["as_of_at"]
        for event in story["timeline"]
        if event["evidence_status"] == "available"
    )
    live_curve = story["economic_path"]["mark_to_market_curve"]
    assert live_curve["kind"] == "unrealized_price_return_pct"
    assert all(
        point["source_entity_type"] == "market_ohlcv_bar"
        for point in live_curve["points"]
    )


def test_story_rejects_closed_position_without_close_boundary(monkeypatch):
    db = _db()
    db.execute("UPDATE positions SET status='closed', closed_at=NULL WHERE id='position-1'")
    db.commit()

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    assert story["availability"] == "unavailable"
    assert story["unavailable_reason"] == "position_close_boundary_invalid"


def test_story_preserves_adverse_peak_source_type(monkeypatch):
    db = _db()
    db.execute("UPDATE fills SET fill_price=101.0 WHERE id='close-fill'")
    db.commit()
    fact = _fact()
    for row in fact["stages"]["holding"]["ohlcv"]["rows"]:
        row[2] = 100.0
        row[3] = 100.0
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    adverse = next(
        event for event in story["timeline"]
        if event["event_type"] == "maximum_adverse_excursion"
    )

    assert adverse["source_entity_type"] == "entry_fill"
    assert adverse["source_entity_id"] == "entry-fill"


def test_story_masks_future_close_fill_but_keeps_known_exit_intent(monkeypatch):
    db = _db()
    db.execute(
        "UPDATE orders SET created_at=? WHERE id='close-order'",
        ("2026-08-13T00:02:00+00:00",),
    )
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at=_CUTOFF, db=db
    )

    trigger = next(event for event in story["timeline"] if event["event_type"] == "exit_triggered")
    close_fill = next(event for event in story["timeline"] if event["event_type"] == "exit_filled")
    assert trigger["evidence_status"] == "available"
    assert trigger["facts"]["close_reason"] == "stop_loss"
    assert close_fill["evidence_status"] == "unavailable"
    assert close_fill["occurred_at"] is None


def test_story_open_prefix_never_claims_settled_excursion_or_horizon(monkeypatch):
    db = _db()
    db.execute(
        "UPDATE positions SET status='open', closed_at=NULL, realized_pnl=NULL, "
        "exit_fill_verified=0, exit_price_source='unavailable' WHERE id='position-1'"
    )
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at=_CUTOFF, db=db
    )

    assert story["economic_path"]["path_evidence_status"] == "unavailable"
    assert story["economic_path"]["maximum_favorable_excursion_pct"] is None
    assert story["economic_path"]["maximum_adverse_excursion_pct"] is None
    assert not any(
        event["event_type"] in {"maximum_favorable_excursion", "maximum_adverse_excursion", "expected_horizon_reached"}
        and event["evidence_status"] == "available"
        for event in story["timeline"]
    )


def test_story_uses_only_visible_broker_funding_settlement(monkeypatch):
    db = _db()
    db.execute(
        "CREATE TABLE audit_events ("
        "id TEXT PRIMARY KEY, timestamp TEXT, actor TEXT, source TEXT, event_type TEXT, "
        "entity_type TEXT, entity_id TEXT, before_state TEXT, after_state TEXT, message TEXT)"
    )
    db.execute(
        "UPDATE positions SET funding_source='broker_ledger' WHERE id='position-1'"
    )
    db.execute(
        "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "settlement-1",
            "2026-08-13T00:06:00+00:00",
            "system",
            "test",
            "exchange_position_funding_settled",
            "position",
            "position-1",
            "{}",
            "{}",
            "settled",
        ),
    )
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    before_settlement = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:05:30+00:00", db=db
    )
    after_settlement = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    assert before_settlement["audit"]["settlement"]["status"] == "unavailable"
    assert before_settlement["economic_path"]["verified_net_pnl"] is None
    assert after_settlement["audit"]["settlement"]["status"] == "available"
    assert after_settlement["economic_path"]["verified_net_pnl"] == -1.7


def test_story_preserves_fill_multiplicity_instead_of_verifying_one_fill(monkeypatch):
    db = _db()
    db.execute(
        "INSERT INTO fills VALUES (?,?,?,?,?)",
        ("entry-fill-2", "entry-order", 101.0, 0.5, "2026-08-13T00:00:10+00:00"),
    )
    db.execute(
        "UPDATE positions SET quantity=1.5, entry_price=?, realized_pnl=? WHERE id='position-1'",
        (100.33333333333333, -3.7),
    )
    db.execute(
        "INSERT INTO orders VALUES (?,?,?,?,?,?,?)",
        ("close-order-2", None, None, "position-1", "stop_loss", "2026-08-13T00:04:55+00:00", "2026-08-13T00:05:00+00:00"),
    )
    db.execute(
        "INSERT INTO fills VALUES (?,?,?,?,?)",
        ("close-fill-2", "close-order-2", 97.0, 0.5, "2026-08-13T00:05:00+00:00"),
    )
    db.commit()
    fact = _fact()
    fact["economics"]["gross_pnl"] = -3.5
    fact["economics"]["net_pnl"] = -3.7
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    assert story["opening"]["entry_fill_ids"] == ["entry-fill", "entry-fill-2"]
    assert story["opening"]["entry_fill_aggregation"] == "quantity_weighted"
    assert story["opening"]["entry_price"] == pytest.approx(100.33333333333333)
    entry_event = next(event for event in story["timeline"] if event["event_type"] == "entry_filled")
    assert entry_event["evidence_status"] == "available"
    assert entry_event["source_entity_type"] == "fill_aggregate"
    assert entry_event["facts"]["fill_ids"] == ["entry-fill", "entry-fill-2"]
    exit_event = next(event for event in story["timeline"] if event["event_type"] == "exit_filled")
    assert exit_event["evidence_status"] == "available"
    assert exit_event["facts"]["fill_ids"] == ["close-fill", "close-fill-2"]
    assert story["economic_path"]["availability"] == "available"
    assert story["economic_path"]["fill_aggregation"] == "quantity_weighted"
    assert story["economic_path"]["verified_gross_pnl"] == pytest.approx(-3.5)


def test_story_rejects_fill_quantity_mismatch(monkeypatch):
    db = _db()
    db.execute("UPDATE fills SET fill_quantity=0.5 WHERE id='close-fill'")
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    assert story["economic_path"]["availability"] == "unavailable"
    assert story["economic_path"]["unavailable_reason"] == "fill_quantity_mismatch"
    assert story["economic_path"]["quantity_evidence_status"] == "unavailable"
    close_fill = next(event for event in story["timeline"] if event["event_type"] == "exit_filled")
    assert close_fill["evidence_status"] == "unavailable"
    assert close_fill["facts"]["unavailable_reason"] == "fill_quantity_mismatch"


def test_story_rejects_false_risk_approval_and_preserves_exit_reason(monkeypatch):
    db = _db()
    db.execute("UPDATE risk_decisions SET approved='0' WHERE id='risk-1'")
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    risk = next(event for event in story["timeline"] if event["event_type"] == "risk_approved")
    assert risk["evidence_status"] == "unavailable"
    trigger = next(event for event in story["structured_events"] if event["event_type"] == "exit_trigger")
    assert trigger["facts"]["close_reason"] == "stop_loss" or trigger["facts"]["reason"] == "stop_loss"


def test_story_preserves_same_bar_ambiguity(monkeypatch):
    db = _db()
    fact = _fact()
    fact["stages"]["holding"]["trade_path"]["path_evidence_status"] = "ambiguous"
    fact["stages"]["holding"]["trade_path"]["path_evidence_reason"] = "intrabar_and_stop_barrier_order_unavailable"
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)

    story = lifecycle.build_position_lifecycle_story("position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db)
    mfe = next(event for event in story["timeline"] if event["event_type"] == "maximum_favorable_excursion")
    giveback = next(event for event in story["timeline"] if event["event_type"] == "profit_giveback")

    assert mfe["evidence_status"] == "unavailable"
    assert giveback["evidence_status"] == "ambiguous"
    assert story["economic_path"]["same_bar_ordering"] == "ambiguous"
    assert "profit_giveback" not in story["observed_mechanisms"]
    assert story["economic_path"]["mark_to_market_curve"]["status"] == "ambiguous"
    assert story["economic_path"]["mark_to_market_curve"]["points"]


def test_position_story_cli_supports_json_and_human(monkeypatch):
    story = {
        "position_id": "position-1",
        "timeline": [
            {
                "event_type": "hypothesis_invalidated",
                "evidence_status": "unavailable",
                "unavailable_reason": "path_missing",
            }
        ],
        "opening": {},
        "economic_path": {},
        "observed_mechanisms": [],
    }
    monkeypatch.setattr(lifecycle, "build_position_lifecycle_story", lambda *args, **kwargs: story)
    runner = CliRunner()

    raw = runner.invoke(inspect_app, ["position-story", "--id", "position-1", "--as-of", _CUTOFF, "--json"])
    human = runner.invoke(inspect_app, ["position-story", "--id", "position-1"])

    assert raw.exit_code == 0, raw.output
    assert json.loads(raw.stdout)["position_id"] == "position-1"
    assert human.exit_code == 0, human.output
    assert "Position: position-1 unknown" in human.stdout
    assert "Evidence limits" in human.stdout
    assert "path_missing" in human.stdout


def test_story_uses_completed_bar_timestamps_and_excludes_post_close_records(monkeypatch):
    db = _db()
    fact = _fact()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)
    db.execute(
        "INSERT INTO pi_decisions VALUES (?,?,?,?,?,?)",
        (
            "review-after-close",
            "proposal-1",
            "adjust_position",
            "2026-08-13T00:06:00+00:00",
            "late",
            json.dumps({"position_review": {"target_position_id": "position-1"}}),
        ),
    )
    db.execute(
        "INSERT INTO position_exit_plan_versions VALUES (?,?,?,?,?,?,?)",
        (
            "position-1",
            3,
            "2026-08-13T00:06:00+00:00",
            101.0,
            104.0,
            '{"late":true}',
            "operator",
        ),
    )
    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )

    peak = next(event for event in story["timeline"] if event["event_type"] == "maximum_favorable_excursion")
    assert peak["occurred_at"] == "2026-08-13T00:03:00+00:00"
    assert not any(
        event["event_type"] == "position_reviewed"
        and event["source_entity_id"] == "review-after-close"
        for event in story["timeline"]
    )
    assert not any(
        event["event_type"] == "exit_plan_revised"
        and event["facts"].get("version") == 3
        for event in story["timeline"]
    )


def test_story_reports_cost_flip_and_incomplete_costs_without_double_counting(monkeypatch):
    db = _db()
    db.execute(
        "UPDATE positions SET realized_pnl=-0.1, current_price=100.1 WHERE id='position-1'"
    )
    fact = _fact()
    fact["economics"]["gross_pnl"] = 0.1
    fact["economics"]["net_pnl"] = -0.1
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert story["economic_path"]["verified_gross_pnl"] == 0.1
    assert story["economic_path"]["verified_costs"] == 0.2
    assert story["economic_path"]["verified_net_pnl"] == -0.1
    assert "cost_flip" in story["observed_mechanisms"]

    db.execute("UPDATE positions SET funding_cost=NULL WHERE id='position-1'")
    incomplete = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert incomplete["economic_path"]["verified_costs"] is None
    assert incomplete["economic_path"]["verified_net_pnl"] is None
    assert incomplete["economic_path"]["cost_evidence_status"] == "incomplete"


def test_story_distinguishes_never_favorable_and_missing_hypothesis(monkeypatch):
    db = _db()
    fact = _fact()
    for row in fact["stages"]["holding"]["ohlcv"]["rows"]:
        row[2] = min(row[2], 100.1)
        row[3] = min(row[3], 100.0)
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)
    bad_entry = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert "never_economically_favorable" in bad_entry["observed_mechanisms"]

    db.execute(
        "UPDATE trade_proposals SET attribution_snapshot='{}' WHERE id='proposal-1'"
    )
    no_hypothesis = _fact()
    no_hypothesis["stages"]["decision"]["proposal"]["attribution_snapshot"] = {}
    monkeypatch.setattr(
        lifecycle, "build_position_replay", lambda *args, **kwargs: no_hypothesis
    )
    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert story["opening"]["original_thesis"] == "not_recorded"
    assert story["hypothesis_lifecycle"]["status"] == "not_recorded"
    assert "opening_hypothesis_not_recorded" in story["evidence_gaps"]




def test_story_preserves_protection_trigger_identity_and_collision_policy(monkeypatch):
    db = _db()
    for column in ("plan_version INTEGER", "plan_hash TEXT", "collision_policy TEXT", "bar_hash TEXT"):
        db.execute(f"ALTER TABLE position_protection_triggers ADD COLUMN {column}")
    db.execute(
        "INSERT INTO position_protection_triggers "
        "(trigger_id,position_id,detected_at,stop_hit,target_hit,reason,bar_open_ms,"
        "plan_version,plan_hash,collision_policy,bar_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "trigger-1",
            "position-1",
            "2026-08-13T00:04:30+00:00",
            1,
            1,
            "stop_loss",
            1_786_579_440_000,
            2,
            "plan-hash",
            "stop_first_v1",
            "bar-hash",
        ),
    )
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())
    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    event = next(item for item in story["timeline"] if item["event_type"] == "exit_triggered")
    assert event["facts"]["collision_policy"] == "stop_first_v1"
    assert event["facts"]["plan_hash"] == "plan-hash"
    rendered = lifecycle.render_position_lifecycle_story(story)
    assert "collision_policy=stop_first_v1" in rendered
def test_story_reports_missing_path_and_missing_management_linkage(monkeypatch):
    db = _db()
    db.execute("DELETE FROM pi_decisions WHERE id='review-1'")
    fact = _fact()
    fact["stages"]["holding"]["ohlcv"]["rows"].pop()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: fact)

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert story["economic_path"]["path_evidence_status"] == "unavailable"
    assert "ordered_1m_path_unavailable" in story["evidence_gaps"]
    assert story["audit"]["position_management_linkage"]["status"] == "unavailable"
    assert "position_management_linkage_not_recorded" in story["evidence_gaps"]


def test_structured_events_keep_ambiguous_decision_ids(monkeypatch):
    db = _db()
    db.execute(
        "INSERT INTO pi_decisions VALUES (?,?,?,?,?,?)",
        ("decision-2", "proposal-1", "trade", "2026-08-12T23:59:45+00:00", "trade", "{}"),
    )
    db.commit()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    decision = next(event for event in story["structured_events"] if event["event_type"] == "decision_committed")
    assert decision["evidence_status"] == "ambiguous"
    assert decision["facts"]["candidate_decision_ids"] == ["decision-1", "decision-2"]


def test_structured_events_are_the_only_machine_facing_story_projection(monkeypatch):
    db = _db()
    monkeypatch.setattr(lifecycle, "build_position_replay", lambda *args, **kwargs: _fact())

    story = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    structured = story["structured_events"]
    event_types = {event["event_type"] for event in structured}
    required = {
        "first_meaningful_favorable_excursion",
        "maximum_favorable_excursion",
        "hypothesis_invalidated",
        "expected_horizon_reached",
        "management_decision",
        "exit_plan_revision",
        "protection_activation",
        "exit_trigger",
        "close_fill",
        "verified_costs",
    }

    assert required <= event_types
    assert event_types <= set(lifecycle.POSITION_LIFECYCLE_EVENT_TYPES)
    payload = json.dumps(structured, ensure_ascii=False, sort_keys=True)
    assert "momentum continuation" not in payload
    assert "breakout with trend support" not in payload
    management = next(event for event in structured if event["event_type"] == "management_decision")
    assert "reasoning" not in management["facts"]
    costs = next(event for event in structured if event["event_type"] == "verified_costs")
    assert costs["evidence_status"] == "available"
    assert costs["facts"]["total_costs"] == 0.2

def test_story_exposes_visible_periodic_path_and_hides_late_recovery():
    db = _db()
    db.execute(
        "INSERT INTO position_path_observations "
        "(position_id, bucket_open_ms, observed_at, evidence_status) "
        "VALUES (?, ?, ?, ?)",
        ("position-1", 1_786_579_200_000, "2026-08-13T00:11:00+00:00", "available"),
    )
    late = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert late["periodic_observations"] == []
    assert not any(
        event["event_type"] == "position_path_observed"
        for event in late["timeline"]
    )

    db.execute(
        "UPDATE position_path_observations SET observed_at=?",
        ("2026-08-13T00:01:00+00:00",),
    )
    visible = lifecycle.build_position_lifecycle_story(
        "position-1", as_of_at="2026-08-13T00:10:00+00:00", db=db
    )
    assert [row["bucket_open_ms"] for row in visible["periodic_observations"]] == [
        1_786_579_200_000
    ]
    event = next(
        event for event in visible["structured_events"]
        if event["event_type"] == "position_path_observed"
    )
    assert event["facts"]["bucket_open_ms"] == 1_786_579_200_000
