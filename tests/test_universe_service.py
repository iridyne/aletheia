"""Tests for UniverseService — fallback behavior, get_current_universe, refresh."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from aletheia.universe.service import UniverseService
from aletheia.config.schema import UniverseConfig, AletheiaConfig


@pytest.fixture
def svc():
    return UniverseService()


@pytest.fixture
def disabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=False)
    return cfg


@pytest.fixture
def enabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True, fallback_symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    return cfg


def _paper_learning_identity() -> dict:
    from aletheia.learning.hypotheses import (
        HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
        hypothesis_fingerprint,
    )
    from aletheia.learning.registry import get_learning_path

    path_id = "universe_family_calibration"
    scope = {"family": "momentum", "direction": "long", "market_type": "spot"}
    action = {"type": "opportunity_multiplier", "multiplier": 1.1}
    behavior_version = get_learning_path(path_id).behavior_contract_version
    fingerprint = hypothesis_fingerprint(
        path_id=path_id,
        scope=scope,
        action=action,
        target_horizon="4h",
        source_cohort="universe_forward_outcomes:clean_net:v2",
        behavior_contract_version=behavior_version,
    )
    return {
        "experiment_id": "paper-experiment",
        "fingerprint": fingerprint,
        "scope": scope,
        "action": action,
        "target_horizon": "4h",
        "source_cohort": "universe_forward_outcomes:clean_net:v2",
        "behavior_contract_version": behavior_version,
        "fingerprint_schema_version": HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
    }


def test_active_paper_learning_identity_returns_exact_single_hypothesis():
    from aletheia.universe.service import _active_paper_learning_identity

    identity = _paper_learning_identity()
    hypothesis = {
        "path_id": "universe_family_calibration",
        "fingerprint": identity["fingerprint"],
        "scope_json": json.dumps(identity["scope"]),
        "action_json": json.dumps(identity["action"]),
        "target_horizon": identity["target_horizon"],
        "source_type": identity["source_cohort"],
        "behavior_contract_version": identity["behavior_contract_version"],
        "fingerprint_schema_version": identity["fingerprint_schema_version"],
    }
    with (
        patch(
            "aletheia.learning.ledger.list_open_experiments",
            return_value=[
                {"stage": "shadow", "hypothesis_id": "shadow"},
                {
                    "experiment_id": "paper-experiment",
                    "stage": "paper_10",
                    "hypothesis_id": "paper",
                },
            ],
        ),
        patch("aletheia.learning.ledger.get_record", return_value=hypothesis),
    ):
        assert _active_paper_learning_identity(
            "universe_family_calibration"
        ) == (True, identity)


def test_active_paper_learning_identity_fails_closed_on_fingerprint_drift():
    from aletheia.universe.service import _active_paper_learning_identity

    identity = _paper_learning_identity()
    with (
        patch(
            "aletheia.learning.ledger.list_open_experiments",
            return_value=[{
                "experiment_id": "paper-experiment",
                "stage": "paper_10",
                "hypothesis_id": "paper",
            }],
        ),
        patch(
            "aletheia.learning.ledger.get_record",
            return_value={
                "path_id": "universe_family_calibration",
                "fingerprint": "not-the-canonical-fingerprint",
                "scope_json": json.dumps(identity["scope"]),
                "action_json": json.dumps(identity["action"]),
                "target_horizon": identity["target_horizon"],
                "source_type": identity["source_cohort"],
                "behavior_contract_version": identity["behavior_contract_version"],
                "fingerprint_schema_version": identity["fingerprint_schema_version"],
            },
        ),
    ):
        assert _active_paper_learning_identity(
            "universe_family_calibration"
        ) == (True, None)


def test_active_paper_learning_identity_fails_closed_on_ambiguity():
    from aletheia.universe.service import _active_paper_learning_identity

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[
            {"stage": "paper_10", "hypothesis_id": "first"},
            {"stage": "paper_25", "hypothesis_id": "second"},
        ],
    ):
        assert _active_paper_learning_identity(
            "universe_family_calibration"
        ) == (True, None)


def test_fallback_when_disabled(svc, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"
    assert members[0]["scanner_family"] == "core"
    assert members[0]["snapshot_id"] is None


def test_fallback_when_no_snapshot(svc, enabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=None):
            members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"


def test_fallback_when_snapshot_empty(svc, enabled_cfg):
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=[]):
                members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["scanner_family"] == "core"


def test_returns_active_members(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "DOGE/USDT", "scanner_family": "momentum", "total_score": 75,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "PEPE/USDT", "scanner_family": "volatility", "total_score": 60,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 1,
         "expires_at": future, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 2
    assert members[0]["symbol"] == "DOGE/USDT"


def test_filters_expired_members(svc, enabled_cfg):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "FRESH/USDT", "scanner_family": "momentum", "total_score": 80,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "STALE/USDT", "scanner_family": "momentum", "total_score": 70,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 1,
         "expires_at": past, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 1
    assert members[0]["symbol"] == "FRESH/USDT"


def test_filters_non_execution_eligible(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "GOOD/USDT", "scanner_family": "momentum", "total_score": 80,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "BAD/USDT", "scanner_family": "momentum", "total_score": 70,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 0,
         "expires_at": future, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 1
    assert members[0]["symbol"] == "GOOD/USDT"


def test_execution_ineligible_candidate_cannot_gain_or_revive_planning_authority(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.service import _ensure_planning_generations

    now = datetime.now(timezone.utc)
    first_snapshot = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(first_snapshot)
    ineligible = UniverseMember(
        snapshot_id=first_snapshot.id,
        asset_id="asset-btc",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        rank=1,
        total_score=90,
        scanner_family="momentum",
        scanner_direction="long",
        execution_eligible=False,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_candidate(ineligible)

    assert _ensure_planning_generations(
        [ineligible], soft_lease_sec=900, max_attention_age_sec=3600, now=now
    ) == (set(), set(), 0)
    assert repo.get_candidate(ineligible.id)["execution_eligible"] == 0
    assert get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 0

    # Simulate a generation written by the pre-fix runtime, then restore the
    # Candidate's durable ineligible evidence. It must be invisible to claims.
    repo.execute_write(
        "UPDATE universe_members SET execution_eligible=1 WHERE id=?", (ineligible.id,)
    )
    legacy, _ = repo.ensure_candidate_planning_generation(ineligible.id, now=now)
    repo.execute_write(
        "UPDATE universe_members SET execution_eligible=0 WHERE id=?", (ineligible.id,)
    )
    assert repo.count_pending_candidate_planning(now=now) == 0
    assert repo.claim_next_candidate_planning("legacy-owner", now=now) is None
    assert repo.candidate_attention_is_active(legacy["id"], now=now) is False

    recovery_snapshot = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(recovery_snapshot)
    eligible = ineligible.model_copy(
        update={
            "id": "eligible-recovery",
            "snapshot_id": recovery_snapshot.id,
            "execution_eligible": True,
        }
    )
    repo.insert_candidate(eligible)

    selected, created_ids, created_count = _ensure_planning_generations(
        [eligible], soft_lease_sec=900, max_attention_age_sec=3600, now=now
    )
    assert created_count == 1
    assert selected == created_ids
    generation_id = next(iter(selected))
    assert generation_id != legacy["id"]
    assert repo.get_candidate_planning_generation(legacy["id"])["is_current"] == 0
    assert repo.get_candidate_planning_generation(generation_id)["last_candidate_id"] == eligible.id

    selected_again, created_again, count_again = _ensure_planning_generations(
        [eligible], soft_lease_sec=900, max_attention_age_sec=3600, now=now
    )
    assert selected_again == {generation_id}
    assert created_again == set()
    assert count_again == 0
    assert get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 2


def test_fallback_all_expired(svc, enabled_cfg):
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    mock_members = [
        {"symbol": "OLD/USDT", "scanner_family": "momentum", "total_score": 80,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": past, "rank": 1},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    # Should fallback since all expired
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"


def test_limit_parameter(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": f"SYM{i}/USDT", "scanner_family": "momentum", "total_score": 90 - i,
         "snapshot_id": "snap_1", "id": f"m{i}", "execution_eligible": 1,
         "expires_at": future, "rank": i}
        for i in range(10)
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe(limit=3)
    assert len(members) == 3


def test_change_suppression_fingerprint_covers_carrier_semantics():
    """A reused snapshot may not silently preserve stale execution semantics."""
    from copy import deepcopy

    from aletheia.universe.service import _member_fingerprint

    base = [{
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "venue": "binance",
        "market_type": "perp",
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "execution_eligible": True,
        "ttl_sec": 1800,
        "risk_flags": ["crowded"],
        "last_price": 60_000.0,
        "instrument_background": {
            "version": "instrument-background/v1",
            "status": "complete",
            "asset_class": "crypto",
        },
    }]
    baseline = _member_fingerprint(base, "risk_on")
    variants = {
        "venue": "okx",
        "scanner_direction": "short",
        "execution_eligible": False,
        "ttl_sec": 3600,
        "risk_flags": ["wide_spread"],
        "last_price": 60_001.0,
        "instrument_background": {
            "version": "instrument-background/v1",
            "status": "complete",
            "asset_class": "equity",
        },
    }

    for field, value in variants.items():
        changed = deepcopy(base)
        changed[0][field] = value
        assert _member_fingerprint(changed, "risk_on") != baseline, field


def test_previous_snapshot_fingerprint_reads_frozen_semantic_payload(monkeypatch):
    from aletheia.universe import service

    allocated = [{
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "venue": "binance",
        "market_type": "perp",
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "execution_eligible": True,
        "ttl_sec": 1800,
        "risk_flags": ["crowded"],
        "last_price": 60_000.0,
        "instrument_background": {
            "version": "instrument-background/v1",
            "status": "complete",
            "asset_class": "crypto",
        },
    }]
    expected = service._member_fingerprint(allocated, "risk_on")
    monkeypatch.setattr(service.repo, "get_active_universe_snapshot", lambda _: {
        "id": "snap-1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": json.dumps({
            "information_policy_epoch": service.POLICY_EPOCH_LABEL,
            "member_fingerprint": expected,
        }),
    })

    previous, snapshot_id, _ = service._previous_snapshot_fingerprint("crypto")

    assert snapshot_id == "snap-1"
    assert previous == expected


def test_pre_v3_snapshot_cannot_suppress_background_aware_publication(monkeypatch):
    from aletheia.universe import service

    monkeypatch.setattr(service.repo, "get_active_universe_snapshot", lambda _: {
        "id": "legacy-snapshot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": json.dumps({"information_policy_epoch": "opportunity_information_integrity/v2"}),
    })

    previous, snapshot_id, _ = service._previous_snapshot_fingerprint("crypto")

    assert snapshot_id == "legacy-snapshot"
    assert previous is None


def test_fallback_static_universe(svc, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        symbols = svc.fallback_static_universe()
    assert symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_feature_refresh_nudges_operator_daemon(monkeypatch):
    from aletheia.universe.service import _notify_operator_feature_refresh

    calls = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda *, source: calls.append(source) or True,
    )

    assert _notify_operator_feature_refresh("universe_feature_refresh_test") is True
    assert calls == ["universe_feature_refresh_test"]


@pytest.mark.asyncio
async def test_refresh_returns_none_on_failure(svc):
    mock_cfg = MagicMock()
    mock_cfg.universe = UniverseConfig(enabled=True)
    with patch("aletheia.universe.service.load_config", return_value=mock_cfg):
        with patch("aletheia.universe.service.repo.insert_universe_scanner_run"):
            with patch("aletheia.universe.service.repo.update_universe_scanner_run"):
                with patch("aletheia.universe.registry.sync_registry", side_effect=Exception("net")):
                    result = await svc.refresh()
    assert result is None
