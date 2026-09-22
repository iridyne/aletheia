"""Counterfactual cohort enrichment for the Core canonical trade scorecard."""

def read_shadow_cohort():
    """Preserve the existing single-epoch, deduplicated 90-day shadow cohort."""
    from extensions.counterfactual.trades import (
        _EPOCH_DETECTION_FAILURE_REASON, _detect_window_epochs, list_deduped_shadows,
    )
    error = None
    try:
        epoch_id, status = _detect_window_epochs(days=90)
    except Exception:
        epoch_id, status = None, "unavailable"
        error = _EPOCH_DETECTION_FAILURE_REASON
    if status not in {"single", "mixed", "all_null"}:
        epoch_id, status = None, "unavailable"
        error = _EPOCH_DETECTION_FAILURE_REASON
    rows = []
    if status in {"single", "all_null"}:
        rows = list_deduped_shadows(days=90, columns=("scanner_family",), policy_epoch_id=epoch_id)
    return epoch_id, status, error, rows
