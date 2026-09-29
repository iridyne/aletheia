"""Tests for extensions.counterfactual.propensity."""
from __future__ import annotations

from unittest.mock import patch

from extensions.counterfactual.propensity import (
    _encode,
    _fit,
    _predict,
    _roc_auc,
    build_propensity_report,
)


# ---------------------------------------------------------------------------
# Unit: internal helpers
# ---------------------------------------------------------------------------


def test_roc_auc_perfect():
    y = [0, 0, 1, 1]
    scores = [0.1, 0.2, 0.8, 0.9]
    assert _roc_auc(y, scores) == 1.0


def test_roc_auc_random():
    y = [0, 1, 0, 1]
    scores = [0.5, 0.5, 0.5, 0.5]
    assert _roc_auc(y, scores) == 0.5


def test_roc_auc_degenerate_all_positive():
    assert _roc_auc([1, 1, 1], [0.8, 0.6, 0.9]) == 0.5


def test_fit_predict_shape():
    X = [[0.1, 0.2, 0.0], [0.9, 0.8, 1.0], [0.5, 0.5, 0.5]]
    y = [0, 1, 0]
    w = _fit(X, y, l2=0.1, lr=0.1, max_iter=100)
    assert len(w) == 4  # 3 features + bias
    preds = _predict(X, w)
    assert len(preds) == 3
    assert all(0.0 <= p <= 1.0 for p in preds)


def test_encode_one_hot_leaves_out_last_family():
    rows = [
        {"trade_score": 60.0, "rank": 1, "scanner_family": "momentum", "direction": "long", "was_selected": 1},
        {"trade_score": 50.0, "rank": 2, "scanner_family": "short_bias", "direction": "short", "was_selected": 0},
        {"trade_score": 40.0, "rank": 3, "scanner_family": "derivatives", "direction": "long", "was_selected": 0},
    ]
    X, y, vocab, feature_names = _encode(rows)
    # 3 families → vocab has 2 entries (last one left out)
    assert len(vocab) == 2
    assert len(X[0]) == 4 + 2  # base + one_hot


# ---------------------------------------------------------------------------
# Integration: build_propensity_report
# ---------------------------------------------------------------------------


def test_propensity_insufficient_data():
    """With no data, errors list is populated and no exception is raised."""
    report = build_propensity_report(days=9999, min_samples=5)
    # _inmemory_db fixture gives a fresh DB per test, so 0 rows by default
    assert len(report["errors"]) > 0
    assert report["auc"] is None
    assert report["n_total"] == 0


def test_propensity_report_keys():
    """Report contains all documented keys even when data is insufficient."""
    report = build_propensity_report(days=9999, min_samples=9999)
    required_keys = {
        "n_total", "n_treated", "n_control", "treatment_rate", "auc",
        "feature_names", "coefficients", "propensity_stats", "by_family",
        "window_days", "k_folds", "notes", "warnings", "errors",
    }
    assert required_keys <= set(report.keys())
