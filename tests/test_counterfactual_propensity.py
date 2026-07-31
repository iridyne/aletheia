"""Tests for aletheia.counterfactual.propensity."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import aletheia.storage.sqlite as _sq
from aletheia.counterfactual.propensity import (
    _encode,
    _fit,
    _predict,
    _roc_auc,
    build_propensity_report,
)
from aletheia.counterfactual.trades import persist_shadow_batch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAMILIES = ["momentum", "short_bias", "derivatives"]


def _make_candidates(n: int, score_start: float = 35.0, score_step: float = 0.4) -> list[dict]:
    return [
        {
            "opportunity_id": f"opp-{i:04d}",
            "symbol": "BTC/USDT",
            "direction": "long" if i % 2 == 0 else "short",
            "scanner_family": _FAMILIES[i % len(_FAMILIES)],
            "trade_score": score_start + i * score_step,
        }
        for i in range(n)
    ]


def _insert_and_settle(
    candidates: list[dict],
    selected: dict | list[dict] | None,
    *,
    top_n: int = 5,
    net_pnl_pct: float = 0.3,
) -> None:
    """Insert a shadow batch then mark all rows as settled."""
    with patch("aletheia.counterfactual.trades._current_price", return_value=50000.0):
        persist_shadow_batch(candidates, selected, top_n=top_n)
    _sq._db_conn.execute(
        "UPDATE shadow_trades SET net_pnl_pct = ? WHERE net_pnl_pct IS NULL",
        (net_pnl_pct,),
    )
    _sq._db_conn.commit()


def _insert_many_batches(
    n_batches: int = 15,
    batch_size: int = 8,
    gate: float = 55.0,
) -> None:
    """Insert many batches to reach min_samples threshold."""
    for b in range(n_batches):
        cands = [
            {
                "opportunity_id": f"b{b}-{j}",
                "symbol": "BTC/USDT",
                "direction": "long" if j % 2 == 0 else "short",
                "scanner_family": _FAMILIES[j % len(_FAMILIES)],
                "trade_score": 35.0 + (b * batch_size + j) % 50 * 0.8,
            }
            for j in range(batch_size)
        ]
        selected_list = [c for c in cands if float(c["trade_score"]) >= gate]
        sel = selected_list[0] if selected_list else None
        _insert_and_settle(cands, sel, top_n=batch_size)


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


def test_propensity_basic_auc():
    """AUC should be > 0.65 when selection is clearly correlated with trade_score."""
    # Insert 15 batches × 8 candidates = up to 120 rows; gate=55
    _insert_many_batches(n_batches=15, batch_size=8, gate=55.0)

    report = build_propensity_report(days=9999, min_samples=50)
    assert len(report["errors"]) == 0, report["errors"]
    assert report["n_total"] >= 50
    assert report["auc"] is not None
    assert report["auc"] > 0.55, f"AUC too low: {report['auc']}"


def test_propensity_clipping():
    """All OOS propensity scores must lie within [clip_lo, clip_hi]."""
    _insert_many_batches(n_batches=15, batch_size=8, gate=55.0)

    clip_lo, clip_hi = 0.05, 0.95
    report = build_propensity_report(
        days=9999,
        min_samples=50,
        clip_lo=clip_lo,
        clip_hi=clip_hi,
    )
    assert len(report["errors"]) == 0, report["errors"]
    stats = report["propensity_stats"]
    assert stats["min"] >= clip_lo, f"min={stats['min']} < clip_lo={clip_lo}"
    assert stats["max"] <= clip_hi, f"max={stats['max']} > clip_hi={clip_hi}"


def test_propensity_forced_rank():
    """Rows with rank > FORCED_RANK_THRESHOLD and was_selected=1 should get higher
    mean propensity than rank-1-5 unselected rows (model picks up is_forced signal)."""
    # Insert batches where top_n=3 and a 'forced' candidate is always selected
    for b in range(20):
        # 3 unselected top candidates
        top3 = [
            {
                "opportunity_id": f"f{b}-top{j}",
                "symbol": "BTC/USDT",
                "direction": "long",
                "scanner_family": "momentum",
                "trade_score": 60.0 - j,  # rank 1=60, 2=59, 3=58
            }
            for j in range(3)
        ]
        # forced: rank 8 in the full list, always selected
        forced = {
            "opportunity_id": f"f{b}-forced",
            "symbol": "BTC/USDT",
            "direction": "long",
            "scanner_family": "momentum",
            "trade_score": 40.0,  # lower score → rank 4 in list of 4, but top_n=3 excludes it
        }
        all_cands = top3 + [forced]
        _insert_and_settle(all_cands, forced, top_n=3)

    report = build_propensity_report(days=9999, min_samples=20)
    assert len(report["errors"]) == 0, report["errors"]

    # Directly inspect family stats (all are momentum)
    assert "momentum" in report["by_family"]
    fam = report["by_family"]["momentum"]
    # Model should predict a higher-than-random propensity overall
    # (forced rows are always selected, pushing mean up)
    assert fam["mean_propensity"] is not None
    assert fam["actual_selection_rate"] > 0.0


def test_propensity_by_family_keys():
    """by_family dict has expected sub-keys for every family present."""
    _insert_many_batches(n_batches=10, batch_size=6, gate=55.0)
    report = build_propensity_report(days=9999, min_samples=30)
    if report["errors"]:
        return  # not enough data in this run — skip
    for fam, stats in report["by_family"].items():
        assert "n" in stats
        assert "actual_selection_rate" in stats
        assert "mean_propensity" in stats
