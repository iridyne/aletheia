"""Operational Candidate identity and historical transition recovery."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Mapping

from aletheia.config.loader import get_data_dir, mutate_config
from aletheia.config.schema import UniverseConfig


LEGACY_CANDIDATE_POLICY = "legacy_total_score_v1"
TARGET_CANDIDATE_POLICY = "candidate_selection_utility_v1"
_PAPER_STAGES = frozenset({"paper_10", "paper_25", "paper_50", "paper_active"})
_TRANSITION_JOURNAL = "candidate_selection_authority_transition.json"


def _transition_state(universe: UniverseConfig) -> dict[str, str | None]:
    return {
        "candidate_selection_policy": universe.candidate_selection_policy,
        "candidate_selection_policy_epoch": universe.candidate_selection_policy_epoch,
    }


def _sync_transition_directory(path: object) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def stage_candidate_selection_authority_transition(
    *,
    audit_event_id: str,
    before: UniverseConfig,
    after: UniverseConfig,
) -> None:
    """Durably record how to recover the config around the SQLite commit."""

    path = get_data_dir() / _TRANSITION_JOURNAL
    payload = {
        "version": "candidate_selection_authority_transition.v1",
        "audit_event_id": audit_event_id,
        "before": _transition_state(before),
        "after": _transition_state(after),
    }
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_transition_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def recover_candidate_selection_authority_transition() -> bool:
    """Resolve an interrupted config/audit transition from committed SQLite truth."""

    path = get_data_dir() / _TRANSITION_JOURNAL
    if not path.exists():
        return False
    from aletheia.storage import repositories as repo

    with repo.transaction(immediate=True):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != "candidate_selection_authority_transition.v1":
            raise RuntimeError("candidate_selection_transition_journal_invalid")
        event_id = str(payload.get("audit_event_id") or "")
        committed = repo.get_db().execute(
            "SELECT 1 FROM audit_events WHERE id=? LIMIT 1", (event_id,),
        ).fetchone()
        desired = payload.get("after" if committed else "before")
        if not isinstance(desired, dict):
            raise RuntimeError("candidate_selection_transition_journal_invalid")

        def reconcile(cfg: object) -> bool:
            current = cfg.universe
            proposed = UniverseConfig.model_validate({
                **current.model_dump(mode="json"),
                "candidate_selection_policy": desired.get("candidate_selection_policy"),
                "candidate_selection_policy_epoch": desired.get(
                    "candidate_selection_policy_epoch"
                ),
            })
            if proposed == current:
                return False
            cfg.universe = proposed
            return True

        mutate_config(reconcile)
        path.unlink()
        _sync_transition_directory(path.parent)
    return True


def candidate_selection_policy_identity(
    cfg: object,
    *,
    family_spread_tolerances: Mapping[str, object] | None = None,
    family_spread_evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    universe = getattr(cfg, "universe", cfg)
    if not isinstance(universe, UniverseConfig):
        raise TypeError("candidate_selection_universe_config_required")
    identity: dict[str, object] = {
        "semantic_manifest": candidate_selection_semantic_manifest(),
        "max_candidates": universe.max_candidates,
        "short_enabled": getattr(getattr(cfg, "regime", None), "short_enabled", True),
        "min_volume_24h_usdt": float(universe.min_volume_24h_usdt),
        "max_spread_bps": float(universe.max_spread_bps),
        "min_depth_10bps_usd": float(universe.min_depth_10bps_usd),
        "blacklist": sorted({str(item).upper() for item in universe.blacklist}),
    }
    if all(hasattr(cfg, field) for field in ("fast_trigger", "exchange", "market_data")):
        from aletheia.universe.fast_stream import fast_market_data_venue_for_config

        identity["action_route"] = {
            "fast_trigger_mode": cfg.fast_trigger.mode,
            "monitoring_venue": fast_market_data_venue_for_config(cfg),
            "paper_execution_mode": cfg.exchange.paper_execution_mode,
            "enabled_markets": {
                str(market): bool(enabled)
                for market, enabled in sorted(cfg.exchange.enabled_markets.items())
            },
        }
        identity["preferred_market_type"] = cfg.exchange.preferred_market_type
    if hasattr(cfg, "trading"):
        identity["cost_assumptions"] = {
            "execution_fee_pct": cfg.trading.execution_fee_pct,
            "slippage_bps": cfg.trading.slippage_bps,
        }
    return identity


def candidate_selection_semantic_manifest() -> dict[str, str]:
    """Version only the code contracts that can change Candidate economics."""

    from aletheia.universe.candidate_eligibility import (
        HARD_GATE_POLICY_VERSION,
        HARD_GATE_VERSION,
    )
    from aletheia.universe.candidate_selection_utility import (
        CANDIDATE_SELECTION_UTILITY_VERSION,
    )
    from aletheia.universe.continuous_scoring import CONTINUOUS_SCORER_BEHAVIOR_VERSION
    from aletheia.universe.economic_utility import (
        CANDIDATE_SELECTION_OUTCOME_VERSION,
        ECONOMIC_ESTIMATOR_VERSION,
    )
    from aletheia.universe.opportunity_evidence import OPPORTUNITY_EVIDENCE_VERSION
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION
    from aletheia.universe.features import CANDIDATE_FEATURE_ACQUISITION_VERSION

    return {
        "feature_acquisition": CANDIDATE_FEATURE_ACQUISITION_VERSION,
        "hard_gate": HARD_GATE_VERSION,
        "hard_gate_policy": HARD_GATE_POLICY_VERSION,
        "opportunity_evidence": OPPORTUNITY_EVIDENCE_VERSION,
        "continuous_scoring": CONTINUOUS_SCORER_BEHAVIOR_VERSION,
        "economic_estimator": ECONOMIC_ESTIMATOR_VERSION,
        "outcome_contract": CANDIDATE_SELECTION_OUTCOME_VERSION,
        "cost_model": COST_MODEL_VERSION,
        "candidate_selection": CANDIDATE_SELECTION_UTILITY_VERSION,
        "actionability": "supported-mechanism-and-fast-route-before-capacity/v2",
    }


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


def candidate_selection_authority_block_reason(cfg: object) -> str | None:
    """Validate operational identity, never statistical profitability."""
    from aletheia.universe.candidate_eligibility import canonical_fingerprint

    universe = getattr(cfg, "universe", cfg)
    if not isinstance(universe, UniverseConfig):
        return "candidate_selection_universe_config_unavailable"
    if universe.candidate_selection_policy != TARGET_CANDIDATE_POLICY:
        return "candidate_selection_legacy_runtime_removed"
    expected_epoch = canonical_fingerprint(candidate_selection_policy_identity(cfg))
    if universe.candidate_selection_policy_epoch != expected_epoch:
        return "candidate_selection_policy_epoch_mismatch"
    return None


__all__ = [
    "LEGACY_CANDIDATE_POLICY",
    "TARGET_CANDIDATE_POLICY",
    "candidate_selection_semantic_manifest",
    "candidate_selection_policy_identity",
    "learning_candidate_selection_overlap_block_reason",
    "candidate_selection_overlap_block_reason",
    "candidate_selection_authority_block_reason",
    "recover_candidate_selection_authority_transition",
    "stage_candidate_selection_authority_transition",
]
