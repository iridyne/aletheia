"""Structured Post-Trade Case Study Builder.

Converts closed-position attribution rows into structured case studies with
deterministic quality metrics. When repeated failure patterns emerge, generates
guard candidates automatically.

No LLM. Deterministic. Read-only except for case study / guard candidate creation.
"""
from __future__ import annotations

import logging
import json
from collections import defaultdict
from datetime import datetime, timezone

from aletheia.analytics.trade_attribution import build_trade_attribution_rows, factor_key
from aletheia.knowledge.manager import create_trade_case_study, create_guard_candidate
from aletheia.knowledge.frontmatter import render_frontmatter

logger = logging.getLogger("aletheia.analytics.trade_case_builder")

# Thresholds for guard candidate generation
_MIN_LOSSES_FOR_GUARD = 3
_MIN_LOSS_RATE_FOR_GUARD = 0.7  # 70% losing trades with same factor pattern

# Close reasons that are administrative/synthetic — the position was NOT exited by a
# genuine strategy/risk decision but wiped by reconciliation bookkeeping (local-open
# orphan vanished from the exchange; historical demo-validation cleanup). Their
# realized_pnl is a fabricated/approximate fallback (exit forced to last-known or
# entry price), so feeding them as trade_case_study / guard evidence teaches the
# learning loop a non-outcome. NOTE: a reconciler close that recovered a REAL exchange
# TPSL fill keeps its genuine close_reason (stop_loss/take_profit) and is NOT synthetic
# — we filter on the reason, not on whether the close went through the reconciler.
# (junk-data sweep B3, 2026-06-28 — see LP-S08.)
# Single source of truth in analytics/close_reasons.py (2026-07-01 consolidation);
# re-exported here for backwards compatibility with existing call sites/tests.
from aletheia.analytics.close_reasons import SYNTHETIC_CLOSE_REASONS as _SYNTHETIC_CLOSE_REASONS


def _is_synthetic_closure(row: dict) -> bool:
    """True if the row's close_reason is an administrative/synthetic closure that must
    not enter the learning corpus (case studies / guard candidates)."""
    return str(row.get("close_reason") or "").strip() in _SYNTHETIC_CLOSE_REASONS


def build_case_studies(
    since_hours: int = 168,
    skip_existing: bool = True,
) -> list[dict]:
    """Build structured case studies from recently closed positions.

    Returns list of created case study summaries (not full KnowledgeItems).
    """
    rows = build_trade_attribution_rows(limit=500)
    if not rows:
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600
    # #16: window on CLOSE time, not open time — case studies are built from recently
    # *closed* trades (see docstring), so a position opened before the window but
    # closed within it must still be captured; otherwise long-held trades never get one.
    recent_rows = [r for r in rows if _closed_after(r, cutoff)]
    if not recent_rows:
        return []

    existing_ids = _get_existing_case_position_ids() if skip_existing else set()
    created = []
    skipped_synthetic = 0

    for row in recent_rows:
        pos_id = row.get("position_id")
        if not pos_id or pos_id in existing_ids:
            continue

        if _is_synthetic_closure(row):
            # Administrative closure (orphan reconciliation / demo cleanup) — the exit
            # price is a fabricated fallback, not a genuine trade outcome. Excluded from
            # the learning corpus so case studies / lessons stay attributable (LP-S08).
            skipped_synthetic += 1
            continue

        case = _build_case_from_row(row)
        if case is None:
            continue

        try:
            title = f"Case: {row['symbol']} {row['direction']} ({row.get('close_reason', 'closed')})"
            content = _render_case_body(case)
            metadata = {
                "type": "Trade Case Study",
                "position_id": pos_id,
                "symbol": row["symbol"],
                "direction": row["direction"],
                "outcome": case["outcome"],
                "realized_pnl_pct": case["realized_pnl_pct"],
                "holding_hours": case["holding_hours"],
                "factor_key": case["factor_key"],
                "close_reason": row.get("close_reason"),
                "scanner_family": row.get("scanner_family"),
                "market_type": case.get("market_type"),
                "tags": _build_tags(case),
            }
            confidence = "high_confidence" if case["has_full_chain"] else "medium_confidence"
            item = create_trade_case_study(
                title=title,
                content=content,
                metadata=metadata,
                strategy_id=row.get("strategy_id"),
                confidence=confidence,
                tags=metadata["tags"],
            )
            if item is None:
                # Duplicate (INFO log) or quality-gate failure (WARNING log in manager).
                # Quality-gate failures are distinguishable in logs; watch for
                # repeated warnings as they indicate a content-generation regression.
                continue
            created.append({
                "case_study_id": item.id,
                "position_id": pos_id,
                "outcome": case["outcome"],
                "realized_pnl_pct": case["realized_pnl_pct"],
            })
        except Exception as e:
            logger.warning("Failed to create case study for position %s: %s", pos_id, e, exc_info=True)

    if skipped_synthetic:
        logger.info(
            "build_case_studies: skipped %d synthetic-closure rows "
            "(reconciled_gone/demo_validation_cleanup) — not genuine trade outcomes",
            skipped_synthetic,
        )

    return created


def detect_guard_candidates(min_losses: int | None = None) -> list[dict]:
    """Detect repeated failure patterns and generate guard candidates.

    Groups closed positions by factor_key, finds patterns with high loss rates,
    and creates guard candidates for patterns not already covered by active guards.
    """
    if min_losses is None:
        min_losses = _MIN_LOSSES_FOR_GUARD
    rows = build_trade_attribution_rows(limit=500)
    if not rows:
        return []

    # Exclude administrative/synthetic closures: their realized_pnl is a fabricated
    # fallback, so a guard built from them would penalise a factor pattern on the basis
    # of a non-loss. Filter on close_reason VALUE (LP-S08).
    rows = [r for r in rows if not _is_synthetic_closure(r)]
    if not rows:
        return []

    by_factor: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        fk = factor_key(row)
        by_factor[fk].append(row)

    candidates_created = []
    existing_guards = _get_existing_guard_ids()

    for fk, factor_rows in by_factor.items():
        if len(factor_rows) < min_losses:
            continue

        losses = [r for r in factor_rows if float(r.get("realized_pnl", 0)) < 0]
        loss_rate = len(losses) / len(factor_rows)

        if loss_rate < _MIN_LOSS_RATE_FOR_GUARD:
            continue

        guard_id = f"auto_factor_loss_{fk.replace(':', '_').replace('/', '_')}"
        if guard_id in existing_guards:
            continue

        candidate = _create_factor_guard_candidate(fk, factor_rows, losses, loss_rate)
        if candidate:
            candidates_created.append(candidate)

    return candidates_created


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _build_case_from_row(row: dict) -> dict | None:
    """Extract structured case metrics from an attribution row."""
    realized_pnl_pct = row.get("realized_pnl_pct", 0)
    holding_hours = row.get("holding_hours")

    if realized_pnl_pct > 0.5:
        outcome = "win"
    elif realized_pnl_pct < -0.5:
        outcome = "loss"
    else:
        outcome = "scratch"

    # The strategy rules engine was removed; strategy_id is no longer a reliable
    # proof of attribution. A useful closed-trade case needs the execution proposal
    # and the source opportunity, because those carry symbol/direction/scanner lineage.
    has_full_chain = all([
        row.get("proposal_id"),
        row.get("opportunity_id"),
    ])

    return {
        "outcome": outcome,
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "holding_hours": round(holding_hours, 1) if holding_hours else None,
        "factor_key": factor_key(row),
        "has_full_chain": has_full_chain,
        # Spot vs perp tag so the learning loop can separate e.g. "momentum perp" from
        # "momentum spot" now that the observer emits both (Round 2). NOTE: the case
        # metric realized_pnl_pct is price-move% (realized_pnl = (exit-entry)*qty, NOT
        # ×leverage) — already leverage-NEUTRAL, so no leverage_adjusted_return is added
        # (pnl/margin would be leverage-AMPLIFIED and reintroduce the "more leverage =
        # better" bias deferred #8 warned about).
        "market_type": row.get("market_type"),
        "entry_price": row.get("entry_price"),
        "exit_price": row.get("exit_price"),
        "close_reason": row.get("close_reason"),
        "late_entry_penalty": row.get("late_entry_penalty"),
        "crowding_risk": row.get("crowding_risk"),
        "risk_flags": row.get("risk_flags", []),
        "confidence": row.get("confidence"),
        "scanner_family": row.get("scanner_family"),
        "universe_rank": row.get("universe_rank"),
        "decision_context_status": row.get("decision_context_status") or "not recorded",
        "pi_decision_reasoning": row.get("pi_decision_reasoning") or "not recorded",
        "pi_decision_bear_case": row.get("pi_decision_bear_case") or "not recorded",
        "pi_decision_assumptions": row.get("pi_decision_assumptions") or "not recorded",
        "proposal_agent_rationale": row.get("proposal_agent_rationale") or "not recorded",
        "decision_evidence_manifest": row.get("decision_evidence_manifest") or "not recorded",
        "decision_learning_manifest": row.get("decision_learning_manifest") or "not recorded",
    }


def _render_case_body(case: dict) -> str:
    """Render structured case study content as markdown."""
    def render(value) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    lines = [
        "## Metrics",
        f"- Outcome: **{case['outcome']}**",
        f"- PnL: {case['realized_pnl_pct']:.2f}%",
        f"- Hold: {case['holding_hours'] or 'unknown'}h",
        f"- Close reason: {case['close_reason'] or 'unknown'}",
        "",
        "## Entry Context",
        f"- Factor key: `{case['factor_key']}`",
        f"- Confidence: {case['confidence']}",
        f"- Scanner: {case['scanner_family']}",
        f"- Universe rank: {case['universe_rank']}",
        f"- Late entry penalty: {case['late_entry_penalty']}",
        f"- Crowding risk: {case['crowding_risk']}",
        f"- Risk flags: {case['risk_flags']}",
        "",
        "## Price",
        f"- Entry: {case['entry_price']}",
        f"- Exit: {case['exit_price']}",
        "",
        "## Persisted Decision Context",
        f"- Context status: {render(case['decision_context_status'])}",
        f"- PiDecision reasoning: {render(case['pi_decision_reasoning'])}",
        f"- Bear case: {render(case['pi_decision_bear_case'])}",
        f"- Assumptions: {render(case['pi_decision_assumptions'])}",
        f"- Proposal agent rationale: {render(case['proposal_agent_rationale'])}",
        f"- Evidence manifest: {render(case['decision_evidence_manifest'])}",
        f"- Learning manifest: {render(case['decision_learning_manifest'])}",
    ]
    return "\n".join(lines)


def _build_tags(case: dict) -> list[str]:
    """Generate tags for case study searchability."""
    tags = [case["outcome"]]
    if case.get("scanner_family"):
        tags.append(case["scanner_family"])
    if case.get("close_reason"):
        tags.append(case["close_reason"])
    if case.get("late_entry_penalty") and case["late_entry_penalty"] >= 20:
        tags.append("late-entry")
    if "wide_spread" in (case.get("risk_flags") or []):
        tags.append("wide-spread")
    return tags


def _create_factor_guard_candidate(
    fk: str, factor_rows: list[dict], losses: list[dict], loss_rate: float
) -> dict | None:
    """Create a guard candidate from a repeated-loss factor pattern."""
    parts = fk.split(":")
    if len(parts) < 5:
        return None

    scanner, symbol, direction, conf_bucket, late_bucket = parts[:5]
    avg_loss = sum(float(r.get("realized_pnl_pct", 0)) for r in losses) / len(losses) if losses else 0

    guard_id = f"auto_factor_loss_{fk.replace(':', '_').replace('/', '_')}"
    title = f"Auto: Repeated loss on {scanner}:{symbol}:{direction}"

    predicates = []
    if scanner != "unknown":
        predicates.append({"field": "scanner_family", "op": "eq", "value": scanner})
    if late_bucket in ("late", "very_late"):
        predicates.append({"field": "late_entry_penalty", "op": "gte", "value": 10})

    if not predicates:
        return None

    applies_to: dict = {"asset_class": ["crypto"]}
    if scanner != "unknown":
        applies_to["scanner_family"] = [scanner]
    if symbol != "unknown":
        applies_to["symbol"] = [symbol]
    if direction != "unknown":
        applies_to["direction"] = [direction]

    metadata = {
        "type": "Trading Guard",
        "guard_id": guard_id,
        "title": title,
        "description": f"Auto-generated from {len(losses)}/{len(factor_rows)} losing trades "
                       f"(loss rate {loss_rate:.0%}, avg loss {avg_loss:.2f}%).",
        "status": "candidate",
        "scope": "paper_entry",
        "enforcement": "scoring",
        "severity": "warning",
        "score_penalty": min(25, int(loss_rate * 30)),
        "block_candidate": False,
        # Learned/auto guard: shadow-only, observe-only. It cannot auto-promote to a
        # block; enforcement is suppressed until an experiment grants authority.
        "auto_generated": True,
        "shadow_only": True,
        "applies_to": applies_to,
        "predicates": predicates,
        "evidence_refs": [r.get("position_id") for r in losses[:5] if r.get("position_id")],
        "tags": ["auto-generated", "factor-pattern", "shadow-only", scanner],
    }

    body = (
        f"## Evidence\n\n"
        f"Pattern `{fk}` produced {len(losses)} losses out of {len(factor_rows)} trades "
        f"(loss rate: {loss_rate:.0%}).\n"
        f"Average loss: {avg_loss:.2f}%.\n\n"
        f"## Status\n\n"
        f"Auto-generated **shadow-only** guard candidate. It is observed for learning "
        f"but does NOT block or penalize trades. It cannot auto-promote to an active "
        f"block; paper-block authority requires explicit owner action or a positive "
        f"`ranker_learned_guard` experiment verdict (AT-01 + AT-02).\n"
    )

    try:
        item = create_guard_candidate(
            title=title,
            content=render_frontmatter(metadata, body),
            metadata=metadata,
        )
        if item is None:
            return None  # quality gate or duplicate
        return {
            "guard_candidate_id": item.id,
            "guard_id": guard_id,
            "factor_key": fk,
            "loss_count": len(losses),
            "total_count": len(factor_rows),
            "loss_rate": round(loss_rate, 3),
        }
    except Exception as e:
        logger.warning("Failed to create guard candidate for %s: %s", fk, e, exc_info=True)
        return None


def _closed_after(row: dict, cutoff_ts: float) -> bool:
    """#16: True if the position CLOSED at/after the cutoff (case studies window on
    close time, not open time — long-held trades closed within the window still count)."""
    closed = row.get("closed_at")
    if not closed:
        return False
    try:
        dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
        return dt.timestamp() >= cutoff_ts
    except (ValueError, TypeError):
        return False


def _opened_after(row: dict, cutoff_ts: float) -> bool:
    """Check if position was opened after cutoff timestamp."""
    opened = row.get("opened_at")
    if not opened:
        return False
    try:
        dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        return dt.timestamp() >= cutoff_ts
    except (ValueError, TypeError):
        return False


def _get_existing_case_position_ids() -> set[str]:
    """Get position IDs that already have case studies (from file frontmatter)."""
    try:
        from aletheia.config.loader import get_data_dir
        from aletheia.knowledge.frontmatter import parse_frontmatter

        case_dir = get_data_dir() / "knowledge" / "case_studies"
        if not case_dir.exists():
            return set()

        ids = set()
        for path in case_dir.glob("*.md"):
            try:
                meta, _ = parse_frontmatter(path)
                pid = meta.get("position_id") if meta else None
                if pid:
                    ids.add(pid)
            except Exception:
                continue
        return ids
    except Exception:
        return set()


def _get_existing_guard_ids() -> set[str]:
    """Get guard IDs from existing guard files."""
    try:
        from aletheia.knowledge.guards import load_trading_guards
        active = load_trading_guards(status="active")
        candidates = load_trading_guards(status="candidate")
        return {g.guard_id for g in active + candidates}
    except Exception:
        return set()
