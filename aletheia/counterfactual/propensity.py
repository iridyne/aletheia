"""Propensity model for shadow_trades selection probability.

Estimates P(was_selected=1 | features) — the behavioral policy π_b(a|x) —
using L2-regularised logistic regression fit via gradient descent (numpy).
Cross-fitting (k-fold, stratified) yields out-of-sample propensity scores that
are unbiased for downstream IPS/DR estimators.

Methodological note: we fit on resolved rows only (net_pnl_pct IS NOT NULL).
The selection mechanism operates at scan time, before resolution, so this
introduces a survivorship filter.  In practice the gap is small provided the
resolution rate is high (≥50%), but Phase 2 should verify with a parallel fit
on all rows (including unresolved) once a broader query is available.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from aletheia.counterfactual.trades import _detect_window_epochs, list_deduped_shadows

logger = logging.getLogger(__name__)

_CLIP_LO: float = 0.05
_CLIP_HI: float = 0.95
_K_FOLDS: int = 5
_L2: float = 0.1
_LR: float = 0.1
_MAX_ITER: int = 500
# Candidates ranked above this position in their batch are always selected
# (forced-selection rows appended beyond top_n by persist_shadow_batch).
_FORCED_RANK_THRESHOLD: int = 5


# ---------------------------------------------------------------------------
# Feature encoding
# ---------------------------------------------------------------------------

def _encode(
    rows: list[dict],
    *,
    vocab: dict[str, int] | None = None,
) -> tuple[list[list[float]], list[int], dict[str, int], list[str]]:
    """Encode shadow rows into a numeric feature matrix.

    Returns ``(X, y, vocab, feature_names)`` where *X* is a list of float
    vectors, *y* is 0/1 labels, *vocab* maps scanner_family → one-hot column
    index, and *feature_names* is the ordered column name list.

    If *vocab* is ``None`` it is built from the data (leave-one-out to avoid
    perfect multicollinearity).  Pass an existing vocab when scoring new rows
    with a model trained on different data.
    """
    scores = [float(r.get("trade_score") or 0.0) for r in rows]
    score_min = min(scores) if scores else 0.0
    score_max = max(scores) if scores else 1.0
    score_range = score_max - score_min if score_max > score_min else 1.0

    if vocab is None:
        families = sorted({r.get("scanner_family") or "unknown" for r in rows})
        # Leave out last family to avoid a perfectly collinear column.
        vocab = {f: i for i, f in enumerate(families[:-1])}

    feature_names = [
        "trade_score_norm",
        "rank_topn_norm",
        "is_forced",
        "direction_long",
    ]
    for fam in sorted(vocab, key=lambda k: vocab[k]):
        feature_names.append(f"family_{fam}")

    X: list[list[float]] = []
    y: list[int] = []

    for r in rows:
        score = float(r.get("trade_score") or 0.0)
        rank = int(r.get("rank") or 1)
        fam = r.get("scanner_family") or "unknown"
        direction = r.get("direction") or "long"
        was_selected = int(r.get("was_selected") or 0)

        score_norm = (score - score_min) / score_range
        rank_topn = min(rank, _FORCED_RANK_THRESHOLD)
        rank_norm = rank_topn / _FORCED_RANK_THRESHOLD  # maps to (0, 1]
        is_forced = 1.0 if rank > _FORCED_RANK_THRESHOLD else 0.0
        direction_long = 1.0 if direction == "long" else 0.0

        one_hot = [0.0] * len(vocab)
        if fam in vocab:
            one_hot[vocab[fam]] = 1.0

        X.append([score_norm, rank_norm, is_forced, direction_long] + one_hot)
        y.append(was_selected)

    return X, y, vocab, feature_names


# ---------------------------------------------------------------------------
# Logistic regression (numpy)
# ---------------------------------------------------------------------------

def _fit(
    X_rows: list[list[float]],
    y_list: list[int],
    *,
    l2: float = _L2,
    lr: float = _LR,
    max_iter: int = _MAX_ITER,
) -> list[float]:
    """Gradient-descent logistic regression with L2 regularisation.

    Returns weight vector of length ``n_features + 1`` (bias term appended).
    """
    import numpy as np

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_list, dtype=np.float64)
    n, d = X.shape
    X_aug = np.column_stack([X, np.ones(n)])
    w = np.zeros(d + 1)

    for _ in range(max_iter):
        z = np.clip(X_aug @ w, -500.0, 500.0)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = X_aug.T @ (p - y) / n
        grad[:d] += l2 * w[:d]  # L2 on features only, not bias
        w -= lr * grad

    return w.tolist()


def _predict(X_rows: list[list[float]], weights: list[float]) -> list[float]:
    """Sigmoid predictions for a fitted weight vector."""
    import numpy as np

    X = np.array(X_rows, dtype=np.float64)
    w = np.array(weights)
    d = X.shape[1]
    X_aug = np.column_stack([X, np.ones(len(X_rows))])
    z = np.clip(X_aug @ w, -500.0, 500.0)
    return (1.0 / (1.0 + np.exp(-z))).tolist()


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def _roc_auc(y_true: list[int], y_score: list[float]) -> float:
    """ROC-AUC via score-bucket accumulation (handles ties via 0.5 credit)."""
    pos = sum(y_true)
    neg = len(y_true) - pos
    if pos == 0 or neg == 0:
        return 0.5

    # Group labels by score value; process descending, crediting tied
    # positives with 0.5 each (standard Wilcoxon / trapezoidal rule for ties).
    buckets: dict[float, tuple[int, int]] = {}
    for label, score in zip(y_true, y_score):
        p_count, n_count = buckets.get(score, (0, 0))
        if label:
            buckets[score] = (p_count + 1, n_count)
        else:
            buckets[score] = (p_count, n_count + 1)

    tp = 0
    auc = 0.0
    for score in sorted(buckets.keys(), reverse=True):
        bp, bn = buckets[score]
        auc += (tp + 0.5 * bp) * bn
        tp += bp

    return auc / (pos * neg)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = p / 100.0 * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (idx - lo) * (s[hi] - s[lo])


def _stratified_folds(
    y: list[int], k: int, seed: int = 42
) -> list[tuple[list[int], list[int]]]:
    """Stratified k-fold split.  Returns list of (train_idx, val_idx)."""
    rng = random.Random(seed)
    pos_idx = [i for i, v in enumerate(y) if v == 1]
    neg_idx = [i for i, v in enumerate(y) if v == 0]
    rng.shuffle(pos_idx)
    rng.shuffle(neg_idx)

    folds: list[tuple[list[int], list[int]]] = []
    for k_i in range(k):
        pos_val = pos_idx[k_i::k]
        neg_val = neg_idx[k_i::k]
        val = sorted(pos_val + neg_val)
        val_set = set(val)
        train = [i for i in range(len(y)) if i not in val_set]
        folds.append((train, val))
    return folds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_rows(
    rows: list[dict],
    *,
    l2: float = _L2,
    k_folds: int = _K_FOLDS,
    clip_lo: float = _CLIP_LO,
    clip_hi: float = _CLIP_HI,
) -> tuple[list[float], dict[str, int], list[str]]:
    """Fit propensity model on *rows* and return ``(scores, vocab, feature_names)``.

    Scores are clipped to ``[clip_lo, clip_hi]`` and aligned 1-to-1 with *rows*.
    The returned *vocab* enables consistent feature encoding when an outcome
    model is trained on the same data (used by ``estimators.compute_ope_scores``).

    Returns ``([0.5, ...], {}, [])`` when *rows* has fewer than ``k_folds * 2``
    entries (insufficient for stratified splitting).
    """
    min_for_cv = k_folds * 2
    if len(rows) < min_for_cv:
        return [0.5] * len(rows), {}, []

    X, y, vocab, feature_names = _encode(rows)
    oos = [0.0] * len(rows)
    for train_idx, val_idx in _stratified_folds(y, k=k_folds):
        if not train_idx or not val_idx:
            continue
        w = _fit([X[i] for i in train_idx], [y[i] for i in train_idx], l2=l2)
        preds = _predict([X[i] for i in val_idx], w)
        for j, p in zip(val_idx, preds):
            oos[j] = p

    clipped = [max(clip_lo, min(clip_hi, s)) for s in oos]
    return clipped, vocab, feature_names


def build_propensity_report(
    *,
    days: int = 30,
    scanner_family: str | None = None,
    policy_epoch_id: str | None = None,
    k_folds: int = _K_FOLDS,
    l2: float = _L2,
    clip_lo: float = _CLIP_LO,
    clip_hi: float = _CLIP_HI,
    min_samples: int = 50,
) -> dict[str, Any]:
    """Fit a propensity model on shadow data and return a diagnostic report.

    Read-only — does not write to DB or disk.  Outputs are intended for use as
    π_b(a|x) estimates in IPS/DR off-policy evaluation (Phase 2).

    Args:
        days: lookback window in days passed to ``list_deduped_shadows``.
        scanner_family: restrict to one scanner family.
        policy_epoch_id: restrict to one policy epoch.
        k_folds: number of cross-fitting folds.
        l2: L2 regularisation strength.
        clip_lo / clip_hi: propensity clipping bounds (prevents extreme IPS weights).
        min_samples: minimum resolved rows required; returns error if below.

    Returns:
        dict with keys: n_total, n_treated, n_control, treatment_rate, auc,
        feature_names, coefficients, propensity_stats, by_family, window_days,
        k_folds, notes, warnings, errors.
    """
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if policy_epoch_id is None:
        try:
            _epoch_id, _epoch_status = _detect_window_epochs(days=days)
        except Exception:
            _epoch_status, _epoch_id = "all_null", None
        if _epoch_status == "mixed":
            errors.append(
                "mixed_policy_epoch: window spans multiple policy epochs; "
                "re-run with explicit policy_epoch_id"
            )
            return {
                "n_total": 0, "n_treated": 0, "n_control": 0,
                "treatment_rate": None, "auc": None,
                "feature_names": [], "coefficients": [], "propensity_stats": {},
                "by_family": {}, "window_days": days, "k_folds": k_folds,
                "notes": notes, "warnings": warnings, "errors": errors,
            }
        if _epoch_status == "single":
            policy_epoch_id = _epoch_id

    rows = list_deduped_shadows(
        days=days,
        scanner_family=scanner_family,
        policy_epoch_id=policy_epoch_id,
        columns=("trade_score", "rank", "scanner_family", "direction", "net_pnl_pct", "was_selected"),
    )

    _empty = {
        "n_total": len(rows),
        "n_treated": sum(1 for r in rows if int(r.get("was_selected") or 0)),
        "n_control": sum(1 for r in rows if not int(r.get("was_selected") or 0)),
        "treatment_rate": None,
        "auc": None,
        "feature_names": [],
        "coefficients": [],
        "propensity_stats": {},
        "by_family": {},
        "window_days": days,
        "k_folds": k_folds,
        "notes": notes,
        "warnings": warnings,
        "errors": errors,
    }

    if len(rows) < min_samples:
        errors.append(
            f"Insufficient data: {len(rows)} rows, need >= {min_samples}. "
            "Increase days or wait for more settlements."
        )
        return _empty

    X, y, vocab, feature_names = _encode(rows)
    n_treated = sum(y)
    n_control = len(y) - n_treated
    treatment_rate = n_treated / len(y)

    if treatment_rate < 0.02 or treatment_rate > 0.98:
        warnings.append(
            f"Degenerate treatment rate: {treatment_rate:.3f}. "
            "Propensity estimates may be unreliable near boundary."
        )

    # --- Cross-fitting: OOS propensity scores ---
    oos_scores = [0.0] * len(rows)
    folds = _stratified_folds(y, k=k_folds)
    for train_idx, val_idx in folds:
        if not train_idx or not val_idx:
            continue
        X_train = [X[i] for i in train_idx]
        y_train = [y[i] for i in train_idx]
        X_val = [X[i] for i in val_idx]
        w_fold = _fit(X_train, y_train, l2=l2, lr=_LR, max_iter=_MAX_ITER)
        fold_preds = _predict(X_val, w_fold)
        for j, score in zip(val_idx, fold_preds):
            oos_scores[j] = score

    oos_clipped = [max(clip_lo, min(clip_hi, s)) for s in oos_scores]
    auc = _roc_auc(y, oos_clipped)

    if auc < 0.65:
        warnings.append(
            f"Low OOS AUC: {auc:.3f}. Model has limited discriminative power. "
            "IPS weights derived from this model will be noisy."
        )

    # --- Final model on all data (for scoring new rows) ---
    final_w = _fit(X, y, l2=l2, lr=_LR, max_iter=_MAX_ITER)

    # --- Summary statistics on clipped OOS scores ---
    def _pct(p: float) -> float:
        return round(_percentile(oos_clipped, p), 4)

    propensity_stats = {
        "mean": round(sum(oos_clipped) / len(oos_clipped), 4),
        "min": round(min(oos_clipped), 4),
        "p5": _pct(5),
        "p25": _pct(25),
        "p50": _pct(50),
        "p75": _pct(75),
        "p95": _pct(95),
        "max": round(max(oos_clipped), 4),
    }

    # --- Per-family breakdown ---
    family_buckets: dict[str, dict] = {}
    for row, score in zip(rows, oos_clipped):
        fam = row.get("scanner_family") or "unknown"
        sel = int(row.get("was_selected") or 0)
        if fam not in family_buckets:
            family_buckets[fam] = {"n": 0, "_sel": 0, "_scores": []}
        family_buckets[fam]["n"] += 1
        family_buckets[fam]["_sel"] += sel
        family_buckets[fam]["_scores"].append(score)

    by_family: dict[str, dict] = {
        fam: {
            "n": d["n"],
            "actual_selection_rate": round(d["_sel"] / d["n"], 4) if d["n"] else None,
            "mean_propensity": round(sum(d["_scores"]) / d["n"], 4) if d["n"] else None,
        }
        for fam, d in family_buckets.items()
    }

    notes.append(
        f"rank > {_FORCED_RANK_THRESHOLD} rows (forced selections) encoded with "
        f"is_forced=1; their propensity is expected near clip_hi={clip_hi}."
    )
    notes.append(
        "Propensity fitted on resolved rows only (net_pnl_pct IS NOT NULL). "
        "This introduces a survivorship filter; validate against all-rows fit in Phase 2."
    )
    if scanner_family:
        notes.append(f"Filtered to scanner_family={scanner_family!r}.")
    if policy_epoch_id:
        notes.append(f"Filtered to policy_epoch_id={policy_epoch_id!r}.")

    return {
        "n_total": len(rows),
        "n_treated": n_treated,
        "n_control": n_control,
        "treatment_rate": round(treatment_rate, 4),
        "auc": round(auc, 4),
        "feature_names": feature_names,
        "coefficients": [round(c, 6) for c in final_w],
        "propensity_stats": propensity_stats,
        "by_family": by_family,
        "window_days": days,
        "k_folds": k_folds,
        "notes": notes,
        "warnings": warnings,
        "errors": errors,
    }
