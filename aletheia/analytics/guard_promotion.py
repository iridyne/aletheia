"""Guard Promotion Pipeline.

Manages the lifecycle of trading guards:
  candidate → active (when evidence threshold met)
  active → deprecated (when ineffective)

Deterministic. No LLM. Requires explicit config opt-in (auto_promote_enabled).
"""
from __future__ import annotations

import json
import logging

from aletheia.config.loader import load_config, get_data_dir
from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter
from aletheia.knowledge.guards import load_trading_guards

logger = logging.getLogger("aletheia.analytics.guard_promotion")


def run_promotion_check() -> dict:
    """Check candidate guards for promotion eligibility.

    Returns summary of promotions and deprecations applied.
    Requires config.guard.auto_promote_enabled = True.
    """
    cfg = load_config()
    guard_cfg = getattr(cfg, "guard", None)
    if not guard_cfg or not guard_cfg.auto_promote_enabled:
        return {"skipped": True, "reason": "auto_promote_enabled is False"}

    min_trades = guard_cfg.min_matching_trades
    candidates = load_trading_guards(status="candidate")
    if not candidates:
        return {"promotions": 0, "deprecations": 0, "candidates_checked": 0}

    from aletheia.analytics.guard_effectiveness import _rows_matching_guard, _clean_rows
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows

    # #159: this job MUTATES live guard state, so only learn loss-rates from genuine
    # post-clean-window trade outcomes — synthetic/administrative closes (reconciler
    # sweeps, cleanups) and pre-floor dirty data must not drive promotion/deprecation.
    rows = _clean_rows(build_trade_attribution_rows(limit=500))
    promotions = 0
    deprecations = 0

    for guard in candidates:
        matched = _rows_matching_guard(guard, rows)
        if len(matched) < min_trades:
            continue

        losses = [r for r in matched if float(r.get("realized_pnl", 0)) < 0]
        loss_rate = len(losses) / len(matched) if matched else 0

        if loss_rate >= 0.6:
            if _promote_guard_to_active(guard.guard_id, loss_rate):
                promotions += 1
                logger.info("Promoted guard %s to active (loss_rate=%.0f%%)",
                            guard.guard_id, loss_rate * 100)
        elif loss_rate < 0.3 and len(matched) >= min_trades * 2:
            if _update_guard_status(guard.guard_id, "deprecated"):
                deprecations += 1
                logger.info("Deprecated guard %s (loss_rate=%.0f%%, not effective)",
                            guard.guard_id, loss_rate * 100)

    return {
        "promotions": promotions,
        "deprecations": deprecations,
        "candidates_checked": len(candidates),
    }


def promote_guard_candidate_to_active(
    guard_id: str,
    *,
    actor_type: str = "system",
    actor_id: str = "guard_promotion",
    loss_rate: float | None = None,
    reason: str = "evidence_threshold",
) -> bool:
    """Promote a candidate guard to active without deleting the candidate record.

    score_penalty=10.0 / enforcement=hard_block are written so ranker
    evaluate_guards actually penalizes matching opportunities (断口G fix).
    """
    guards_dir = get_data_dir() / "knowledge" / "guards"
    candidates_dir = get_data_dir() / "knowledge" / "guard_candidates"
    active_existing = any(g.guard_id == guard_id for g in load_trading_guards(status="active"))

    for search_dir in [guards_dir, candidates_dir]:
        if not search_dir.exists():
            continue
        for path in search_dir.glob("*.md"):
            try:
                meta, body = parse_frontmatter(path)
                if not meta or meta.get("guard_id") != guard_id:
                    continue

                source_meta = dict(meta)
                active_meta = dict(meta)
                active_meta["status"] = "active"
                # Ensure penalty values are set so guard actually affects scoring
                if not active_meta.get("score_penalty"):
                    active_meta["score_penalty"] = 10.0
                if not active_meta.get("enforcement"):
                    active_meta["enforcement"] = "blocking"
                if not active_meta.get("block_candidate"):
                    active_meta["block_candidate"] = True

                if search_dir == candidates_dir:
                    if not active_existing:
                        from aletheia.knowledge.manager import create_trading_guard

                        create_trading_guard(
                            title=active_meta.get("title") or path.stem,
                            content=body,
                            metadata=active_meta,
                        )
                    source_meta["status"] = "deprecated"
                    source_meta["promoted_to"] = guard_id
                    source_meta["promotion_reason"] = reason
                    path.write_text(render_frontmatter(source_meta, body), encoding="utf-8")
                    _update_candidate_row_status(path)
                else:
                    path.write_text(render_frontmatter(active_meta, body), encoding="utf-8")
                _record_guard_promotion_event(
                    guard_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    loss_rate=loss_rate,
                    reason=reason,
                    source_path=str(path),
                )
                return True
            except Exception as e:
                logger.warning("Failed to promote guard %s: %s", guard_id, e)
                continue

    return False


def _promote_guard_to_active(guard_id: str, loss_rate: float) -> bool:
    """Backward-compatible auto-promotion entry point."""
    return promote_guard_candidate_to_active(guard_id, loss_rate=loss_rate)


def _update_candidate_row_status(path) -> None:
    """Best-effort DB downgrade for the source candidate row."""
    try:
        from aletheia.storage import repositories as repo

        rows = repo._select_all(
            "knowledge_items",
            where="file_path = ? AND item_type = ?",
            params=(str(path), "guard_candidate"),
            limit=1,
        )
        for row in rows:
            repo.update_knowledge_item(row["id"], status="deprecated", confidence="deprecated")
    except Exception:
        logger.debug("Failed to update promoted guard candidate DB row", exc_info=True)


def _record_guard_promotion_event(
    guard_id: str,
    *,
    actor_type: str,
    actor_id: str,
    loss_rate: float | None,
    reason: str,
    source_path: str,
) -> None:
    try:
        from aletheia.storage.event_store import get_event_store
        from aletheia.storage.models import PSEvent

        get_event_store().append(PSEvent(
            event_type="TRADING_GUARD_PROMOTED",
            aggregate_type="trading_guard",
            aggregate_id=guard_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload_json=json.dumps({
                "guard_id": guard_id,
                "loss_rate": loss_rate,
                "reason": reason,
                "source_path": source_path,
            }, sort_keys=True),
        ))
    except Exception:
        logger.debug("Failed to record guard promotion event", exc_info=True)


def _update_guard_status(guard_id: str, new_status: str) -> bool:
    """Update a guard's status in its markdown file."""
    guards_dir = get_data_dir() / "knowledge" / "guards"
    candidates_dir = get_data_dir() / "knowledge" / "guard_candidates"

    for search_dir in [guards_dir, candidates_dir]:
        if not search_dir.exists():
            continue
        for path in search_dir.glob("*.md"):
            try:
                meta, body = parse_frontmatter(path)
                if not meta or meta.get("guard_id") != guard_id:
                    continue

                meta["status"] = new_status
                content = render_frontmatter(meta, body)
                path.write_text(content, encoding="utf-8")

                if new_status == "active" and search_dir == candidates_dir:
                    target = guards_dir / path.name
                    guards_dir.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    path.unlink()

                return True
            except Exception as e:
                logger.warning("Failed to update guard %s: %s", guard_id, e)
                continue

    return False
