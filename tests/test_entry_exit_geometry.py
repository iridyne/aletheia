from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
import typer

from aletheia.analytics import entry_exit_geometry as geometry
from aletheia.cli.opportunity import _build_audit_view
from aletheia.storage import repositories as repo
from aletheia.storage.models import TradeProposal


def _iso(minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _evidence(monkeypatch, **row_overrides):
    opened = _iso(-30)
    closed = _iso(-10)
    proposal = TradeProposal(
        id="proposal-1",
        opportunity_id="opportunity-1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="limit",
        quantity=2.0,
        price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        risk_reward=2.0,
        stop_source="operator",
        source="native",
        status="filled",
        created_at=_iso(-35),
    )
    proposal.commit_hash = repo._compute_proposal_hash(proposal)
    proposal_dict = proposal.model_dump()
    position = {
        "id": "position-1", "proposal_id": "proposal-1", "opportunity_id": "opportunity-1",
        "order_id": "order-1", "symbol": "BTC/USDT", "market_type": "spot",
        "direction": "long", "source": "native", "entry_price": 100.0,
        "quantity": 2.0, "realized_pnl": 9.0, "opened_at": opened,
        "closed_at": closed, "exit_plan_effective_at": opened,
    }
    payload1 = json.dumps({
        "stop_price": 95.0, "take_profit_price": 110.0,
        "exit_limit_price": None, "exit_limit_source": None,
        "trailing": {"enabled": False},
    }, sort_keys=True, separators=(",", ":"))
    plan1 = {
        "position_id": "position-1", "version": 1, "effective_at": opened,
        "stop_price": 95.0, "take_profit_price": 110.0,
        "exit_limit_price": None, "exit_limit_source": None,
        "plan_json": payload1, "plan_hash": hashlib.sha256(payload1.encode()).hexdigest(),
        "provenance": "paper_entry_protection_v2", "created_at": opened,
    }
    state = {
        "position_id": "position-1", "activated_at": opened, "trigger_id": None,
        "symbol": "BTC/USDT", "venue": "bitget", "market_type": "spot", "timeframe": "1m",
        "last_inspected_bar_open_ms": None, "last_evaluated_bar_open_ms": None,
        "next_bar_open_ms": None,
    }
    rule_json = json.dumps({
        "cost_model_version": "paper-fill-close-bar-v1",
        "exit_fee_rate": 0.001, "slippage_bps": 10.0,
    }, sort_keys=True, separators=(",", ":"))
    assignment = {
        "position_id": "position-1", "experiment_version": "exit-breakeven-shadow/v1",
        "venue": "bitget", "symbol": "BTC/USDT", "market_type": "spot", "timeframe": "1m",
        "direction": "long", "entry_price": 100.0,
        "quantity": 2.0, "entry_fee": 0.2, "exit_fee_rate": 0.001,
        "slippage_bps": 10.0, "exit_plan_version": 1,
        "exit_plan_effective_at": opened, "exit_plan_hash": plan1["plan_hash"],
        "behavior_authority": 0, "rule_json": rule_json,
        "rule_hash": hashlib.sha256(rule_json.encode()).hexdigest(),
    }
    order = {
        "id": "order-1", "position_id": "position-1", "proposal_id": "proposal-1",
        "close_reason": None, "symbol": "BTC/USDT", "direction": "long",
        "market_type": "spot",
    }
    fill = {"order_id": "order-1", "fill_price": 100.0, "fill_quantity": 2.0, "filled_at": opened}
    row = {
        "position_id": "position-1", "proposal_id": "proposal-1",
        "opportunity_id": "opportunity-1", "symbol": "BTC/USDT", "market_type": "spot",
        "direction": "long", "entry_price": 100.0, "quantity": 2.0,
        "entry_fee": 0.2, "opened_at": opened, "closed_at": closed,
        "close_reason": "take_profit", "path_evidence_status": "available",
        "mfe_pct": 8.0, "mae_pct": -3.0,
    }
    row.update(row_overrides)
    monkeypatch.setattr(geometry.repo, "get_trade_proposal", lambda _id: proposal_dict)
    monkeypatch.setattr(geometry.repo, "get_position", lambda _id: position)
    monkeypatch.setattr(geometry.repo, "get_order", lambda _id: order)
    monkeypatch.setattr(geometry.repo, "get_position_protection_state", lambda _id: state)
    monkeypatch.setattr(geometry.repo, "get_position_protection_trigger", lambda _id: None)
    monkeypatch.setattr(geometry.repo, "get_exit_breakeven_shadow_assignment", lambda _id: assignment)
    monkeypatch.setattr(geometry, "_read_plans", lambda _id, **_kwargs: [plan1])
    monkeypatch.setattr(geometry, "_entry_fill", lambda _position, **_kwargs: fill)
    # Unit fixtures isolate geometry from the file-backed global Proposal chain.
    monkeypatch.setattr(geometry, "_validate_proposal_chain", lambda _proposal, **_kwargs: True, raising=False)
    return row, proposal_dict, position, plan1, assignment, state


def test_calculates_only_verified_historical_geometry(monkeypatch):
    row, *_ = _evidence(monkeypatch)

    result = geometry._build_row(row)

    assert result["proposal_gross_risk_reward"] == pytest.approx(2.0)
    assert result["proposal_stop_distance"] == pytest.approx(5.0)
    assert result["proposal_target_distance"] == pytest.approx(10.0)
    assert result["initial_plan_provenance"] == "Operator"
    assert result["entry_atr_status"] == "unavailable"
    assert result["entry_atr_reason"] == "entry_atr_lineage_unavailable"
    assert result["initial_risk_amount"] == pytest.approx(10.0)
    assert result["realized_net_r"] == pytest.approx(0.9)
    # paper-fill-close-bar-v1 applies adverse sell-side slippage first, then
    # charges the exit fee on each resulting fill (stop and target differ).
    assert result["stop_fill_price"] == pytest.approx(94.905)
    assert result["target_fill_price"] == pytest.approx(109.89)
    assert result["stop_exit_fee"] == pytest.approx(0.18981)
    assert result["target_exit_fee"] == pytest.approx(0.21978)
    assert result["net_target_gain"] == pytest.approx(19.36022)
    assert result["net_stop_loss"] == pytest.approx(10.57981)
    assert result["break_even_win_rate"] == pytest.approx(10.57981 / 29.94003)
    assert result["mfe_r"] == pytest.approx(1.6)
    assert result["mae_r"] == pytest.approx(-0.6)
    assert result["path_ratio_precision"] == "derived_from_rounded_4dp_excursion_pct"


@pytest.mark.parametrize(
    ("mutation", "section", "reason"),
    [
        (lambda row, proposal, *_: proposal.update(risk_reward=3.0), "proposal_geometry", "proposal_risk_reward_mismatch"),
        (lambda row, proposal, position, *_: position.update(proposal_id="other"), "proposal_geometry", "proposal_position_identity_mismatch"),
        (lambda row, *_: row.update(path_evidence_status="bounded"), "path_normalization", "exact_trade_path_unavailable"),
    ],
)
def test_conflicting_or_bounded_evidence_fails_closed(monkeypatch, mutation, section, reason):
    evidence = _evidence(monkeypatch)
    mutation(*evidence)
    result = geometry._build_row(evidence[0])
    assert result[f"{section}_status"] == "unavailable"
    assert result[f"{section}_reason"] == reason


def test_malformed_plan_and_mismatched_cost_contract_do_not_leak_metrics(monkeypatch):
    row, _proposal, _position, plan, assignment, _state = _evidence(monkeypatch)
    plan["plan_hash"] = "0" * 64
    assignment["entry_fee"] = 99.0

    result = geometry._build_row(row)

    assert result["revision_history_reason"] == "exit_plan_json_or_hash_invalid"
    assert result["initial_risk_amount"] is None
    assert result["realized_net_r"] is None
    assert result["round_trip_cost"] is None
    assert result["cost_assumptions_status"] == "unavailable"


def test_lists_only_actual_trailing_revisions_and_validates_dual_hit(monkeypatch):
    row, _proposal, position, first, _assignment, state = _evidence(monkeypatch)
    effective2 = _iso(-20)
    payload2 = json.dumps({
        "stop_price": 97.0, "take_profit_price": 110.0,
        "exit_limit_price": None, "exit_limit_source": None,
        "trailing": {"enabled": True, "mode": "atr", "distance": 2.0},
    }, sort_keys=True, separators=(",", ":"))
    second = {**first, "version": 2, "effective_at": effective2, "created_at": effective2,
              "stop_price": 97.0, "plan_json": payload2,
              "plan_hash": hashlib.sha256(payload2.encode()).hexdigest(), "provenance": "trailing_v1"}
    position["exit_plan_effective_at"] = effective2
    monkeypatch.setattr(geometry, "_read_plans", lambda _id, **_kwargs: [first, second])
    bar_open_ms = int((datetime.fromisoformat(first["effective_at"]) + timedelta(minutes=5)).timestamp() * 1000)
    bar = {
        "venue": "bitget", "symbol": "BTC/USDT", "market_type": "spot", "timeframe": "1m",
        "bar_open_ms": bar_open_ms, "bar_close_ms": bar_open_ms + 60_000,
        "bar_open": 100.0, "bar_high": 111.0, "bar_low": 94.0, "bar_close": 101.0,
        "bar_volume": 4.0, "provider": "test",
    }
    trigger = {
        "position_id": "position-1", "trigger_id": "trigger-1", **bar,
        "bar_hash": repo.hash_position_protection_bar(**bar), "plan_version": 1,
        "plan_effective_at": first["effective_at"], "plan_hash": first["plan_hash"],
        "stop_price": 95.0, "upside_price": 110.0, "stop_hit": 1, "target_hit": 1,
        "reason": "stop_loss", "collision_policy": "stop_first_v1",
    }
    state.update({
        "trigger_id": "trigger-1",
        "last_inspected_bar_open_ms": bar["bar_open_ms"],
        "last_evaluated_bar_open_ms": bar["bar_open_ms"],
        "next_bar_open_ms": bar["bar_close_ms"],
    })
    monkeypatch.setattr(geometry.repo, "get_position_protection_trigger", lambda _id: trigger)

    result = geometry._build_row(row)

    assert [item["version"] for item in result["trailing_plan_revisions"]] == [2]
    assert result["trailing_plan_revisions"][0]["trailing"]["mode"] == "atr"
    assert result["protective_trigger_status"] == "available"
    assert result["protective_trigger"] == {
        "trigger_id": "trigger-1", "bar_open_ms": bar_open_ms,
        "bar_close_ms": bar_open_ms + 60_000, "bar_hash": trigger["bar_hash"],
        "plan_version": 1, "plan_hash": first["plan_hash"], "stop_hit": True,
        "target_hit": True, "dual_hit": True, "reason": "stop_loss",
        "collision_policy": "stop_first_v1",
    }
    assert result["protective_trigger_cross_evidence_status"] == "independent"

    # Stored flags must equal reconstructed crossings, including false negatives.
    trigger["target_hit"] = 0
    false_negative = geometry._build_row(row)
    assert false_negative["protective_trigger_status"] == "unavailable"
    assert false_negative["protective_trigger_reason"] == "protective_trigger_crossing_flags_mismatch"

    # A valid trigger cannot be cross-presented with a different path venue.
    trigger["target_hit"] = 1
    row.update({"ohlcv_exchange": "kraken", "ohlcv_market_type": "spot", "timeframe": "1m"})
    venue_mismatch = geometry._build_row(row)
    assert venue_mismatch["protective_trigger_status"] == "unavailable"
    assert venue_mismatch["protective_trigger_reason"] == "protective_trigger_path_identity_mismatch"


def test_rendering_preserves_unavailable_reasons_and_trigger_evidence(monkeypatch):
    row, *_ = _evidence(monkeypatch, path_evidence_status="bounded")
    built = geometry._build_row(row)
    text = geometry.render_entry_exit_geometry_audit({"hours": 24, "position_count": 1, "rows": [built]})
    encoded = json.loads(json.dumps(built))

    assert "Entry ATR: unavailable (entry_atr_lineage_unavailable)" in text
    assert "Rounded path ratios: unavailable (exact_trade_path_unavailable)" in text
    assert "Protective trigger: unavailable (protective_trigger_unavailable)" in text
    assert encoded["entry_atr"] is None
    assert encoded["mfe_r"] is None
    assert isinstance(encoded["proposal_gross_risk_reward"], float)


def test_builder_filters_before_display_limit_beyond_500_and_exposes_cohort(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        {
            "position_id": f"eth-{index}", "closed_at": (now - timedelta(seconds=index)).isoformat(),
            "symbol": "ETH/USDT", "outcome_eligible": 1,
            "outcome_exclusion_reason": "eligible", "outcome_semantics_version": "oi-v1",
        }
        for index in range(501)
    ]
    rows.append({
        "position_id": "btc", "closed_at": (now - timedelta(minutes=20)).isoformat(),
        "symbol": "BTC/USDT", "outcome_eligible": 1,
        "outcome_exclusion_reason": "eligible", "outcome_semantics_version": "oi-v1",
    })
    captured = {}
    def fake_paths(**kwargs):
        captured.update(kwargs)
        return rows[:kwargs["limit"]], False
    monkeypatch.setattr(geometry, "build_trade_path_rows", fake_paths)
    monkeypatch.setattr(geometry, "_build_row", lambda row, **_kwargs: dict(row))

    report = geometry.build_entry_exit_geometry_audit(hours=24, limit=1, symbol="BTC/USDT")

    assert [row["position_id"] for row in report["rows"]] == ["btc"]
    assert report["cohort_identity"] == {
        "basis": "current_eligible_strategy_outcomes_closed_in_window",
        "outcome_semantics_version": "oi-v1",
        "symbol": "BTC/USDT",
    }
    assert report["cohort_eligible_count"] == 1
    assert report["cohort_exclusions"] == {
        "pre_path_outcome_exclusions": "not_included_by_eligible_strategy_outcomes",
        "outside_close_window": 0,
        "symbol_mismatch": 501,
        "source_truncated": False,
    }
    assert captured["symbol"] == "BTC/USDT"
    assert captured["closed_after"]
    assert captured["include_source_status"] is True


def test_short_close_costs_match_frozen_paper_fill_formula():
    stop = geometry._paper_close_economics(
        direction="short", reference_price=105.0, entry_price=100.0,
        quantity=2.0, entry_fee=0.2, exit_fee_rate=0.001, slippage_bps=10.0,
    )
    target = geometry._paper_close_economics(
        direction="short", reference_price=90.0, entry_price=100.0,
        quantity=2.0, entry_fee=0.2, exit_fee_rate=0.001, slippage_bps=10.0,
    )

    # Closing a short buys: adverse slippage raises both fills; fee uses fill not reference.
    assert stop["fill_price"] == pytest.approx(105.105)
    assert stop["exit_fee"] == pytest.approx(0.21021)
    assert stop["net_pnl"] == pytest.approx(-10.62021)
    assert target["fill_price"] == pytest.approx(90.09)
    assert target["exit_fee"] == pytest.approx(0.18018)
    assert target["net_pnl"] == pytest.approx(19.43982)


def test_proposal_chain_failure_gates_geometry_initial_risk_and_costs(monkeypatch):
    row, *_ = _evidence(monkeypatch)
    monkeypatch.setattr(geometry, "_validate_proposal_chain", lambda _proposal, **_kwargs: False)

    result = geometry._build_row(row)

    assert result["proposal_geometry_reason"] == "proposal_chain_identity_or_hash_invalid"
    assert result["initial_risk_status"] == "unavailable"
    assert result["realized_net_r"] is None
    assert result["cost_assumptions_status"] == "unavailable"


def test_canonical_plan_hash_accepts_equivalent_json(monkeypatch):
    row, _proposal, _position, plan, *_ = _evidence(monkeypatch)
    payload = json.loads(plan["plan_json"])
    plan["plan_json"] = json.dumps(payload, indent=2, sort_keys=False)
    plan["plan_hash"] = repo.hash_position_exit_plan_json(plan["plan_json"])

    result = geometry._build_row(row)

    assert result["initial_risk_status"] == "available"
    assert result["revision_history_status"] == "available"


def test_later_malformed_revision_does_not_invalidate_v1_metrics(monkeypatch):
    row, _proposal, position, first, *_ = _evidence(monkeypatch)
    effective2 = _iso(-20)
    second = {
        **first, "version": 2, "effective_at": effective2, "created_at": effective2,
        "plan_hash": "0" * 64,
    }
    position["exit_plan_effective_at"] = effective2
    monkeypatch.setattr(geometry, "_read_plans", lambda _id, **_kwargs: [first, second])

    result = geometry._build_row(row)

    assert result["revision_history_reason"] == "exit_plan_json_or_hash_invalid"
    assert result["initial_risk_status"] == "available"
    assert result["realized_net_r"] == pytest.approx(0.9)
    assert result["cost_assumptions_status"] == "available"


def test_rejects_retrospectively_created_later_revision(monkeypatch):
    row, _proposal, position, first, *_ = _evidence(monkeypatch)
    effective2 = _iso(-20)
    payload2 = json.dumps({
        "stop_price": 97.0, "take_profit_price": 110.0,
        "exit_limit_price": None, "exit_limit_source": None,
        "trailing": {"enabled": True},
    }, sort_keys=True, separators=(",", ":"))
    second = {
        **first, "version": 2, "effective_at": effective2, "created_at": _iso(-15),
        "stop_price": 97.0, "plan_json": payload2,
        "plan_hash": repo.hash_position_exit_plan_json(payload2),
    }
    position["exit_plan_effective_at"] = effective2
    monkeypatch.setattr(geometry, "_read_plans", lambda _id, **_kwargs: [first, second])

    result = geometry._build_row(row)

    assert result["revision_history_status"] == "unavailable"
    assert result["revision_history_reason"] == "retrospective_exit_plan_revision"
    assert result["initial_risk_status"] == "available"


def test_file_backed_chain_validation_and_audit_are_read_only(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()
    root = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long", quantity=1.0)
    child = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="short", quantity=1.0)
    repo.insert_trade_proposal(root)
    repo.insert_trade_proposal(child)
    stored = repo.get_trade_proposal(child.id)
    assert geometry._validate_proposal_chain(stored) is True
    conn.execute("UPDATE trade_proposals SET quantity=2.0 WHERE id=?", (child.id,))
    tampered = repo.get_trade_proposal(child.id)
    before_changes = conn.total_changes
    monkeypatch.setattr(geometry, "build_trade_path_rows", lambda **_kwargs: ([], False))

    assert geometry._validate_proposal_chain(tampered) is False
    report = geometry.build_entry_exit_geometry_audit(hours=24, limit=10)

    assert report["rows"] == []
    assert conn.total_changes == before_changes
    sq._db_conn = None
    conn.close()

def test_chain_skips_only_leading_legacy_rows_and_requires_hashed_target(tmp_path, monkeypatch):
    import sqlite3

    legacy = TradeProposal(id="legacy", symbol="OLD/USDT", market_type="spot", direction="long", quantity=1.0)
    target = TradeProposal(id="target", symbol="BTC/USDT", market_type="spot", direction="long", quantity=1.0)
    target.commit_hash = repo._compute_proposal_hash(target)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE trade_proposals AS SELECT * FROM json_each('{}') WHERE 0")
    columns = list(target.model_dump())
    db.execute("DROP TABLE trade_proposals")
    db.execute("CREATE TABLE trade_proposals (" + ",".join(f"{name}" + (" REAL" if name in {"quantity","price","stop_price","take_profit","risk_reward"} else " TEXT") for name in columns) + ")")
    def insert(model):
        data = model.model_dump()
        db.execute("INSERT INTO trade_proposals (" + ",".join(data) + ") VALUES (" + ",".join("?" for _ in data) + ")", tuple(data.values()))
    insert(legacy)
    insert(target)
    assert geometry._validate_proposal_chain(target.model_dump(), db=db) is True
    db.execute("INSERT INTO trade_proposals SELECT * FROM trade_proposals WHERE id='legacy'")
    db.execute("UPDATE trade_proposals SET id='gap' WHERE rowid=(SELECT max(rowid) FROM trade_proposals)")
    later = TradeProposal(id="later", symbol="ETH/USDT", market_type="spot", direction="long", quantity=1.0, parent_hash=target.commit_hash)
    later.commit_hash = repo._compute_proposal_hash(later)
    insert(later)
    assert geometry._validate_proposal_chain(later.model_dump(), db=db) is False
    unhashed = target.model_copy(update={"commit_hash": None}).model_dump()
    assert geometry._validate_proposal_chain(unhashed, db=db) is False
def test_unknown_source_truncation_and_unavailable_absent_database(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sq.reset_db()
    report = geometry.build_entry_exit_geometry_audit()
    assert report["status"] == "unavailable"
    assert report["cohort_exclusions"]["source_truncated"] is None
    assert not (tmp_path / "app.db").exists()
    assert not (tmp_path / "app.write.lock").exists()


def test_order_market_and_perp_funding_fail_closed(monkeypatch):
    row, proposal, position, _plan, assignment, state = _evidence(monkeypatch)
    order = {"id": "order-1", "position_id": "position-1", "proposal_id": "proposal-1", "close_reason": None, "symbol": "BTC/USDT", "direction": "long", "market_type": "perp"}
    monkeypatch.setattr(geometry.repo, "get_order", lambda _id: order)
    assert geometry._build_row(row)["initial_risk_reason"] == "entry_fill_or_initial_plan_malformed"
    proposal["market_type"] = position["market_type"] = row["market_type"] = "usdt_futures"
    order["market_type"] = "usdt_futures"
    assignment["market_type"] = state["market_type"] = "perp"
    result = geometry._build_row(row)
    assert result["initial_risk_status"] == "available"
    assert result["cost_assumptions_reason"] == "perp_barrier_funding_unverified"
    assert result["net_target_gain"] is None


def test_cli_routes_entry_exit_and_rejects_opportunity_filters(monkeypatch):
    report = {"view": "entry-exit", "rows": []}
    monkeypatch.setattr(geometry, "build_entry_exit_geometry_audit", lambda **kwargs: {**report, **kwargs})
    monkeypatch.setattr(geometry, "render_entry_exit_geometry_audit", lambda value: f"rendered {value['hours']}")

    built, rendered = _build_audit_view(hours=12, limit=7, stage=None, verdict=None, symbol="BTC/USDT", view="entry-exit")

    assert built["symbol"] == "BTC/USDT"
    assert rendered == "rendered 12"
    with pytest.raises(typer.BadParameter, match="--stage not supported"):
        _build_audit_view(hours=12, limit=7, stage="proposed", verdict=None, symbol=None, view="entry-exit")
    with pytest.raises(typer.BadParameter, match="--verdict not supported"):
        _build_audit_view(hours=12, limit=7, stage=None, verdict="won", symbol=None, view="entry-exit")
