"""Guard Promotion Pipeline.

Manages the lifecycle of trading guards:
  candidate → active (when evidence threshold met)
  active → deprecated (when ineffective)

Deterministic. No LLM. Requires explicit config opt-in (auto_promote_enabled).
"""
from __future__ import annotations

import logging
from pathlib import Path

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

    from aletheia.analytics.guard_effectiveness import _rows_matching_guard
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows

    rows = build_trade_attribution_rows(limit=500)
    promotions = 0
    deprecations = 0

    for guard in candidates:
        matched = _rows_matching_guard(guard, rows)
        if len(matched) < min_trades:
            continue

        losses = [r for r in matched if float(r.get("realized_pnl", 0)) < 0]
        loss_rate = len(losses) / len(matched) if matched else 0

        if loss_rate >= 0.6:
            if _update_guard_status(guard.guard_id, "active"):
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
