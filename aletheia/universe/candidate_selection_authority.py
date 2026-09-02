"""Single qualification and owner-admission gate for Candidate authority."""

from __future__ import annotations

from typing import Mapping

from aletheia.config.schema import UniverseConfig


LEGACY_CANDIDATE_POLICY = "legacy_total_score_v1"
TARGET_CANDIDATE_POLICY = "candidate_selection_utility_v1"
_PAPER_STAGES = frozenset({"paper_10", "paper_25", "paper_50", "paper_active"})


def candidate_selection_policy_identity(cfg: object) -> dict[str, object]:
    universe = getattr(cfg, "universe", cfg)
    if not isinstance(universe, UniverseConfig):
        raise TypeError("candidate_selection_universe_config_required")
    identity: dict[str, object] = {
        "max_candidates": universe.max_candidates,
        "min_volume_24h_usdt": universe.min_volume_24h_usdt,
        "max_spread_bps": universe.max_spread_bps,
        "min_depth_10bps_usd": universe.min_depth_10bps_usd,
        "blacklist": sorted({str(item).upper() for item in universe.blacklist}),
    }
    if all(hasattr(cfg, field) for field in ("fast_trigger", "exchange", "market_data")):
        from aletheia.universe.fast_stream import fast_market_data_venue_for_config

        identity["action_route"] = {
            "fast_trigger_mode": cfg.fast_trigger.mode,
            "monitoring_venue": fast_market_data_venue_for_config(cfg),
            "paper_execution_mode": cfg.exchange.paper_execution_mode,
        }
    if hasattr(cfg, "trading"):
        identity["cost_assumptions"] = {
            "execution_fee_pct": cfg.trading.execution_fee_pct,
            "slippage_bps": cfg.trading.slippage_bps,
        }
    return identity


def learning_candidate_selection_overlap_block_reason(
    path_id: str,
    cfg: object,
) -> str | None:
    """Block Universe-family Learning authority while Candidate target owns ranking."""

    if path_id != "universe_family_calibration":
        return None
    try:
        policy = cfg.universe.candidate_selection_policy
    except (AttributeError, TypeError):
        return "candidate_selection_policy_evidence_unavailable"
    if policy == TARGET_CANDIDATE_POLICY:
        return "learning_overlapping_candidate_selection_authority"
    return None


def candidate_selection_overlap_block_reason() -> str | None:
    """Prevent two Candidate-ranking authorities from operating together."""

    try:
        from aletheia.learning.ledger import list_open_experiments

        rows = list_open_experiments(path_id="universe_family_calibration")
    except Exception:
        return "candidate_selection_overlap_evidence_unavailable"
    if any(str(row.get("stage") or "") in _PAPER_STAGES for row in rows):
        return "candidate_selection_overlapping_learning_authority"
    return None


def candidate_selection_authority_block_reason(
    cfg: object,
    qualification: Mapping[str, object],
) -> str | None:
    """Return why configured target authority cannot run; legacy is always valid."""

    universe = getattr(cfg, "universe", cfg)
    if not isinstance(universe, UniverseConfig):
        return "candidate_selection_universe_config_unavailable"
    if universe.candidate_selection_policy == LEGACY_CANDIDATE_POLICY:
        return None
    expected_epoch = str(universe.candidate_selection_policy_epoch or "")
    if not expected_epoch:
        return "candidate_selection_policy_epoch_missing"
    if qualification.get("verdict") != "positive":
        return "candidate_selection_qualification_not_positive"
    if qualification.get("promotion_eligible") is not True:
        return "candidate_selection_promotion_not_eligible"
    if str(qualification.get("policy_epoch_id") or "") != expected_epoch:
        return "candidate_selection_policy_epoch_mismatch"
    if qualification.get("selection_policy") != candidate_selection_policy_identity(cfg):
        return "candidate_selection_policy_identity_mismatch"
    if qualification.get("blocking_reasons") or qualification.get("invalid_reasons"):
        return "candidate_selection_qualification_invalid"
    return None


__all__ = [
    "LEGACY_CANDIDATE_POLICY",
    "TARGET_CANDIDATE_POLICY",
    "candidate_selection_policy_identity",
    "learning_candidate_selection_overlap_block_reason",
    "candidate_selection_overlap_block_reason",
    "candidate_selection_authority_block_reason",
]
