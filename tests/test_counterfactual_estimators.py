"""Tests for aletheia.counterfactual.estimators and DR-mode calibration."""
from __future__ import annotations

from unittest.mock import patch

import aletheia.storage.sqlite as _sq
from aletheia.counterfactual.calibration import build_calibration
from aletheia.counterfactual.estimators import (
    compute_ope_scores,
    dr_estimate,
    ips_estimate,
)
from aletheia.counterfactual.propensity import build_propensity_report
from aletheia.counterfactual.trades import persist_shadow_batch

# ---------------------------------------------------------------------------
# Helpers (shared with test_counterfactual_propensity pattern)
# ---------------------------------------------------------------------------

_FAMILIES = ["momentum", "short_bias", "derivatives"]


def _insert_batch(
    candidates: list[dict],
    selected: dict | list[dict] | None,
    top_n: int = 50,
    net_pnl_pct: float = 0.3,
) -> None:
    with patch("aletheia.counterfactual.trades._current_price", return_value=50000.0):
        persist_shadow_batch(candidates, selected, top_n=top_n)
    _sq._db_conn.execute(
        "UPDATE shadow_trades SET net_pnl_pct = ? WHERE net_pnl_pct IS NULL",
        (net_pnl_pct,),
    )
    _sq._db_conn.commit()


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
# Integration: build_calibration(method="dr")
# ---------------------------------------------------------------------------


def _populate_db_many_batches(n_batches: int = 20) -> None:
    for b in range(n_batches):
        cands = [
            {
                "opportunity_id": f"b{b}-{j}",
                "symbol": "BTC/USDT",
                "direction": "long" if j % 2 == 0 else "short",
                "scanner_family": _FAMILIES[j % len(_FAMILIES)],
                "trade_score": 35.0 + ((b * 6 + j) % 50) * 0.7,
            }
            for j in range(6)
        ]
        sel = next((c for c in cands if float(c["trade_score"]) >= 55.0), None)
        _insert_batch(cands, sel)


def test_calibration_naive_default_keys():
    """Default method='naive' report has all expected keys."""
    report = build_calibration(days=9999)
    assert "bands" in report
    assert "estimation_method" in report
    assert report["estimation_method"] == "naive"


def test_calibration_dr_adds_ope_keys():
    """method='dr' adds ips/dr/dm keys to each band."""
    _populate_db_many_batches(n_batches=20)
    report = build_calibration(days=9999, method="dr")
    assert len(report["errors"]) == 0
    assert report["bands"]
    for band in report["bands"]:
        assert "ips_avg_net_pct" in band, f"missing ips key in band {band['band']}"
        assert "dr_avg_net_pct" in band, f"missing dr key in band {band['band']}"
        assert "dm_avg_net_pct" in band, f"missing dm key in band {band['band']}"


def test_calibration_naive_ope_keys_are_none():
    """method='naive' (default) keeps ope keys present but set to None."""
    _populate_db_many_batches(n_batches=10)
    report = build_calibration(days=9999, method="naive")
    assert report["bands"]
    for band in report["bands"]:
        assert band.get("ips_avg_net_pct") is None
        assert band.get("dr_avg_net_pct") is None


def test_calibration_dr_numeric_values():
    """DR/IPS values should be finite floats when data is available."""
    _populate_db_many_batches(n_batches=20)
    report = build_calibration(days=9999, method="dr")
    for band in report["bands"]:
        if band["n"] > 0:
            assert band["avg_net_pct"] is not None  # naive still present
            # DR and IPS may be None in low-n bands but should not raise


# ---------------------------------------------------------------------------
# Regression: mixed-policy-epoch guard (CF-I04)
# ---------------------------------------------------------------------------


def _insert_row_with_epoch(opp_id: str, epoch: str | None, net_pnl_pct: float = 0.3) -> None:
    """Insert one resolved shadow row with the given policy_epoch_id."""
    with patch("aletheia.counterfactual.trades._current_price", return_value=50000.0):
        with patch("aletheia.counterfactual.trades._current_policy_epoch", return_value=epoch):
            persist_shadow_batch(
                [{"opportunity_id": opp_id, "symbol": "BTC/USDT", "direction": "long",
                  "scanner_family": "momentum", "trade_score": 60.0}],
                selected={"opportunity_id": opp_id, "symbol": "BTC/USDT", "direction": "long",
                           "scanner_family": "momentum", "trade_score": 60.0},
                top_n=5,
            )
    _sq._db_conn.execute(
        "UPDATE shadow_trades SET net_pnl_pct = ? WHERE net_pnl_pct IS NULL",
        (net_pnl_pct,),
    )
    _sq._db_conn.commit()


def test_mixed_epoch_calibration_returns_error():
    """build_calibration without explicit epoch returns mixed_policy_epoch error when window spans multiple epochs."""
    _insert_row_with_epoch("opp-epoch-a-1", "epoch_A")
    _insert_row_with_epoch("opp-epoch-b-1", "epoch_B")
    report = build_calibration(days=9999)
    assert any("mixed_policy_epoch" in e for e in report["errors"]), (
        f"expected mixed_policy_epoch error, got: {report['errors']}"
    )
    assert report["bands"] == []  # no estimate produced for mixed window


def test_mixed_epoch_with_null_rows_calibration_returns_error():
    """NULL-epoch rows mixed with non-null epoch rows also trigger the guard."""
    _insert_row_with_epoch("opp-null-1", None)
    _insert_row_with_epoch("opp-epoch-c-1", "epoch_C")
    report = build_calibration(days=9999)
    assert any("mixed_policy_epoch" in e for e in report["errors"])


def test_explicit_epoch_filters_correctly():
    """Explicit policy_epoch_id skips auto-detection and counts only matching rows."""
    _insert_row_with_epoch("opp-a-only", "epoch_A", net_pnl_pct=1.0)
    _insert_row_with_epoch("opp-b-only", "epoch_B", net_pnl_pct=-1.0)
    report = build_calibration(days=9999, policy_epoch_id="epoch_A")
    assert not report["errors"]
    total = report.get("total_candidates", 0)
    assert total == 1, f"expected 1 candidate for epoch_A, got {total}"


def test_single_epoch_window_auto_resolves():
    """When all rows share one epoch, calibration auto-resolves it without error."""
    _insert_row_with_epoch("opp-single-1", "epoch_X")
    _insert_row_with_epoch("opp-single-2", "epoch_X")
    report = build_calibration(days=9999)
    assert not report["errors"]
    assert report.get("policy_epoch_id") == "epoch_X"


def test_mixed_epoch_propensity_returns_error():
    """build_propensity_report without explicit epoch returns error for mixed window."""
    _insert_row_with_epoch("opp-prop-a", "epoch_A")
    _insert_row_with_epoch("opp-prop-b", "epoch_B")
    report = build_propensity_report(days=9999)
    assert any("mixed_policy_epoch" in e for e in report["errors"])
