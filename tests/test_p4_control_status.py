"""P4 regression: aletheia control status surfaces governance state automatically.

Tests:
1. build_control_status() returns all required top-level sections.
2. SHA section correctly reflects deployed vs config vs master.
3. Active experiments section pulls from open experiments DB.
4. Evidence epoch section pulls from evidence_epoch_summary.
5. Window continuity flags are derived correctly.
6. All field values are concrete (never manually injected dynamic values from
   outside the DB/git state).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_status(**overrides):
    """Call build_control_status with mocked I/O to avoid network/DB."""
    from aletheia.cli.control_cmd import build_control_status

    defaults = dict(
        _deployed_commit=lambda: "abc1234567890000",
        _disk_commit=lambda: "abc1234567890000",
        _cached_master_commit=lambda: "abc1234567890000",
        _cfg_commit=lambda: "abc1234567890000",
        _auto_deploy_commit=lambda: "",
        _open_experiments=lambda: {"available": True, "items": []},
        _open_policy_epochs=lambda limit=10: [],
        _evidence_epochs=lambda: {
            "all_anchored": True,
            "all_sha_consistent": True,
            "current_commit": "abc1234567890000",
            "paths": [],
        },
        _learning_authority=lambda: {"influence_gate": "open", "global_refreeze": "none", "influence_enabled": True},
        _runtime_continuity=lambda: {"enabled": False, "paused": False},
        _gateway_running=lambda: False,
        _position_path_observer_status=lambda: {
            "available": True,
            "oldest_missing_bucket": None,
            "deferred_bucket_count": 0,
            "observer_lag": {"seconds": 0.0, "buckets": 0},
            "last_24h": {"available": 0, "unavailable": 0, "skipped": 0},
        },
        _ufc_status=lambda: {
            "schema_version": "ufc_control_status/v1",
            "path_id": "universe_family_calibration",
            "status": "not_registered",
            "availability": "available",
        },
        _profit_loop_status=lambda evidence=None: {
            "schema_version": "profit_loop_control_status/v1",
            "path_id": "profit_loop_observability",
            "status": "not_registered",
            "availability": "available",
        },
    )
    defaults.update(overrides)

    import aletheia.cli.control_cmd as cmd_mod
    with (
        patch.object(cmd_mod, "_deployed_commit", defaults["_deployed_commit"]),
        patch.object(cmd_mod, "_disk_commit", defaults["_disk_commit"]),
        patch.object(cmd_mod, "_cached_master_commit", defaults["_cached_master_commit"]),
        patch.object(cmd_mod, "_cfg_commit", defaults["_cfg_commit"]),
        patch.object(cmd_mod, "_auto_deploy_commit", defaults["_auto_deploy_commit"]),
        patch.object(cmd_mod, "_open_experiments", defaults["_open_experiments"]),
        patch.object(cmd_mod, "_open_policy_epochs", defaults["_open_policy_epochs"]),
        patch.object(cmd_mod, "_evidence_epochs", defaults["_evidence_epochs"]),
        patch.object(cmd_mod, "_learning_authority", defaults["_learning_authority"]),
        patch.object(cmd_mod, "_runtime_continuity", defaults["_runtime_continuity"]),
        patch.object(cmd_mod, "_gateway_running", defaults["_gateway_running"]),
        patch.object(
            cmd_mod,
            "_position_path_observer_status",
            defaults["_position_path_observer_status"],
        ),
        patch.object(cmd_mod, "_ufc_status", defaults["_ufc_status"]),
        patch.object(cmd_mod, "_profit_loop_status", defaults["_profit_loop_status"]),
    ):
        return build_control_status()


# ---------------------------------------------------------------------------
# Test 1: all required sections present
# ---------------------------------------------------------------------------

def test_control_status_has_all_sections():
    data = _minimal_status()

    required = {"schema_version", "as_of", "sha", "gateway_running", "runtime", "safety", "authority",
                "active_experiments", "active_experiments_available",
                "open_policy_epochs", "evidence_epochs", "window_continuity",
                "position_path_observer", "ufc", "profit_loop"}
    assert required.issubset(data.keys()), (
        f"Missing keys: {required - set(data.keys())}"
    )


# ---------------------------------------------------------------------------
# Test 2: SHA section correctness
# ---------------------------------------------------------------------------

def test_control_status_exposes_position_path_recovery():
    observer = {
        "available": True,
        "oldest_missing_bucket": {
            "position_id": "position-1",
            "bucket_open_ms": 123,
            "bucket_open_at": "2026-08-13T00:00:00+00:00",
        },
        "deferred_bucket_count": 2,
        "observer_lag": {"seconds": 600.0, "buckets": 2},
        "last_24h": {"available": 5, "unavailable": 1, "skipped": 0},
    }
    data = _minimal_status(_position_path_observer_status=lambda: observer)
    assert data["position_path_observer"] == observer


def test_control_status_keeps_ufc_and_profit_loop_separate():
    ufc = {
        "path_id": "universe_family_calibration",
        "status": "collecting",
        "availability": "available",
        "assignment": {"complete_treatment_units": 7, "complete_control_units": 9},
    }
    profit_loop = {
        "path_id": "profit_loop_observability",
        "status": "collecting",
        "availability": "available",
        "registration": {"epoch_id": "profit-epoch", "cutoff_at": "2026-09-01T00:00:00+00:00"},
    }
    data = _minimal_status(
        _ufc_status=lambda: ufc,
        _profit_loop_status=lambda evidence=None: profit_loop,
    )
    assert data["schema_version"] == "control_status/v1"
    assert data["ufc"] == ufc
    assert data["profit_loop"] == profit_loop
    assert data["ufc"]["path_id"] != data["profit_loop"]["path_id"]


def test_sha_section_deployed_matches_config():
    sha = "abc1234567890123"
    data = _minimal_status(
        _deployed_commit=lambda: sha,
        _disk_commit=lambda: sha,
        _cfg_commit=lambda: sha,
        _cached_master_commit=lambda: sha,
    )
    assert data["sha"]["deployed_head_short"] == sha[:12]
    assert data["sha"]["deployed_matches_config"] is True
    assert data["sha"]["deployed_matches_master"] is True


def test_sha_section_config_disagrees():
    data = _minimal_status(
        _deployed_commit=lambda: "new_sha_0000000000000",
        _cfg_commit=lambda: "old_sha_1111111111111",
        _cached_master_commit=lambda: "",
    )
    assert data["sha"]["deployed_matches_config"] is False


def test_sha_section_uses_atomic_auto_deploy_provenance():
    deployed = "new_sha_0000000000000"
    data = _minimal_status(
        _deployed_commit=lambda: deployed,
        _cfg_commit=lambda: "old_sha_1111111111111",
        _auto_deploy_commit=lambda: deployed,
    )
    assert data["sha"]["deployed_matches_config"] is False
    assert data["sha"]["runtime_recorded"] == deployed
    assert data["sha"]["runtime_recorded_source"] == "auto_release_tag"
    assert data["sha"]["deployed_matches_recorded"] is True
    assert data["window_continuity"]["deployed_head_consistent"] is True


def test_sha_section_not_available_when_empty():
    data = _minimal_status(
        _deployed_commit=lambda: "",
        _disk_commit=lambda: "",
        _cfg_commit=lambda: "",
        _cached_master_commit=lambda: "",
    )
    assert data["sha"]["deployed_head_short"] == "not_available"
    assert data["sha"]["cached_master_head_short"] == "not_available"
    assert data["sha"]["deployed_matches_config"] is None


# ---------------------------------------------------------------------------
# Test 3: active experiments come from DB
# ---------------------------------------------------------------------------

def test_active_experiments_populated_from_db():
    fake_experiment = {
        "experiment_id": "exp-abc123",
        "hypothesis_id": "hyp-xyz",
        "stage": "paper",
        "status": "active",
        "started_at": "2026-08-01T00:00:00",
        "concurrent_policy_epoch": "epoch-1234",
    }
    data = _minimal_status(
        _open_experiments=lambda: {"available": True, "items": [fake_experiment]}
    )

    exps = data["active_experiments"]
    assert len(exps) == 1
    assert exps[0]["experiment_id"] == "exp-abc123"
    assert exps[0]["stage"] == "paper"
    assert exps[0]["policy_epoch"] == "epoch-1234"


def test_active_experiments_empty_when_none():
    data = _minimal_status(
        _open_experiments=lambda: {"available": True, "items": []}
    )
    assert data["active_experiments_available"] is True
    assert data["active_experiments"] == []


def test_active_experiments_unavailable_is_not_empty_success():
    data = _minimal_status(
        _open_experiments=lambda: {
            "available": False,
            "items": [],
            "error": "OperationalError",
        }
    )
    assert data["active_experiments_available"] is False
    assert data["active_experiments_error"] == "OperationalError"


def test_malformed_experiment_rows_fail_closed_without_crashing():
    data = _minimal_status(
        _open_experiments=lambda: {"available": True, "items": [None]},
    )
    assert data["active_experiments_available"] is False
    assert data["active_experiments_error"] == "malformed_experiment_row"
    assert data["active_experiments"] == []


# ---------------------------------------------------------------------------
# Test 4: evidence epochs from evidence_epoch_summary
# ---------------------------------------------------------------------------

def test_evidence_epochs_reflected_in_status():
    evidence = {
        "all_anchored": True,
        "all_sha_consistent": True,
        "current_commit": "abc123",
        "paths": [
            {
                "path_id": "exit_trailing_evidence",
                "epoch_id": "ep-trail",
                "started_at": "2026-08-12T00:00:00",
                "deploy_commit": "abc123",
                "sha_consistent": True,
                "status": "open",
            },
            {
                "path_id": "entry_correction_v2",
                "epoch_id": "ep-entry",
                "started_at": "2026-08-12T00:00:00",
                "deploy_commit": "abc123",
                "sha_consistent": True,
                "status": "open",
            },
        ],
    }
    data = _minimal_status(_evidence_epochs=lambda: evidence)
    assert data["evidence_epochs"]["all_anchored"] is True
    assert data["evidence_epochs"]["all_sha_consistent"] is True
    assert len(data["evidence_epochs"]["paths"]) == 2


# ---------------------------------------------------------------------------
# Test 5: window_continuity flags derived correctly
# ---------------------------------------------------------------------------

def test_window_continuity_all_good():
    data = _minimal_status(
        _deployed_commit=lambda: "sha123",
        _cfg_commit=lambda: "sha123",
        _evidence_epochs=lambda: {"all_anchored": True, "all_sha_consistent": True, "paths": []},
    )
    wc = data["window_continuity"]
    assert wc["evidence_anchored"] is True
    assert wc["evidence_sha_consistent"] is True
    assert wc["deployed_head_consistent"] is True


def test_window_continuity_flags_sha_mismatch():
    data = _minimal_status(
        _deployed_commit=lambda: "newsha_abc",
        _cfg_commit=lambda: "oldsha_xyz",
        _evidence_epochs=lambda: {"all_anchored": True, "all_sha_consistent": False, "paths": []},
    )
    wc = data["window_continuity"]
    assert wc["evidence_sha_consistent"] is False
    assert wc["deployed_head_consistent"] is False


# ---------------------------------------------------------------------------
# Test 6: values are from system state, not hardcoded
# ---------------------------------------------------------------------------

def test_sha_values_come_from_git_not_hardcoded():
    """deployed_head must change when the underlying SHA function returns a different value."""
    sha_a = "aaaa0000000000000000"
    sha_b = "bbbb1111111111111111"

    data_a = _minimal_status(_deployed_commit=lambda: sha_a, _cfg_commit=lambda: sha_a, _cached_master_commit=lambda: sha_a)
    data_b = _minimal_status(_deployed_commit=lambda: sha_b, _cfg_commit=lambda: sha_b, _cached_master_commit=lambda: sha_b)

    assert data_a["sha"]["deployed_head_short"] == sha_a[:12]
    assert data_b["sha"]["deployed_head_short"] == sha_b[:12]
    assert data_a["sha"]["deployed_head_short"] != data_b["sha"]["deployed_head_short"]


def test_ufc_status_projects_fixed_look_units_and_coverage(monkeypatch):
    import aletheia.cli.control_cmd as cmd_mod

    projection = {
        "schema_version": "lco-v1",
        "items": [
            {
                "path_id": "universe_family_calibration",
                "availability": "available",
                "data_freshness": {"state": "fresh"},
                "hypothesis_id": "hyp-1",
                "experiment_id": "exp-1",
                "stage": "paper",
                "experiment_status": "active",
                "allocation_pct": 10,
                    "current_identity": {
                        "policy_epoch": "epoch-1",
                        "target_horizon": "24h",
                        "outcome_contract_version": "outcome/v1",
                        "metric_version": "metric/v1",
                        "statistical_unit": "assignment_unit",
                        "behavior_contract_hash": "behavior-contract",
                        "code_commit": "boot-sha",
                    },
                "measurement": {
                    "state": "insufficient_data",
                    "coverage": {"current_consecutive_market_days": 3},
                    "evaluated_at": None,
                },
                "inference": {"status": "collecting", "plan_hash": "plan-1"},
                "samples": {
                    "treatment_assignment_units": 11,
                    "control_assignment_units": 13,
                    "complete_assignment_units": 24,
                    "actual_intervention_treatment_units": 7,
                    "exposed_actual_intervention_treatment_units": 8,
                    "required_per_arm": 50,
                },
                "integrity": {"harm_state": "none"},
                "lifecycle": {"authority_enabled": False, "authority_block_reason": "not_ready"},
            }
        ],
        "path_states": [],
        "operations": {"evaluator": {"status": "running"}},
    }
    from aletheia.learning import read_models

    monkeypatch.setattr(read_models, "build_learning_contribution_projection", lambda limit=100: projection)
    result = cmd_mod._ufc_status()
    assert result["status"] == "collecting"
    assert result["assignment"]["complete_treatment_units"] == 11
    assert result["assignment"]["actual_intervention_units"] == 7
    assert result["coverage"]["current_consecutive_eligible_utc_days"] == 3
    assert result["fixed_look"]["plan_hash"] == "plan-1"
    assert result["path_id"] == "universe_family_calibration"


def test_profit_loop_status_stays_collecting_before_cutoff(monkeypatch):
    import aletheia.cli.control_cmd as cmd_mod

    evidence = {
        "paths": [
            {
                "path_id": "profit_loop_observability",
                "status": "open",
                "epoch_id": "profit-epoch",
                "started_at": "2026-08-17T00:00:00+00:00",
                "cutoff_at": "2099-08-17T00:00:00+00:00",
                "deploy_commit": "boot-sha",
                "behavior_fingerprint": "behavior-sha",
                "registry_hash": "registry-sha",
            }
        ]
    }
    monkeypatch.setattr(
        cmd_mod,
        "_profit_loop_coverage",
        lambda **_kwargs: {"availability": "available", "unavailable_reason": None},
    )
    result = cmd_mod._profit_loop_status(evidence)
    assert result["status"] == "collecting"
    assert result["identity"]["registry_hash"] == "registry-sha"
    assert result["artifact"]["status"] == "unavailable"
    assert result["fixed_result_hash"] is None
    assert "layer_winner" not in result


def test_malformed_profit_epoch_fails_closed():
    import aletheia.cli.control_cmd as cmd_mod

    result = cmd_mod._profit_loop_status({"paths": None})
    assert result["status"] == "unavailable"
    assert result["availability"] == "unavailable"
    assert result["unavailable_reason"] == "malformed_evidence_epoch_paths"
