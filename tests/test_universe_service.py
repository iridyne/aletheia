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
        assert _active_paper_learning_identity("universe_family_calibration") == (True, identity)


def test_active_paper_learning_identity_fails_closed_on_fingerprint_drift():
    from aletheia.universe.service import _active_paper_learning_identity

    identity = _paper_learning_identity()
    with (
        patch(
            "aletheia.learning.ledger.list_open_experiments",
            return_value=[
                {
                    "experiment_id": "paper-experiment",
                    "stage": "paper_10",
                    "hypothesis_id": "paper",
                }
            ],
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
        assert _active_paper_learning_identity("universe_family_calibration") == (True, None)


def test_active_paper_learning_identity_fails_closed_on_ambiguity():
    from aletheia.universe.service import _active_paper_learning_identity

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[
            {"stage": "paper_10", "hypothesis_id": "first"},
            {"stage": "paper_25", "hypothesis_id": "second"},
        ],
    ):
        assert _active_paper_learning_identity("universe_family_calibration") == (True, None)


def test_fallback_when_disabled(svc, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"
    assert members[0]["scanner_family"] == "core"
    assert members[0]["snapshot_id"] is None
    assert members[0]["candidate_selection_policy"] == "legacy_total_score_v1"
    assert members[0]["candidate_selection_utility_pct"] is None


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


def test_target_snapshot_empty_is_intentional_no_fill(svc, enabled_cfg):
    enabled_cfg.universe = UniverseConfig(
        enabled=True,
        fallback_symbols=["BTC/USDT"],
        candidate_selection_policy="candidate_selection_utility_v1",
        candidate_selection_policy_epoch="candidate-selection-utility:epoch",
    )
    snap = {
        "id": "snap-target-empty",
        "status": "active",
        "policy_id": "candidate-selection-utility:epoch",
    }
    with (
        patch("aletheia.universe.service.load_config", return_value=enabled_cfg),
        patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap),
        patch("aletheia.universe.service.repo.list_universe_members", return_value=[]),
    ):
        assert svc.get_current_universe() == []

        enabled_cfg.universe = UniverseConfig.model_validate({
            **enabled_cfg.universe.model_dump(mode="json"),
            "candidate_selection_policy": "legacy_total_score_v1",
            "candidate_selection_policy_epoch": None,
        })
        assert svc.get_current_universe() == []


@pytest.mark.asyncio
async def test_refresh_persists_legacy_family_without_opportunity_context(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as repo
    from aletheia.universe import service

    cfg = load_config()
    cfg.universe.enabled = True
    cfg.regime.enabled = False
    feature = {
        "asset_id": "legacy-volatility-asset",
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "last_price": 60_000.0,
        "quote_volume_24h": 1_000_000_000.0,
        "spread_bps": 2.0,
        "execution_eligible": False,
    }
    scanner_result = {
        "scanner_family": "volatility",
        "scanner_direction": "long",
        "opportunity_score": 70.0,
        "all_scores": {"volatility": 70.0},
        "reasons": ["volatility expansion"],
        "risk_flags": [],
    }
    allocated = [{**feature, **scanner_result, "total_score": 70.0, "ttl_sec": 3600}]

    async def one_feature(*_args, **_kwargs):
        return [feature]

    async def no_op(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(service, "load_config", lambda: cfg)
    monkeypatch.setattr(service, "_active_paper_learning_identity", lambda _path: (False, None))
    monkeypatch.setattr(service, "_previous_snapshot_fingerprint", lambda _market: (None, None, None))
    monkeypatch.setattr(service, "_should_sample_alpha_control", lambda _now: False)
    monkeypatch.setattr(service, "_persist_candidate_selection_shadow_nonfatal", no_op)
    monkeypatch.setattr(service, "_capture_universe_discovery_nonfatal", lambda **_kwargs: None)
    monkeypatch.setattr(service, "_notify_operator_feature_refresh", lambda _source: True)
    monkeypatch.setattr(service, "fast_market_data_unavailability_reason", lambda *_args: None)
    monkeypatch.setattr(repo, "list_universe_assets", lambda **_kwargs: [feature])
    monkeypatch.setattr("aletheia.universe.registry.sync_registry", no_op)
    monkeypatch.setattr("aletheia.universe.features.collect_features", one_feature)
    monkeypatch.setattr("aletheia.universe.filters.apply_coarse_tradability_filters", lambda rows, *_args, **_kwargs: rows)
    monkeypatch.setattr("aletheia.universe.filters.apply_fine_tradability_filters", lambda rows, *_args, **_kwargs: rows)
    monkeypatch.setattr("aletheia.universe.scanners.run_all_scanners", lambda _row: scanner_result)
    monkeypatch.setattr("aletheia.universe.allocator.compute_total_score", lambda *_args, **_kwargs: 70.0)
    monkeypatch.setattr("aletheia.universe.allocator.get_family_multipliers", lambda **_kwargs: {})
    monkeypatch.setattr("aletheia.universe.allocator.allocate_universe", lambda *_args, **_kwargs: allocated)
    monkeypatch.setattr("aletheia.universe.outcome_recorder.settle_due", no_op)
    monkeypatch.setattr("aletheia.universe.outcome_recorder.build_pending", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.get_latest_volume_ranking", lambda: None)
    monkeypatch.setattr(
        "aletheia.universe.candidate_selection_shadow.candidate_selection_frozen_family_spread_control",
        lambda _policy: ({}, {}),
    )

    snapshot_id = await UniverseService().refresh()
    member = repo.list_universe_members(snapshot_id, limit=10)[0]

    assert member["scanner_family"] == "volatility"
    assert member["opportunity_mechanism"] is None
    assert member["candidate_opportunity_context_version"] is None
    assert member["candidate_opportunity_context_json"] is None
    assert member["candidate_opportunity_context_hash"] is None


def test_returns_active_members(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {
            "symbol": "DOGE/USDT",
            "scanner_family": "momentum",
            "total_score": 75,
            "snapshot_id": "snap_1",
            "id": "m1",
            "execution_eligible": 1,
            "expires_at": future,
            "rank": 1,
        },
        {
            "symbol": "PEPE/USDT",
            "scanner_family": "volatility",
            "total_score": 60,
            "snapshot_id": "snap_1",
            "id": "m2",
            "execution_eligible": 1,
            "expires_at": future,
            "rank": 2,
        },
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
        {
            "symbol": "FRESH/USDT",
            "scanner_family": "momentum",
            "total_score": 80,
            "snapshot_id": "snap_1",
            "id": "m1",
            "execution_eligible": 1,
            "expires_at": future,
            "rank": 1,
        },
        {
            "symbol": "STALE/USDT",
            "scanner_family": "momentum",
            "total_score": 70,
            "snapshot_id": "snap_1",
            "id": "m2",
            "execution_eligible": 1,
            "expires_at": past,
            "rank": 2,
        },
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
        {
            "symbol": "GOOD/USDT",
            "scanner_family": "momentum",
            "total_score": 80,
            "snapshot_id": "snap_1",
            "id": "m1",
            "execution_eligible": 1,
            "expires_at": future,
            "rank": 1,
        },
        {
            "symbol": "BAD/USDT",
            "scanner_family": "momentum",
            "total_score": 70,
            "snapshot_id": "snap_1",
            "id": "m2",
            "execution_eligible": 0,
            "expires_at": future,
            "rank": 2,
        },
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 1
    assert members[0]["symbol"] == "GOOD/USDT"


def test_execution_ineligible_candidate_cannot_gain_or_revive_planning_authority(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from tests.test_entry_planner import _typed_candidate
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.service import _ensure_planning_generations

    now = datetime.now(timezone.utc)
    ineligible = _typed_candidate(mechanism="continuation", family="momentum", source_at=now.isoformat())
    ineligible.execution_eligible = False
    repo.execute_write("UPDATE universe_members SET execution_eligible=0 WHERE id=?", (ineligible.id,))

    assert _ensure_planning_generations([ineligible], soft_lease_sec=900, max_attention_age_sec=3600, now=now) == (
        set(),
        set(),
        0,
    )
    assert repo.get_candidate(ineligible.id)["execution_eligible"] == 0
    assert get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 0

    # Simulate a generation written by the pre-fix runtime, then restore the
    # Candidate's durable ineligible evidence. It must be invisible to claims.
    repo.execute_write("UPDATE universe_members SET execution_eligible=1 WHERE id=?", (ineligible.id,))
    legacy, _ = repo.ensure_candidate_planning_generation(ineligible.id, now=now)
    repo.execute_write("UPDATE universe_members SET execution_eligible=0 WHERE id=?", (ineligible.id,))
    assert repo.count_pending_candidate_planning(now=now) == 0
    assert repo.claim_next_candidate_planning("legacy-owner", now=now) is None
    assert repo.candidate_attention_is_active(legacy["id"], now=now) is False

    eligible = _typed_candidate(
        mechanism="continuation", family="momentum",
        source_at=(now + timedelta(seconds=1)).isoformat(),
    )

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
        {
            "symbol": "OLD/USDT",
            "scanner_family": "momentum",
            "total_score": 80,
            "snapshot_id": "snap_1",
            "id": "m1",
            "execution_eligible": 1,
            "expires_at": past,
            "rank": 1,
        },
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
        {
            "symbol": f"SYM{i}/USDT",
            "scanner_family": "momentum",
            "total_score": 90 - i,
            "snapshot_id": "snap_1",
            "id": f"m{i}",
            "execution_eligible": 1,
            "expires_at": future,
            "rank": i,
        }
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

    base = [
        {
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
        }
    ]
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
        "candidate_selection_utility_pct": 1.25,
    }

    for field, value in variants.items():
        changed = deepcopy(base)
        changed[0][field] = value
        assert _member_fingerprint(changed, "risk_on") != baseline, field

    assert _member_fingerprint(
        [], "risk_on", policy_id="legacy_total_score_v1",
    ) != _member_fingerprint(
        [], "risk_on", policy_id="candidate-selection-utility:epoch",
    )


def test_previous_snapshot_fingerprint_reads_frozen_semantic_payload(monkeypatch):
    from aletheia.universe import service

    allocated = [
        {
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
        }
    ]
    expected = service._member_fingerprint(allocated, "risk_on")
    monkeypatch.setattr(
        service.repo,
        "get_active_universe_snapshot",
        lambda _: {
            "id": "snap-1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": json.dumps(
                {
                    "information_policy_epoch": service.POLICY_EPOCH_LABEL,
                    "member_fingerprint": expected,
                }
            ),
        },
    )

    previous, snapshot_id, _ = service._previous_snapshot_fingerprint("crypto")

    assert snapshot_id == "snap-1"
    assert previous == expected


def test_pre_v3_snapshot_cannot_suppress_background_aware_publication(monkeypatch):
    from aletheia.universe import service

    monkeypatch.setattr(
        service.repo,
        "get_active_universe_snapshot",
        lambda _: {
            "id": "legacy-snapshot",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": json.dumps({"information_policy_epoch": "opportunity_information_integrity/v2"}),
        },
    )

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


def test_venue_mismatch_sets_execution_ineligible_with_risk_flag():
    """Allocated candidates whose venue cannot be monitored are marked ineligible."""
    from unittest.mock import MagicMock
    from aletheia.universe.service import _member_fingerprint
    from aletheia.universe import service as svc_mod

    cfg = MagicMock()
    cfg.exchange.paper_execution_mode = "bitget_demo"

    item_matching = {
        "symbol": "BTC/USDT",
        "venue": "bitget",
        "market_type": "perp",
        "scanner_family": "momentum",
        "execution_eligible": True,
        "risk_flags": [],
    }
    item_mismatch = {
        "symbol": "ETH/USDT",
        "venue": "okx",
        "market_type": "perp",
        "scanner_family": "momentum",
        "execution_eligible": True,
        "risk_flags": [],
    }
    allocated = [item_matching, item_mismatch]

    # Re-run the inline venue-eligibility loop that service.py applies after allocate_universe.
    from aletheia.universe.fast_stream import fast_market_data_unavailability_reason
    import json

    for item in allocated:
        venue_reason = fast_market_data_unavailability_reason(cfg, str(item.get("venue") or ""))
        if venue_reason is not None and item.get("execution_eligible", True):
            item["execution_eligible"] = False
            flags = item.get("risk_flags") or []
            if isinstance(flags, str):
                try:
                    flags = json.loads(flags)
                except (TypeError, ValueError):
                    flags = [flags]
            if "fast_market_data_venue_unavailable" not in flags:
                flags = list(flags) + ["fast_market_data_venue_unavailable"]
            item["risk_flags"] = flags

    assert item_matching["execution_eligible"] is True
    assert "fast_market_data_venue_unavailable" not in item_matching.get("risk_flags", [])
    assert item_mismatch["execution_eligible"] is False
    assert "fast_market_data_venue_unavailable" in item_mismatch["risk_flags"]


def test_venue_mismatch_changes_fingerprint():
    """Eligibility change from venue mismatch must produce a different fingerprint."""
    from aletheia.universe.service import _member_fingerprint

    before = [
        {
            "symbol": "BTC/USDT",
            "venue": "okx",
            "market_type": "perp",
            "scanner_family": "momentum",
            "execution_eligible": True,
            "risk_flags": [],
        }
    ]
    after = [
        {
            "symbol": "BTC/USDT",
            "venue": "okx",
            "market_type": "perp",
            "scanner_family": "momentum",
            "execution_eligible": False,
            "risk_flags": ["fast_market_data_venue_unavailable"],
        }
    ]

    assert _member_fingerprint(before, None) != _member_fingerprint(after, None)


def test_fast_market_data_unavailability_reason_returns_stable_tokens():
    """Helper returns None for matching venue, stable token for mismatch/unavailable."""
    from unittest.mock import MagicMock
    from aletheia.universe.fast_stream import fast_market_data_unavailability_reason

    cfg_bitget = MagicMock()
    cfg_bitget.exchange.paper_execution_mode = "bitget_demo"

    assert fast_market_data_unavailability_reason(cfg_bitget, "bitget") is None
    assert fast_market_data_unavailability_reason(cfg_bitget, "okx") == "FAST_MARKET_DATA_VENUE_MISMATCH"
    assert fast_market_data_unavailability_reason(cfg_bitget, "") == "FAST_MARKET_DATA_VENUE_MISMATCH"

    cfg_binance = MagicMock()
    cfg_binance.exchange.paper_execution_mode = "binance_testnet"
    # Binance public streams cover the matching venue; exact market capability
    # is checked when the caller supplies the candidate's market type.
    result = fast_market_data_unavailability_reason(cfg_binance, "binance")
    assert result is None


def test_venue_ineligible_candidate_does_not_gain_planning_generation(tmp_path, monkeypatch):
    """A candidate marked execution_eligible=False by venue check produces no generation."""
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.service import _ensure_planning_generations
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    snap = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snap)
    candidate = UniverseMember(
        snapshot_id=snap.id,
        asset_id="asset-eth",
        venue="okx",
        symbol="ETH/USDT",
        market_type="perp",
        rank=1,
        total_score=85,
        scanner_family="momentum",
        scanner_direction="long",
        execution_eligible=False,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_candidate(candidate)

    selected, created, count = _ensure_planning_generations(
        [candidate], soft_lease_sec=900, max_attention_age_sec=3600, now=now
    )
    assert count == 0
    assert selected == set()
    assert get_db().execute("SELECT COUNT(*) FROM candidate_planning_generations").fetchone()[0] == 0
