"""Structured Post-Trade Case Study Builder.

Converts closed-position attribution rows into structured case studies with
deterministic quality metrics. When repeated failure patterns emerge, generates
guard candidates automatically.

No LLM. Deterministic. Read-only except for case study / guard candidate creation.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from aletheia.analytics.trade_attribution import build_trade_attribution_rows, factor_key
from aletheia.knowledge.manager import create_trade_case_study, create_guard_candidate
from aletheia.knowledge.frontmatter import render_frontmatter

logger = logging.getLogger("aletheia.analytics.trade_case_builder")

# Thresholds for guard candidate generation
_MIN_LOSSES_FOR_GUARD = 3
_MIN_LOSS_RATE_FOR_GUARD = 0.7  # 70% losing trades with same factor pattern


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
    recent_rows = [r for r in rows if _opened_after(r, cutoff)]
    if not recent_rows:
        return []

    existing_ids = _get_existing_case_position_ids() if skip_existing else set()
    created = []

    for row in recent_rows:
        pos_id = row.get("position_id")
        if not pos_id or pos_id in existing_ids:
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
            created.append({
                "case_study_id": item.id,
                "position_id": pos_id,
                "outcome": case["outcome"],
                "realized_pnl_pct": case["realized_pnl_pct"],
            })
        except Exception as e:
            logger.warning("Failed to create case study for position %s: %s", pos_id, e)

    return created


def detect_guard_candidates(min_losses: int | None = None) -> list[dict]:
    """Detect repeated failure patterns and generate guard candidates.

    Groups closed positions by factor_key, finds patterns with high loss rates,
    and creates guard candidates for patterns not already covered by active guards.
    """
    min_losses = min_losses or _MIN_LOSSES_FOR_GUARD
    rows = build_trade_attribution_rows(limit=500)
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

    has_full_chain = all([
        row.get("proposal_id"),
        row.get("opportunity_id"),
        row.get("strategy_id"),
    ])

    return {
        "outcome": outcome,
        "realized_pnl_pct": round(realized_pnl_pct, 4),
        "holding_hours": round(holding_hours, 1) if holding_hours else None,
        "factor_key": factor_key(row),
        "has_full_chain": has_full_chain,
        "entry_price": row.get("entry_price"),
        "exit_price": row.get("exit_price"),
        "close_reason": row.get("close_reason"),
        "late_entry_penalty": row.get("late_entry_penalty"),
        "crowding_risk": row.get("crowding_risk"),
        "risk_flags": row.get("risk_flags", []),
        "confidence": row.get("confidence"),
        "scanner_family": row.get("scanner_family"),
        "universe_rank": row.get("universe_rank"),
    }


def _render_case_body(case: dict) -> str:
    """Render structured case study content as markdown."""
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
        "applies_to": {"asset_class": ["crypto"]},
        "predicates": predicates,
        "evidence_refs": [r.get("position_id") for r in losses[:5] if r.get("position_id")],
        "tags": ["auto-generated", "factor-pattern", scanner],
    }

    body = (
        f"## Evidence\n\n"
        f"Pattern `{fk}` produced {len(losses)} losses out of {len(factor_rows)} trades "
        f"(loss rate: {loss_rate:.0%}).\n"
        f"Average loss: {avg_loss:.2f}%.\n\n"
        f"## Status\n\n"
        f"Auto-generated guard candidate. Requires human review before promotion to active.\n"
    )

    try:
        item = create_guard_candidate(
            title=title,
            content=render_frontmatter(metadata, body),
            metadata=metadata,
        )
        return {
            "guard_candidate_id": item.id,
            "guard_id": guard_id,
            "factor_key": fk,
            "loss_count": len(losses),
            "total_count": len(factor_rows),
            "loss_rate": round(loss_rate, 3),
        }
    except Exception as e:
        logger.warning("Failed to create guard candidate for %s: %s", fk, e)
        return None


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
