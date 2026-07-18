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
from aletheia.knowledge.guards import load_trading_guards, guard_origin

logger = logging.getLogger("aletheia.analytics.guard_promotion")


def _notify_ranker_guard_change() -> None:
    """Wake Ranker after a committed guard-set mutation; never fail the mutation."""
    try:
        from aletheia.agent_runtime.scheduler import notify_paper_trader

        notify_paper_trader(source="guard_set_changed")
    except Exception:
        logger.debug("Failed to notify Ranker after guard-set change", exc_info=True)


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

    # Automatic promotion is fail-closed when the shared Learning reviewer has
    # stopped or accumulated a stale/oversized backlog. Existing active guards
    # remain unchanged; the owner-controlled manual promotion route is separate.
    from aletheia.learning.control import promotion_refreeze_reason
    refreeze_reason = promotion_refreeze_reason()
    if refreeze_reason is not None:
        return {"skipped": True, "refrozen": True, "reason": refreeze_reason}

    from aletheia.analytics.guard_effectiveness import _rows_matching_guard, _clean_rows
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows
    from aletheia.knowledge.guards import learned_guard_block_authorized

    # #159: this job MUTATES live guard state, so only learn loss-rates from genuine
    # post-clean-window trade outcomes — synthetic/administrative closes (reconciler
    # sweeps, cleanups) and pre-floor dirty data must not drive promotion/deprecation.
    rows = _clean_rows(build_trade_attribution_rows(limit=500))
    promotions = 0
    deprecations = 0
    skipped_non_learned = 0
    skipped_unauthorized = 0

    for guard in candidates:
        # Fix 6 (P0 2026-07-12): safety-type seed/manual guards are owner-controlled
        # and must NEVER be processed by the automatic learned-guard job. Only learned
        # (auto-generated) guards flow through here.
        if guard_origin(guard) != "learned":
            skipped_non_learned += 1
            continue
        # Fix 3: shadow-only learned guards are observe-only. The automatic job never
        # promotes them and never strips their shadow-only marker.
        if guard.shadow_only or "shadow-only" in set(guard.tags or []):
            skipped_unauthorized += 1
            continue

        matched = _rows_matching_guard(guard, rows)
        if len(matched) < min_trades:
            continue

        losses = [r for r in matched if float(r.get("realized_pnl", 0)) < 0]
        loss_rate = len(losses) / len(matched) if matched else 0

        # Auto-deprecation of an ineffective learned guard is safe cleanup.
        if loss_rate < 0.3 and len(matched) >= min_trades * 2:
            if _update_guard_status(guard.guard_id, "deprecated"):
                deprecations += 1
                logger.info("Deprecated guard %s (loss_rate=%.0f%%, not effective)",
                            guard.guard_id, loss_rate * 100)
            continue

        # Fixes 4/5: historical loss-rate correlation is NOT causal block authority.
        # A learned guard is promoted to an active block ONLY when the
        # ranker_learned_guard experiment authorizes it (AT-01 verdict + AT-02 path
        # authority). Fail-closed on master → nothing auto-promotes into a blocker.
        if loss_rate >= 0.6:
            if learned_guard_block_authorized(guard) and _promote_guard_to_active(guard.guard_id, loss_rate):
                promotions += 1
                logger.info("Promoted guard %s to active (loss_rate=%.0f%%, experiment-authorized)",
                            guard.guard_id, loss_rate * 100)
            else:
                skipped_unauthorized += 1
                logger.info("guard_promotion: %s meets loss threshold but lacks experiment "
                            "authority — kept observe-only (fail-closed)", guard.guard_id)

    return {
        "promotions": promotions,
        "deprecations": deprecations,
        "candidates_checked": len(candidates),
        "skipped_non_learned": skipped_non_learned,
        "skipped_unauthorized": skipped_unauthorized,
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
                # Fix 3 (P0 2026-07-12): a shadow-only candidate stays observe-only even
                # when promoted — never strip shadow_only / the "shadow-only" tag and
                # never force it into a block. Only non-shadow (owner/manual) guards get
                # the blocking defaults that make evaluate_guards enforce.
                is_shadow = bool(meta.get("shadow_only")) or "shadow-only" in set(meta.get("tags") or [])
                if not is_shadow:
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
                _notify_ranker_guard_change()
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

                _notify_ranker_guard_change()
                return True
            except Exception as e:
                logger.warning("Failed to update guard %s: %s", guard_id, e)
                continue

    return False


# ---------------------------------------------------------------------------
# Fix 7: audit + reversible shadow-downgrade of learned active guards
# ---------------------------------------------------------------------------

def audit_auto_guards() -> list[dict]:
    """READ-ONLY audit of learned (auto-generated) guards that are active.

    Reports which learned guards the old auto-promotion chain turned into blockers
    or scorers. Mutates nothing. Use ``downgrade_learned_guards_to_shadow`` to fix.
    """
    out: list[dict] = []
    for guard in load_trading_guards(status="active"):
        if guard_origin(guard) != "learned":
            continue
        out.append({
            "guard_id": guard.guard_id,
            "title": guard.title,
            "enforcement": guard.enforcement,
            "block_candidate": guard.block_candidate,
            "score_penalty": guard.score_penalty,
            "shadow_only": guard.shadow_only,
            "tags": list(guard.tags or []),
            # A learned guard is a live behavior-changer only if it still blocks or
            # scores AND is not already shadow-only.
            "still_enforcing": (guard.block_candidate or guard.score_penalty > 0) and not guard.shadow_only,
        })
    return out


def _record_guard_downgrade_event(guard_id: str, before: dict) -> None:
    try:
        from aletheia.storage.event_store import get_event_store
        from aletheia.storage.models import PSEvent

        get_event_store().append(PSEvent(
            event_type="GUARD_DOWNGRADED_TO_SHADOW",
            aggregate_type="trading_guard",
            aggregate_id=guard_id,
            actor_type="human",
            actor_id="guard_shadow_downgrade",
            payload_json=json.dumps({"guard_id": guard_id, "before": before}, sort_keys=True),
        ))
    except Exception:
        logger.debug("Failed to record guard downgrade event", exc_info=True)


def downgrade_learned_guards_to_shadow(*, dry_run: bool = True) -> dict:
    """Downgrade every learned active guard to shadow-only (observe-only).

    Reversible and auditable: original enforcement/block/penalty are stored in
    ``pre_shadow_*`` frontmatter keys, an event is recorded, and the file is kept
    (never deleted). Owner-controlled seed/manual guards are never touched.

    dry_run=True (default) reports what would change without writing.
    """
    guards_dir = get_data_dir() / "knowledge" / "guards"
    changed: list[dict] = []
    scanned = 0
    if guards_dir.exists():
        for path in sorted(guards_dir.glob("*.md")):
            try:
                meta, body = parse_frontmatter(path)
                if not meta or meta.get("status") != "active":
                    continue
                if guard_origin(meta) != "learned":
                    continue
                scanned += 1
                # Fully observe-only = shadow + advisory + no block + no penalty. Zeroing
                # the penalty too means even the pre-restart gateway (which applies
                # score_penalty regardless of shadow_only) stops changing behavior; the
                # new evaluate_guards seam enforces the same thing post-restart.
                fully_observe = (
                    bool(meta.get("shadow_only"))
                    and not bool(meta.get("block_candidate"))
                    and str(meta.get("enforcement")) == "advisory"
                    and float(meta.get("score_penalty") or 0) == 0
                )
                if fully_observe:
                    continue
                before = {
                    "enforcement": meta.get("enforcement"),
                    "block_candidate": meta.get("block_candidate"),
                    "score_penalty": meta.get("score_penalty"),
                    "shadow_only": meta.get("shadow_only"),
                }
                changed.append({"guard_id": meta.get("guard_id"), "path": str(path), "before": before})
                if dry_run:
                    continue
                new_meta = dict(meta)
                # Preserve originals once (idempotent) for rollback.
                new_meta.setdefault("pre_shadow_enforcement", meta.get("enforcement"))
                new_meta.setdefault("pre_shadow_block_candidate", meta.get("block_candidate"))
                new_meta.setdefault("pre_shadow_score_penalty", meta.get("score_penalty"))
                new_meta["shadow_only"] = True
                new_meta["enforcement"] = "advisory"
                new_meta["block_candidate"] = False
                new_meta["score_penalty"] = 0
                tags = list(new_meta.get("tags") or [])
                if "shadow-only" not in tags:
                    tags.append("shadow-only")
                new_meta["tags"] = tags
                path.write_text(render_frontmatter(new_meta, body), encoding="utf-8")
                _record_guard_downgrade_event(str(meta.get("guard_id")), before)
            except Exception as e:
                logger.warning("Failed to downgrade guard at %s: %s", path, e)
                continue

    if changed and not dry_run:
        _notify_ranker_guard_change()

    return {
        "dry_run": dry_run,
        "scanned_learned_active": scanned,
        ("would_downgrade" if dry_run else "downgraded"): len(changed),
        "guards": changed,
    }


def _record_guard_candidate_deprecated_event(guard_id: str, path: str) -> None:
    try:
        from aletheia.storage.event_store import get_event_store
        from aletheia.storage.models import PSEvent

        get_event_store().append(PSEvent(
            event_type="GUARD_CANDIDATE_DEPRECATED",
            aggregate_type="trading_guard",
            aggregate_id=guard_id,
            actor_type="human",
            actor_id="guard_prune_candidates",
            payload_json=json.dumps(
                {"guard_id": guard_id, "path": path, "reason": "stale_shadow_candidate_cleanup"},
                sort_keys=True,
            ),
        ))
    except Exception:
        logger.debug("Failed to record guard candidate deprecation event", exc_info=True)


def prune_learned_guard_candidates(*, dry_run: bool = True) -> dict:
    """Deprecate stale LEARNED guard candidates (soft, reversible, audited).

    Learned candidates are observe-only and can never auto-promote, so a large
    backlog is pure noise. This marks each ``status=deprecated`` (KB rule: never
    hard-delete — the file and DB row are kept for audit) and records an event.
    Owner-authored (manual) candidates awaiting review are NEVER touched. To undo,
    flip a file's status back to ``candidate``.

    dry_run=True (default) reports what would change without writing.
    """
    candidates_dir = get_data_dir() / "knowledge" / "guard_candidates"
    changed: list[dict] = []
    scanned = 0
    if candidates_dir.exists():
        for path in sorted(candidates_dir.glob("*.md")):
            try:
                meta, body = parse_frontmatter(path)
                if not meta or meta.get("status") != "candidate":
                    continue
                # Only learned candidates — never deprecate a manual candidate the
                # owner may be reviewing.
                if guard_origin(meta) != "learned":
                    continue
                scanned += 1
                # Old pre-fix candidates (L-Bug-D) lack guard_id — fall back to the
                # file stem so the audit trail still identifies each one.
                gid = str(meta.get("guard_id") or path.stem)
                changed.append({"guard_id": gid, "path": str(path)})
                if dry_run:
                    continue
                new_meta = dict(meta)
                new_meta["status"] = "deprecated"
                new_meta.setdefault("deprecated_reason", "stale_shadow_candidate_cleanup")
                path.write_text(render_frontmatter(new_meta, body), encoding="utf-8")
                _update_candidate_row_status(path)
                _record_guard_candidate_deprecated_event(gid, str(path))
            except Exception as e:
                logger.warning("Failed to prune guard candidate at %s: %s", path, e)
                continue

    return {
        "dry_run": dry_run,
        "scanned_learned_candidates": scanned,
        ("would_deprecate" if dry_run else "deprecated"): len(changed),
        "guard_ids": [c["guard_id"] for c in changed],
    }
