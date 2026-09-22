from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aletheia.config.schema import AletheiaConfig
from aletheia.trading.trading_mandate import (
    TRADING_MANDATE_FIT_VERSION,
    TRADING_MANDATE_VERSION,
    EdgeScope,
    TradingMandate,
    assess_trading_mandate_fit,
    resolve_trading_mandate,
    validate_frozen_trading_mandate_provenance,
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


def _setup_mandate_db(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.storage import repositories as repo
    from aletheia.storage import sqlite as sqlite_store
    from aletheia.storage.migrations import apply_migrations

    sqlite_store.reset_db()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    sqlite_store._db_conn = db
    apply_migrations()
    configured = AletheiaConfig.model_validate(
        {"trading_mandate": {"enabled": True, **_mandate().model_dump(mode="json")}}
    )
    monkeypatch.setattr(loader, "load_config", lambda **_: configured)
    return repo, sqlite_store, db, configured


def _archived_mandate_evidence(*, configured, opportunity, decision, proposal):
    return {
        "trading_mandate_identity": configured.identity(),
        "trading_mandate_fit": assess_trading_mandate_fit(
            mandate=configured, opportunity=opportunity, decision=decision, proposal=proposal,
        ),
    }


def _insert_mandate_proposal(
    repo,
    proposal_id: str,
    *,
    configured: AletheiaConfig,
    created_at: str | None = None,
    execution_mode: str = "paper_internal",
):
    from aletheia.storage.models import Opportunity, TradeProposal

    opportunity_id = f"{proposal_id}-opp"
    repo.insert_opportunity(
        Opportunity(
            id=opportunity_id,
            symbol="BTC/USDT",
            market="spot",
            scanner_family="momentum",
        )
    )
    proposal = TradeProposal(
        id=proposal_id,
        execution_mode=execution_mode,
        opportunity_id=opportunity_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        expected_horizon_hours=4.0,
        attribution_snapshot=json.dumps(
            {"market_context": {"instrument": {"exchange": "Bitget"}}}
        ),
        **({"created_at": created_at} if created_at is not None else {}),
    )
    # Historical rows are fixtures, never recreated through the active producer.
    snapshot = json.loads(proposal.attribution_snapshot)
    snapshot.update(_archived_mandate_evidence(
        configured=resolve_trading_mandate(configured).mandate,
        opportunity=repo.get_opportunity(opportunity_id), decision=None, proposal=proposal,
    ))
    proposal.attribution_snapshot = json.dumps(snapshot)
    proposal.commit_hash = repo._compute_proposal_hash(proposal)
    repo._insert("trade_proposals", proposal.model_dump())
    return proposal


def _tamper_mandate_snapshot(
    db: sqlite3.Connection,
    proposal_id: str,
    tamper: str = "fit_status",
) -> None:
    if tamper == "missing_hash":
        db.execute(
            "UPDATE trade_proposals SET commit_hash=NULL WHERE id=?",
            (proposal_id,),
        )
        db.commit()
        return
    row = db.execute(
        "SELECT attribution_snapshot FROM trade_proposals WHERE id=?",
        (proposal_id,),
    ).fetchone()
    snapshot = json.loads(row["attribution_snapshot"])
    fit = dict(snapshot["trading_mandate_fit"])
    if tamper == "identity":
        snapshot["trading_mandate_identity"] = {
            **snapshot["trading_mandate_identity"],
            "mandate_version": 2,
        }
    elif tamper == "fit_checks":
        fit["checks"] = [{**fit["checks"][0], "reason": "tampered_reason"}]
    elif tamper == "fit_reasons":
        fit["reasons"] = ["tampered_reason"]
    elif tamper == "source_ids":
        fit["source_identities"] = {
            **fit["source_identities"],
            "proposal_id": "tampered-proposal",
        }
    else:
        fit["status"] = "out_of_scope"
        fit["reasons"] = ["tampered_reason"]
    snapshot["trading_mandate_fit"] = fit
    db.execute(
        "UPDATE trade_proposals SET attribution_snapshot=? WHERE id=?",
        (json.dumps(snapshot), proposal_id),
    )
    db.commit()


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


@pytest.mark.parametrize("enabled", [0, None, "", [], {}])
def test_false_like_enabled_values_are_unavailable_not_not_configured(enabled):
    cfg = AletheiaConfig.model_validate({"trading_mandate": {"enabled": enabled}})
    assert resolve_trading_mandate(cfg).status == "mandate_unavailable"


def test_malformed_collection_and_horizon_types_fail_closed_without_type_error():
    for field, value in (
        ("market_scope", {"allowed_asset_classes": 1}),
        ("edge_scope", {"max_intended_holding_horizon_hours": "12"}),
    ):
        cfg = AletheiaConfig.model_validate(
            {"trading_mandate": {"enabled": True, field: value}}
        )
        result = resolve_trading_mandate(cfg)
        assert result.status == "mandate_unavailable"


def test_frozen_identity_requires_complete_contract_shape():
    opportunity, decision, proposal = _facts()
    evidence = _archived_mandate_evidence(
        configured=_mandate(), opportunity=opportunity, decision=decision, proposal=proposal
    )
    for identity in (
        {},
        {**evidence["trading_mandate_identity"], "content_hash": "bogus"},
    ):
        with pytest.raises(ValueError):
            validate_frozen_trading_mandate_provenance(
                {
                    "trading_mandate_identity": identity,
                    "trading_mandate_fit": evidence["trading_mandate_fit"],
                }
            )


def _frozen_mandate_snapshot() -> str:
    opportunity, decision, proposal = _facts()
    evidence = _archived_mandate_evidence(
        configured=_mandate(),
        opportunity=opportunity,
        decision=decision,
        proposal=proposal,
    )
    return json.dumps(evidence)


def test_completed_position_duration_requires_genuine_close():
    from aletheia.analytics.trading_mandate_compliance import _duration

    assert _duration({"opened_at": "2026-01-01T00:00:00+00:00", "closed_at": None}) is None
    assert _duration({
        "opened_at": "2026-01-01T00:00:00+00:00",
        "closed_at": "2026-01-01T06:00:00+00:00",
    }) == 6.0


def test_aggregate_window_bounds_are_inclusive():
    from aletheia.analytics.trading_mandate_compliance import (
        _event_window_exclusion,
    )

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    window_to = datetime(2026, 1, 2, tzinfo=timezone.utc)

    assert _event_window_exclusion(
        cutoff.isoformat(),
        cutoff=cutoff,
        window_to=window_to,
        field="created_at",
    ) is None
    assert _event_window_exclusion(
        window_to.isoformat(),
        cutoff=cutoff,
        window_to=window_to,
        field="created_at",
    ) is None
    assert _event_window_exclusion(
        (window_to + timedelta(microseconds=1)).isoformat(),
        cutoff=cutoff,
        window_to=window_to,
        field="created_at",
    ) == "created_at_future"


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


def test_external_proposal_keeps_hypothesis_without_recreating_archived_mandate(tmp_path, monkeypatch):
    from aletheia.storage.models import Opportunity, TradeProposal

    repo, sqlite_store, db, _configured = _setup_mandate_db(tmp_path, monkeypatch)
    repo.insert_opportunity(Opportunity(id="external-opp", symbol="BTC/USDT", market="spot"))
    proposal = TradeProposal(
        id="external-proposal", execution_mode="bitget_demo", opportunity_id="external-opp",
        symbol="BTC/USDT", market_type="spot", direction="long", quantity=1.0, price=100.0,
    )
    repo.insert_trade_proposal(proposal)
    snapshot = json.loads(repo.get_trade_proposal(proposal.id)["attribution_snapshot"])
    assert "position_hypothesis" in snapshot
    assert "trading_mandate_identity" not in snapshot
    assert "trading_mandate_fit" not in snapshot
    injected = proposal.model_copy(update={
        "id": "injected-proposal", "commit_hash": None,
        "attribution_snapshot": _frozen_mandate_snapshot(),
    })
    with pytest.raises(ValueError, match="production is retired"):
        repo.insert_trade_proposal(injected)
    assert repo.get_trade_proposal(injected.id) is None
    sqlite_store.reset_db()


def test_assessment_does_not_mutate_behavior_inputs():
    from copy import deepcopy

    opportunity, decision, proposal = _facts()
    before = deepcopy((opportunity, decision, proposal))
    assess_trading_mandate_fit(
        mandate=_mandate(), opportunity=opportunity, decision=decision, proposal=proposal
    )
    assert (opportunity, decision, proposal) == before


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


def test_compliance_aggregate_uses_completed_event_time_cohort(tmp_path, monkeypatch):
    import aletheia.analytics.trading_mandate_compliance as compliance

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
            return current if tz is not None else current.replace(tzinfo=None)

    repo, sqlite_store, db, configured = _setup_mandate_db(tmp_path, monkeypatch)
    monkeypatch.setattr(compliance, "datetime", FixedDatetime)
    now = FixedDatetime.now(timezone.utc)
    for proposal_id, created_at in (
        ("proposal-current", now - timedelta(hours=3)),
        ("proposal-cutoff", now - timedelta(hours=24)),
        ("proposal-window-end", now),
        ("proposal-future", now + timedelta(minutes=1)),
    ):
        _insert_mandate_proposal(
            repo,
            proposal_id,
            configured=configured,
            created_at=created_at.isoformat(),
        )
    for position_id, opened_at, closed_at, status in (
        ("position-closed", now - timedelta(hours=3), now - timedelta(hours=1), "closed"),
        ("position-open", now - timedelta(hours=20), None, "open"),
        ("position-future-open", now + timedelta(minutes=1), None, "open"),
        (
            "position-future-close",
            now - timedelta(hours=2),
            now + timedelta(minutes=1),
            "closed",
        ),
        (
            "position-bad-close",
            now - timedelta(hours=2),
            now - timedelta(hours=3),
            "closed",
        ),
    ):
        db.execute(
            "INSERT INTO positions "
            "(id,symbol,market_type,direction,quantity,entry_price,execution_mode,"
            "proposal_id,status,opened_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                position_id,
                "BTC/USDT",
                "spot",
                "long",
                1.0,
                100.0,
                "paper_internal",
                "proposal-current",
                status,
                opened_at.isoformat(),
                closed_at.isoformat() if closed_at else None,
            ),
        )
    db.commit()

    report = compliance.build_mandate_compliance_report(hours=24, db=db)

    assert report["counts"]["proposals_total"] == 3
    assert report["counts"]["positions_total"] == 4
    assert report["denominators"]["proposal_fit_status_denominator"] == 3
    assert report["denominators"]["position_fit_status_denominator"] == 4
    assert report["exclusions"]["cohort_by_reason"] == {
        "position_opened_at_future": 1,
        "proposal_created_at_future": 1,
    }
    duration = report["holding_duration_compatibility"]
    assert duration["declared_horizon_positions"] == 4
    assert duration["measurable_duration_positions"] == 1
    assert duration["compatible_count"] == 1
    assert duration["incompatible_count"] == 0
    assert duration["not_available_count"] == 3
    assert duration["not_measurable_by_reason"] == {
        "closed_at_future": 1,
        "closed_before_open": 1,
        "position_open": 1,
    }
    overrun = report["horizon_overrun"]
    assert overrun["declared_max_horizon_positions"] == 4
    assert overrun["measurable_count"] == 1
    assert overrun["overrun_count"] == 0
    assert overrun["not_measurable_count"] == 3
    assert overrun["not_measurable_by_reason"] == duration["not_measurable_by_reason"]
    sqlite_store.reset_db()


@pytest.mark.parametrize(
    "tamper",
    ["identity", "fit_status", "fit_checks", "fit_reasons", "source_ids", "missing_hash"],
)
def test_compliance_rejects_hash_mismatched_frozen_mandate(
    tmp_path,
    monkeypatch,
    tamper,
):
    from aletheia.analytics.trading_mandate_compliance import (
        build_mandate_compliance_report,
    )

    repo, sqlite_store, db, configured = _setup_mandate_db(tmp_path, monkeypatch)
    proposal = _insert_mandate_proposal(
        repo,
        "proposal-integrity",
        configured=configured,
    )

    repo.update_trade_proposal(proposal.id, status="approved")
    valid = build_mandate_compliance_report(proposal_id=proposal.id, db=db)
    assert valid["availability"] == "available"
    assert valid["fit_status"] == "in_scope"

    _tamper_mandate_snapshot(db, proposal.id, tamper)
    report = build_mandate_compliance_report(proposal_id=proposal.id, db=db)

    assert report["availability"] == "unavailable"
    assert report["unavailable_reason"] == "proposal_integrity_mismatch"
    assert report["trading_mandate_identity"] is None
    assert report["trading_mandate_fit"] is None
    assert report["fit_status"] == "mandate_unavailable"
    sqlite_store.reset_db()


def test_compliance_aggregate_excludes_hash_mismatched_mandate_cohorts(
    tmp_path,
    monkeypatch,
):
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.analytics.trading_mandate_compliance import (
        build_mandate_compliance_report,
    )
    from aletheia.storage.models import Fill, Order, Position

    repo, sqlite_store, db, configured = _setup_mandate_db(tmp_path, monkeypatch)
    # Frozen external history exercises Outcome admission; retired simulator
    # history is no longer eligible. This does not enable current mandate use.
    valid = _insert_mandate_proposal(
        repo, "proposal-valid", configured=configured, execution_mode="bitget_demo",
    )
    tampered = _insert_mandate_proposal(
        repo,
        "proposal-tampered",
        configured=configured,
        execution_mode="bitget_demo",
    )
    for proposal in (valid, tampered):
        position_id = f"{proposal.id}-position"
        order_id = f"{proposal.id}-order"
        repo._insert("orders",
            Order(
                id=order_id,
                proposal_id=proposal.id,
                position_id=position_id,
                symbol="BTC/USDT",
                market_type="spot",
                direction="long",
                order_type="market",
                quantity=1.0,
                price=100.0,
                execution_mode=proposal.execution_mode,
                status="filled",
                source="adopted",
            ).model_dump()
        )
        repo._insert("positions",
            Position(
                id=position_id,
                proposal_id=proposal.id,
                order_id=order_id,
                opportunity_id=proposal.opportunity_id,
                symbol="BTC/USDT",
                market_type="spot",
                direction="long",
                quantity=1.0,
                entry_price=100.0,
                current_price=110.0,
                realized_pnl=10.0,
                execution_mode=proposal.execution_mode,
                status="closed",
                close_category="strategy",
                entry_price_source="broker_fill",
                entry_fill_verified=True,
                exit_price_source="broker_fill",
                exit_fill_verified=True,
                outcome_eligible=True,
                outcome_exclusion_reason="eligible",
                outcome_semantics_version=OUTCOME_SEMANTICS_VERSION,
                funding_cost=0.0,
                funding_source="not_applicable",
                entry_fee=0.0,
                exit_fee=0.0,
                closed_at=datetime.now(timezone.utc).isoformat(),
            ).model_dump()
        )
        repo._insert("fills", Fill(
            id=f"{order_id}-fill", order_id=order_id, fill_price=100.0,
            fill_quantity=1.0, fee=0.0, fee_source="reconciled",
        ).model_dump())
    _tamper_mandate_snapshot(db, tampered.id)

    report = build_mandate_compliance_report(hours=720, db=db)

    assert report["counts"]["proposals_total"] == 2
    assert report["counts"]["positions_total"] == 2
    assert report["counts"]["proposals_by_fit_status"]["in_scope"] == 1
    assert report["counts"]["positions_by_fit_status"]["in_scope"] == 1
    assert report["denominators"]["proposal_fit_status_denominator"] == 1
    assert report["denominators"]["position_fit_status_denominator"] == 1
    assert report["eligible_genuine_net_outcomes"]["total_eligible_count"] == 1, report["exclusions"]
    assert report["eligible_genuine_net_outcomes"]["by_fit_status"]["in_scope"] == {
        "eligible_count": 1,
        "net_pnl": 10.0,
    }
    assert report["exclusions"]["proposal_integrity_mismatches"] == 1
    assert report["exclusions"]["positions_by_reason"] == {
        "proposal_integrity_mismatch": 1,
    }
    assert "tampered_reason" not in report["reason_code_distribution"]
    sqlite_store.reset_db()
