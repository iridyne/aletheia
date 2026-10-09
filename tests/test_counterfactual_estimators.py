"""Tests for extensions.counterfactual.estimators and DR-mode calibration."""
from __future__ import annotations

from unittest.mock import Mock

from extensions.counterfactual.calibration import build_calibration
from extensions.counterfactual.estimators import (
    compute_ope_scores,
    dr_estimate,
    ips_estimate,
)
from extensions.counterfactual.propensity import build_propensity_report

# ---------------------------------------------------------------------------
# Helpers (shared with test_counterfactual_propensity pattern)
# ---------------------------------------------------------------------------

_FAMILIES = ["momentum", "short_bias", "derivatives"]


def _synthetic_rows(n: int, *, base_score: float = 50.0) -> list[dict]:
    """Synthetic rows already in memory (no DB involved)."""
    return [
        {
            "trade_score": base_score + i * 0.3,
            "rank": (i % 5) + 1,
            "scanner_family": _FAMILIES[i % len(_FAMILIES)],
            "direction": "long" if i % 2 == 0 else "short",
            "was_selected": 1 if (i % 5) < 2 else 0,  # ~40% selected
            "net_pnl_pct": 0.5 if (i % 5) < 2 else None,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Unit: ips_estimate
# ---------------------------------------------------------------------------


def test_ips_empty():
    assert ips_estimate([], []) == {"ips_avg_net_pct": None, "ips_effective_n": 0}


def test_ips_no_selected():
    rows = [{"was_selected": 0, "net_pnl_pct": 1.0}] * 5
    props = [0.4] * 5
    result = ips_estimate(rows, props)
    assert result["ips_avg_net_pct"] == 0.0
    assert result["ips_effective_n"] == 0


def test_ips_all_selected_propensity_one():
    """When all rows are selected with propensity=1.0, IPS ≈ naive mean."""
    rows = [{"was_selected": 1, "net_pnl_pct": 2.0}] * 4
    props = [0.95] * 4  # clipped from 1.0
    result = ips_estimate(rows, props, clip_lo=0.05, clip_hi=0.95)
    # IPS = Σ(r/prop) / n = 4*(2.0/0.95) / 4 ≈ 2.105
    expected = round(2.0 / 0.95, 3)
    assert abs(result["ips_avg_net_pct"] - expected) < 0.01
    assert result["ips_effective_n"] == 4


def test_ips_selection_bias_correction():
    """High-score rows overrepresented in selected; IPS should be lower than naive mean."""
    # Selected: score 70 (true r = 2.0), propensity = 0.8
    # Unselected: score 45 (true r = -1.0), propensity = 0.2 — never observed
    # Naive mean of selected = 2.0
    # IPS = (2.0/0.8) / 2 = 1.25 < 2.0
    rows = [
        {"was_selected": 1, "net_pnl_pct": 2.0, "trade_score": 70.0},
        {"was_selected": 0, "net_pnl_pct": None, "trade_score": 45.0},
    ]
    props = [0.8, 0.2]
    result = ips_estimate(rows, props)
    assert result["ips_avg_net_pct"] is not None
    assert result["ips_avg_net_pct"] < 2.0  # corrects upward bias in naive mean
    assert result["ips_effective_n"] == 1


# ---------------------------------------------------------------------------
# Unit: dr_estimate
# ---------------------------------------------------------------------------


def test_dr_empty():
    result = dr_estimate([], [], [])
    assert result == {"dr_avg_net_pct": None, "dm_avg_net_pct": None}


def test_dr_dm_only_when_no_selected():
    """When no rows are selected, DR = DM (outcome model predictions only)."""
    rows = [{"was_selected": 0, "net_pnl_pct": None}] * 3
    props = [0.3] * 3
    preds = [0.5, 1.0, -0.5]
    result = dr_estimate(rows, props, preds)
    expected_dm = round((0.5 + 1.0 - 0.5) / 3, 3)
    assert result["dm_avg_net_pct"] == expected_dm
    assert result["dr_avg_net_pct"] == expected_dm


def test_dr_with_perfect_outcome_model():
    """If outcome model is perfect (pred == realized), DR correction = 0 → DR = DM."""
    rows = [
        {"was_selected": 1, "net_pnl_pct": 2.0},
        {"was_selected": 0, "net_pnl_pct": None},
    ]
    props = [0.5, 0.5]
    preds = [2.0, -1.0]  # perfect: pred == net_pnl for selected
    result = dr_estimate(rows, props, preds)
    # DM = (2.0 + (-1.0)) / 2 = 0.5
    # correction = (2.0 - 2.0) / 0.5 / 2 = 0
    # DR = DM = 0.5
    assert result["dm_avg_net_pct"] == 0.5
    assert result["dr_avg_net_pct"] == 0.5


def test_dr_correction_direction():
    """DR correction moves estimate toward unbiased direction."""
    # Selected row has high realized r > prediction → correction pushes DR up
    rows = [
        {"was_selected": 1, "net_pnl_pct": 3.0},
        {"was_selected": 0, "net_pnl_pct": None},
    ]
    props = [0.4, 0.6]
    preds = [1.0, 1.0]  # outcome model underestimates
    result = dr_estimate(rows, props, preds)
    # DM = (1.0 + 1.0) / 2 = 1.0
    # correction = (3.0 - 1.0) / 0.4 / 2 = 2.5
    # DR = 1.0 + 2.5 = 3.5
    assert result["dm_avg_net_pct"] == 1.0
    assert abs(result["dr_avg_net_pct"] - 3.5) < 0.01


# ---------------------------------------------------------------------------
# Integration: compute_ope_scores
# ---------------------------------------------------------------------------


def test_compute_ope_scores_length():
    """Output lists are aligned with input rows."""
    rows = _synthetic_rows(60)
    propensities, outcome_preds = compute_ope_scores(rows)
    assert len(propensities) == len(rows)
    assert len(outcome_preds) == len(rows)


def test_compute_ope_scores_propensity_clipped():
    rows = _synthetic_rows(60)
    propensities, _ = compute_ope_scores(rows, clip_lo=0.05, clip_hi=0.95)
    assert all(0.05 <= p <= 0.95 for p in propensities)


def test_compute_ope_scores_empty():
    assert compute_ope_scores([]) == ([], [])


# ---------------------------------------------------------------------------
# Integration: build_calibration
# ---------------------------------------------------------------------------


def test_calibration_naive_default_keys():
    """Default method='naive' report has all expected keys."""
    report = build_calibration(days=9999)
    assert "bands" in report
    assert "estimation_method" in report
    assert report["estimation_method"] == "naive"


def test_epoch_detection_failure_calibration_does_not_use_legacy_aggregate(monkeypatch):
    import extensions.counterfactual.calibration as calibration

    def fail_detector(**_kwargs):
        raise RuntimeError("temporary database lock")

    reader = Mock(return_value=[{"trade_score": 60.0, "net_pnl_pct": 1.0}])

    monkeypatch.setattr(calibration, "_detect_window_epochs", fail_detector)
    monkeypatch.setattr(calibration, "list_deduped_shadows", reader)

    report = calibration.build_calibration(days=9999)

    assert report["policy_epoch_status"] == "unavailable"
    assert report["policy_epoch_error"] == "policy_epoch_detection_failed"
    assert report["errors"] == ["policy_epoch_detection_failed"]
    assert report["bands"] == []
    reader.assert_not_called()


def test_epoch_detection_failure_propensity_does_not_use_legacy_aggregate(monkeypatch):
    import extensions.counterfactual.propensity as propensity

    def fail_detector(**_kwargs):
        raise RuntimeError("temporary database lock")

    reader = Mock(return_value=[{"trade_score": 60.0, "net_pnl_pct": 1.0}])

    monkeypatch.setattr(propensity, "_detect_window_epochs", fail_detector)
    monkeypatch.setattr(propensity, "list_deduped_shadows", reader)

    report = propensity.build_propensity_report(days=9999)

    assert report["policy_epoch_status"] == "unavailable"
    assert report["policy_epoch_error"] == "policy_epoch_detection_failed"
    assert report["errors"] == ["policy_epoch_detection_failed"]
    assert report["n_total"] == 0
    reader.assert_not_called()


def test_epoch_detection_failure_scorecard_does_not_use_legacy_aggregate(monkeypatch, optional_evidence_jobs):
    import aletheia.analytics.family_scorecard as family_scorecard
    import extensions.counterfactual.trades as shadow_trades

    def fail_detector(**_kwargs):
        raise RuntimeError("temporary database lock")

    reader = Mock(return_value=[{"scanner_family": "momentum"}])

    monkeypatch.setattr(shadow_trades, "_detect_window_epochs", fail_detector)
    monkeypatch.setattr(shadow_trades, "list_deduped_shadows", reader)

    result = family_scorecard.compute_family_scorecard_with_epoch()

    assert result["shadow_epoch_status"] == "unavailable"
    assert result["shadow_epoch_error"] == "policy_epoch_detection_failed"
    assert result["errors"] == ["policy_epoch_detection_failed"]
    assert result["families"] == []
    reader.assert_not_called()


def test_scorecard_shadow_read_db_error_is_unavailable_not_all_null(optional_evidence_jobs):
    """A failing shadow_trades query is an unavailable source, never a legitimate zero."""
    import aletheia.analytics.family_scorecard as family_scorecard
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("DROP TABLE shadow_trades")
    db.commit()

    result = family_scorecard.compute_family_scorecard_with_epoch()

    assert result["shadow_epoch_status"] == "unavailable"
    assert result["shadow_epoch_error"] == "policy_epoch_detection_failed"


def test_successful_all_null_epoch_detection_keeps_legacy_reader(monkeypatch):
    import extensions.counterfactual.calibration as calibration

    seen = []

    monkeypatch.setattr(
        calibration,
        "_detect_window_epochs",
        lambda **_kwargs: (None, "all_null"),
    )
    monkeypatch.setattr(
        calibration,
        "list_deduped_shadows",
        lambda **kwargs: seen.append(kwargs) or [],
    )

    report = calibration.build_calibration(days=9999)

    assert report["policy_epoch_status"] == "all_null"
    assert report["policy_epoch_error"] is None
    assert not report["errors"]
    assert seen and seen[0]["policy_epoch_id"] is None
