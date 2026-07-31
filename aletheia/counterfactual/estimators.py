"""Off-policy estimators (IPS, DR) for shadow-trade calibration.

Implements two estimators targeting E[r] under the counterfactual policy
"select every candidate" (π_new(select=1|x) = 1), given historical data
collected under the behavioral policy π_b (the current conviction gate).

    IPS:  (1/n) Σ_i  [sel_i / prop_i]  * r_i
    DR:   (1/n) Σ_i  r̂(x_i) + [sel_i / prop_i] * (r_i − r̂(x_i))

DR is "doubly robust": consistent if either the propensity model or the
outcome model is correctly specified.  Both estimators require clipped
propensity scores; use ``aletheia.counterfactual.propensity.score_rows``
to obtain them.

Intended use: ``compute_ope_scores(rows)`` returns (propensities,
outcome_preds) aligned with *rows*; callers then invoke ``ips_estimate`` /
``dr_estimate`` per band with the corresponding sub-lists.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_L2_OUTCOME: float = 0.1
_CLIP_LO: float = 0.05
_CLIP_HI: float = 0.95


# ---------------------------------------------------------------------------
# Outcome model  (ridge regression: E[r | x], trained on selected rows only)
# ---------------------------------------------------------------------------

def _fit_outcome(
    sel_rows: list[dict],
    vocab: dict[str, int],
    *,
    l2: float = _L2_OUTCOME,
) -> list[float]:
    """Fit ridge regression for E[net_pnl_pct | x] on *selected rows* only.

    Uses the same feature encoding as the propensity model so both models
    share the same vocab.  Returns weight vector (len = n_features + 1,
    bias at end), or ``[]`` when there are too few rows to fit.
    """
    import numpy as np
    from aletheia.counterfactual.propensity import _encode

    if len(sel_rows) < 5:
        return []

    X_raw, _, _, _ = _encode(sel_rows, vocab=vocab)
    y = np.array([float(r.get("net_pnl_pct") or 0.0) for r in sel_rows])
    X = np.array(X_raw, dtype=np.float64)
    n, d = X.shape
    X_aug = np.column_stack([X, np.ones(n)])

    # Augmented-system ridge: stack [X; sqrt(λ)·I] and solve via lstsq.
    reg = np.sqrt(l2) * np.eye(d + 1)
    reg[-1, -1] = np.sqrt(l2 * 0.01)  # lighter regularization on bias
    X_ridge = np.vstack([X_aug, reg])
    y_ridge = np.append(y, np.zeros(d + 1))

    w, _, _, _ = np.linalg.lstsq(X_ridge, y_ridge, rcond=None)
    return w.tolist()


def _predict_outcome(
    rows: list[dict],
    weights: list[float],
    vocab: dict[str, int],
) -> list[float]:
    """Apply outcome model to *all* rows (selected + unselected).

    Returns the global mean of observed selected-row outcomes when *weights*
    is empty (fallback for too-few-data case).
    """
    if not weights:
        observed = [float(r.get("net_pnl_pct") or 0.0)
                    for r in rows if int(r.get("was_selected") or 0) == 1]
        fallback = sum(observed) / len(observed) if observed else 0.0
        return [fallback] * len(rows)

    import numpy as np
    from aletheia.counterfactual.propensity import _encode

    X_raw, _, _, _ = _encode(rows, vocab=vocab)
    X = np.array(X_raw, dtype=np.float64)
    X_aug = np.column_stack([X, np.ones(len(rows))])
    w = np.array(weights)
    return (X_aug @ w).tolist()


# ---------------------------------------------------------------------------
# Point estimators
# ---------------------------------------------------------------------------

def ips_estimate(
    rows: list[dict],
    propensities: list[float],
    *,
    clip_lo: float = _CLIP_LO,
    clip_hi: float = _CLIP_HI,
) -> dict[str, Any]:
    """Horvitz-Thompson IPS estimate of E[r] under π_new = 'select all'.

    Formula: (1/n) Σ_i  (sel_i / prop_i) · r_i
    Only selected rows (sel_i=1) contribute; the denominator is n_total.
    Returns ``ips_avg_net_pct`` and ``ips_effective_n`` (number of selected rows).
    """
    n = len(rows)
    if n == 0:
        return {"ips_avg_net_pct": None, "ips_effective_n": 0}

    total = 0.0
    eff_n = 0
    for r, prop in zip(rows, propensities):
        if int(r.get("was_selected") or 0) != 1:
            continue
        ri = float(r.get("net_pnl_pct") or 0.0)
        prop_c = max(clip_lo, min(clip_hi, prop))
        total += ri / prop_c
        eff_n += 1

    return {
        "ips_avg_net_pct": round(total / n, 3),
        "ips_effective_n": eff_n,
    }


def dr_estimate(
    rows: list[dict],
    propensities: list[float],
    outcome_preds: list[float],
    *,
    clip_lo: float = _CLIP_LO,
    clip_hi: float = _CLIP_HI,
) -> dict[str, Any]:
    """Doubly Robust estimate of E[r] under π_new = 'select all'.

    Formula: (1/n) Σ_i  r̂(x_i) + (sel_i / prop_i) · (r_i − r̂(x_i))
           = DM_term + IPS_correction_term

    Consistent if either propensity model or outcome model is correct.
    Returns ``dr_avg_net_pct`` (DR), ``dm_avg_net_pct`` (outcome model only).
    """
    n = len(rows)
    if n == 0:
        return {"dr_avg_net_pct": None, "dm_avg_net_pct": None}

    dm_total = sum(outcome_preds)
    correction = 0.0

    for r, prop, pred in zip(rows, propensities, outcome_preds):
        if int(r.get("was_selected") or 0) != 1:
            continue
        ri = float(r.get("net_pnl_pct") or 0.0)
        prop_c = max(clip_lo, min(clip_hi, prop))
        correction += (ri - pred) / prop_c

    dm_est = dm_total / n
    dr_est = dm_est + correction / n

    return {
        "dr_avg_net_pct": round(dr_est, 3),
        "dm_avg_net_pct": round(dm_est, 3),
    }


# ---------------------------------------------------------------------------
# Convenience entry point used by build_calibration(method="dr")
# ---------------------------------------------------------------------------

def compute_ope_scores(
    rows: list[dict],
    *,
    l2: float = _L2_OUTCOME,
    clip_lo: float = _CLIP_LO,
    clip_hi: float = _CLIP_HI,
) -> tuple[list[float], list[float]]:
    """Return ``(propensity_scores, outcome_predictions)`` aligned with *rows*.

    Both lists have the same length as *rows*.  Use them to call
    ``ips_estimate`` / ``dr_estimate`` per band in ``build_calibration``.

    Propensity scores are OOS cross-fitted and clipped to ``[clip_lo, clip_hi]``.
    Outcome predictions come from a ridge regression trained on selected rows
    only; predictions are extrapolated to unselected rows using the same model.
    """
    from aletheia.counterfactual.propensity import score_rows

    if not rows:
        return [], []

    propensities, vocab, _ = score_rows(rows, l2=l2, clip_lo=clip_lo, clip_hi=clip_hi)

    sel_rows = [r for r in rows if int(r.get("was_selected") or 0) == 1]
    outcome_w = _fit_outcome(sel_rows, vocab, l2=l2)
    outcome_preds = _predict_outcome(rows, outcome_w, vocab)

    return propensities, outcome_preds
