from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic import ValidationError

from aletheia.config.schema import AletheiaConfig
from aletheia.trading.trading_mandate import (
    TRADING_MANDATE_FIT_VERSION,
    TRADING_MANDATE_VERSION,
    EdgeScope,
    TradingMandate,
    assess_trading_mandate_fit,
    build_trading_mandate_provenance,
    freeze_trading_mandate_provenance,
    resolve_trading_mandate,
)


def _mandate(**overrides) -> TradingMandate:
    payload = {
        "contract_version": TRADING_MANDATE_VERSION,
        "mandate_id": "crypto-paper-observe",
        "mandate_version": 1,
        "status": "active",
        "enforcement_mode": "observe_only",
        "market_scope": {
            "allowed_asset_classes": ["crypto"],
            "allowed_physical_market_types": ["spot", "perp"],
            "allowed_venues": ["bitget"],
            "allowed_symbols": ["BTC/USDT", "ETH/USDT"],
        },
        "edge_scope": {
            "allowed_candidate_families": ["momentum", "reversion"],
            "allowed_directions": ["long", "short"],
            "min_intended_holding_horizon_hours": 1,
            "max_intended_holding_horizon_hours": 12,
        },
        "default_behavior": {
            "insufficient_evidence": "prefer_skip",
            "conflicting_directional_evidence": "not_declared",
            "unavailable_cost_estimate": "not_declared",
        },
        "holding_governance": {
            "expected_horizon_reached": "review_required",
            "opening_hypothesis_invalidated": "review_required",
        },
    }
    payload.update(overrides)
    return TradingMandate.model_validate(payload)


def _facts(*, market_type: str = "spot", family: str = "momentum", horizon: float = 4):
    opportunity = {
        "id": "opp-1",
        "asset_class": "crypto",
        "scanner_family": family,
    }
    decision = {"id": "decision-1", "direction": "long"}
    proposal = {
        "id": "proposal-1",
        "opportunity_id": "opp-1",
        "symbol": "BTC/USDT",
        "market_type": market_type,
        "direction": "long",
        "expected_horizon_hours": horizon,
        "attribution_snapshot": json.dumps(
            {"market_context": {"instrument": {"exchange": "Bitget"}}}
        ),
    }
    return opportunity, decision, proposal


def test_valid_mandate_normalizes_collections_and_hashes_stably():
    first = _mandate()
    second = TradingMandate.model_validate(
        {
            **first.model_dump(mode="json"),
            "market_scope": {
                **first.market_scope.model_dump(mode="json"),
                "allowed_asset_classes": ["CRYPTO", "crypto"],
                "allowed_physical_market_types": ["USDT_FUTURES", "spot"],
                "allowed_symbols": ["eth/usdt", "BTC/USDT"],
            },
            "edge_scope": {
                **first.edge_scope.model_dump(mode="json"),
                "allowed_candidate_families": ["reversion", "Momentum", "momentum"],
            },
        }
    )

    assert first.market_scope.allowed_asset_classes == ("crypto",)
    assert second.market_scope.allowed_physical_market_types == ("perp", "spot")
    assert second.edge_scope.allowed_candidate_families == ("momentum", "reversion")
    assert first.content_hash() == second.content_hash()
    assert first.identity()["content_hash"].startswith("sha256:")
    assert _mandate(mandate_version=2).content_hash() != first.content_hash()
    assert _mandate(default_behavior={"insufficient_evidence": "prefer_skip"}).content_hash() == first.content_hash()


def test_invalid_enforcement_and_horizon_fail_closed():
    with pytest.raises(ValidationError):
        _mandate(enforcement_mode="block")
    with pytest.raises(ValidationError):
        _mandate(edge_scope={"min_intended_holding_horizon_hours": 12, "max_intended_holding_horizon_hours": 1})
    with pytest.raises(ValidationError):
        _mandate(edge_scope={"min_intended_holding_horizon_hours": float("nan")})
    with pytest.raises(ValidationError):
        _mandate(market_scope={"allowed_asset_classes": [], "allowed_physical_market_types": ["spot"]})
    with pytest.raises(ValidationError):
        _mandate(market_scope={"allowed_asset_classes": ["crypto"], "allowed_physical_market_types": ["margin"]})


def test_config_missing_disabled_and_malformed_are_explicit():
    assert resolve_trading_mandate(AletheiaConfig()).status == "not_configured"
    disabled = AletheiaConfig.model_validate({"trading_mandate": {"enabled": False}})
    assert resolve_trading_mandate(disabled).status == "not_configured"
    malformed = AletheiaConfig.model_validate({"trading_mandate": {"enabled": True, "mandate_id": "x"}})
    result = resolve_trading_mandate(malformed)
    assert result.status == "mandate_unavailable"
    assert "market_scope" in result.validation_errors
    coercion = AletheiaConfig.model_validate(
        {"trading_mandate": {"enabled": True, "mandate_version": "1"}}
    )
    assert resolve_trading_mandate(coercion).status == "mandate_unavailable"
    unknown_null = AletheiaConfig.model_validate(
        {"trading_mandate": {"enabled": True, "typo": None}}
    )
    assert resolve_trading_mandate(unknown_null).status == "mandate_unavailable"
    active = AletheiaConfig.model_validate({"trading_mandate": {"enabled": True, **_mandate().model_dump(mode="json")}})
    assert resolve_trading_mandate(active).status == "active"


def test_enabled_mandate_round_trips_through_yaml_without_activation_drift(tmp_path, monkeypatch):
    from aletheia.config import loader

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    loader._invalidate_config_cache()
    cfg = AletheiaConfig.model_validate(
        {"trading_mandate": {"enabled": True, **_mandate().model_dump(mode="json")}}
    )
    loader.save_config(cfg)
    loaded = loader.load_config(persist_migrations=False)
    assert resolve_trading_mandate(loaded).identity == _mandate().identity()


def test_fit_matching_spot_and_perp_and_disallowed_dimensions_are_observations():
    mandate = _mandate()
    for market_type in ("spot", "usdt_futures"):
        opportunity, decision, proposal = _facts(market_type=market_type)
        result = assess_trading_mandate_fit(
            mandate=mandate, opportunity=opportunity, decision=decision, proposal=proposal
        )
        assert result["status"] == "in_scope"
        assert result["behavior_changed"] is False
        assert all(check["status"] in {"matched", "not_declared"} for check in result["checks"])

    opportunity, decision, proposal = _facts()
    opportunity["asset_class"] = "equity"
    result = assess_trading_mandate_fit(
        mandate=mandate, opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert result["status"] == "out_of_scope"
    assert result["reasons"] == ["asset_class_disallowed"]

    opportunity, decision, proposal = _facts(market_type="margin")
    result = assess_trading_mandate_fit(
        mandate=mandate, opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert result["status"] == "not_assessable"
    assert result["reasons"] == ["market_type_unavailable"]


def test_fit_optional_dimensions_missing_and_unconfigured_are_deterministic():
    mandate = _mandate()
    opportunity, decision, proposal = _facts(family="unknown", horizon=20)
    result = assess_trading_mandate_fit(
        mandate=mandate, opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert result["status"] == "out_of_scope"
    assert result["reasons"] == ["candidate_family_disallowed", "holding_horizon_incompatible"]

    opportunity, decision, proposal = _facts()
    del opportunity["asset_class"]
    del proposal["expected_horizon_hours"]
    result = assess_trading_mandate_fit(
        mandate=mandate, opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert result["status"] == "not_assessable"
    assert [check["reason"] for check in result["checks"] if check["status"] == "not_assessable"] == [
        "asset_class_unavailable",
        "holding_horizon_unavailable",
    ]

    result = assess_trading_mandate_fit(
        mandate=None, opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert result["status"] == "not_configured"
    assert result["contract_version"] == TRADING_MANDATE_FIT_VERSION
    assert result["behavior_changed"] is False


def test_frozen_pair_is_idempotent_and_conflicts_fail_closed():
    opportunity, decision, proposal = _facts()
    evidence = build_trading_mandate_provenance(
        configured=_mandate(), opportunity=opportunity, decision=decision, proposal=proposal
    )
    snapshot: dict = {}
    freeze_trading_mandate_provenance(snapshot, evidence)
    original = json.dumps(snapshot, sort_keys=True)
    freeze_trading_mandate_provenance(snapshot, evidence)
    assert json.dumps(snapshot, sort_keys=True) == original
    conflicting = {
        **evidence,
        "trading_mandate_identity": {**evidence["trading_mandate_identity"], "mandate_version": 2},
    }
    with pytest.raises(ValueError, match="immutable"):
        freeze_trading_mandate_provenance(snapshot, conflicting)


def test_new_native_paper_proposal_freezes_once_and_config_changes_do_not_rewrite(
    tmp_path, monkeypatch
):
    from aletheia.config import loader
    from aletheia.storage import repositories as repo
    from aletheia.storage import sqlite as sqlite_store
    from aletheia.storage.migrations import apply_migrations
    from aletheia.storage.models import Opportunity, TradeProposal
    from aletheia.storage.proposal_hypothesis import freeze_position_hypothesis

    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    connection = sqlite3.connect(str(tmp_path / "app.db"))
    connection.row_factory = sqlite3.Row
    sqlite_store._db_conn = connection
    apply_migrations()
    repo.insert_opportunity(
        Opportunity(id="opportunity-freeze", symbol="BTC/USDT", market="spot")
    )
    configured = AletheiaConfig.model_validate(
        {"trading_mandate": {"enabled": True, **_mandate().model_dump(mode="json")}}
    )
    monkeypatch.setattr(loader, "load_config", lambda **_: configured)
    proposal = TradeProposal(
        id="proposal-freeze",
        opportunity_id="opportunity-freeze",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
    )
    repo.insert_trade_proposal(proposal)
    stored = repo.get_trade_proposal(proposal.id)
    snapshot = json.loads(stored["attribution_snapshot"])
    assert snapshot["trading_mandate_identity"] == _mandate().identity()
    assert snapshot["trading_mandate_fit"]["contract_version"] == TRADING_MANDATE_FIT_VERSION
    original_hash = stored["commit_hash"]

    # Current config is irrelevant to already-frozen evidence.
    disabled = AletheiaConfig.model_validate({"trading_mandate": {"enabled": False}})
    monkeypatch.setattr(loader, "load_config", lambda **_: disabled)
    assert repo.get_trade_proposal(proposal.id)["commit_hash"] == original_hash

    changed = dict(snapshot)
    changed["trading_mandate_fit"] = dict(changed["trading_mandate_fit"])
    changed["trading_mandate_fit"]["status"] = "out_of_scope"
    proposal.attribution_snapshot = json.dumps(changed)
    with pytest.raises(ValueError, match="immutable"):
        freeze_position_hypothesis(proposal, db=connection)
    sqlite_store.reset_db()


def test_assessment_does_not_mutate_behavior_inputs():
    opportunity, decision, proposal = _facts()
    behavior = {
        "action": "trade",
        "direction": "long",
        "score": 87,
        "conviction": 82,
        "quantity": 0.1,
        "notional": 100.0,
        "leverage": 1,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "expected_horizon_hours": 4.0,
        "risk_decision": "approved",
        "execution_path": "paper_internal",
        "position_protection": "unchanged",
        "learning_assignment": "static_baseline",
        "universe_family_calibration": "unchanged",
    }
    before = json.loads(json.dumps(behavior))
    assess_trading_mandate_fit(
        mandate=_mandate(), opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert behavior == before


def test_compliance_aggregate_is_read_only_and_keeps_denominators(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.storage import sqlite as sqlite_store
    from aletheia.storage.migrations import apply_migrations
    from aletheia.analytics.trading_mandate_compliance import build_mandate_compliance_report

    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    sqlite_store._db_conn = db
    apply_migrations()
    db.execute(
        "INSERT INTO trade_proposals (id,symbol,market_type,direction,execution_mode,created_at) "
        "VALUES ('legacy-proposal','BTC/USDT','spot','long','paper_internal','2026-01-01T00:00:00+00:00')"
    )
    db.commit()
    before = db.total_changes
    report = build_mandate_compliance_report(hours=720, db=db)
    assert db.total_changes == before
    assert report["read_only"] is True
    assert report["counts"]["proposals_by_fit_status"]["not_recorded"] == 0
    assert report["denominators"]["proposal_fit_status_denominator"] == 0
    sqlite_store.reset_db()