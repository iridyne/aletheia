"""Committee workbench overview (GET /api/committee/reports).

Read-only aggregation over the append-only ``committee_reports`` table that
powers the standalone AI Jury page: recent evaluations (newest first, parsed
into structured opinions/evidence/risks) plus a window summary (direction
mix, average conviction, average explainability score, blocked/fallback
counts, model usage).

Boundaries:
- Pure read path. Performs no writes and never touches trade/risk semantics —
  committee data is advisory only (same contract as decision_report.py).
- Absence and read failures are reported honestly (``status`` field), never
  coerced into benign zeros.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("aletheia.analytics.committee_overview")

_MAX_LIMIT = 200  # mirrors repositories.list_committee_reports hard cap


def _parse_json(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return parsed


def _evaluation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape one committee_reports row into the display contract.

    Mirrors decision_report._committee_report_section field-by-field (so the
    drawer and the workbench render identical data), extended with the row
    identity fields the workbench needs (id, symbol, opportunity_id).
    """
    opinions = _parse_json(row.get("agent_opinions_json"), [])
    indicators = _parse_json(row.get("indicators_json"), {})
    risks = _parse_json(row.get("risks_json"), [])
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "opportunity_id": row.get("opportunity_id"),
        "symbol": row.get("symbol") or "",
        "decision_id": row.get("decision_id"),
        "direction": row.get("direction") or "neutral",
        "confidence": row.get("confidence") or 0.0,
        "explainability_score": row.get("explainability_score") or 0.0,
        "blocked": bool(row.get("blocked")),
        "model_used": row.get("model_used"),
        "fallback_used": bool(row.get("fallback_used")),
        "indicators": indicators if isinstance(indicators, dict) else {},
        "agent_opinions": opinions if isinstance(opinions, list) else [],
        "risks": risks if isinstance(risks, list) else [],
    }


def _summarize(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    """Window summary computed client-agnostically over the returned rows."""
    total = len(evaluations)
    direction_mix: dict[str, int] = {}
    models: dict[str, int] = {}
    symbols: set[str] = set()
    confidence_sum = 0.0
    es_sum = 0.0
    blocked = 0
    fallback = 0
    for ev in evaluations:
        direction = str(ev.get("direction") or "neutral")
        direction_mix[direction] = direction_mix.get(direction, 0) + 1
        confidence_sum += float(ev.get("confidence") or 0.0)
        es_sum += float(ev.get("explainability_score") or 0.0)
        if ev.get("blocked"):
            blocked += 1
        if ev.get("fallback_used"):
            fallback += 1
        model = ev.get("model_used")
        if model:
            models[str(model)] = models.get(str(model), 0) + 1
        if ev.get("symbol"):
            symbols.add(str(ev["symbol"]))
    return {
        "total": total,
        "direction_mix": direction_mix,
        "avg_confidence": round(confidence_sum / total, 4) if total else None,
        "avg_explainability": round(es_sum / total, 4) if total else None,
        "blocked_count": blocked,
        "fallback_count": fallback,
        "models": models,
        "symbol_count": len(symbols),
        "latest_at": evaluations[0]["created_at"] if evaluations else None,
    }


def assemble_committee_overview(
    symbol: str | None = None,
    limit: int = _MAX_LIMIT,
) -> dict[str, Any]:
    """Assemble the AI Jury workbench payload.

    Newest-first evaluations plus window summary. Any storage read failure
    degrades to ``status="read_failed"`` so the UI can render an honest
    "not available" instead of guessing.
    """
    try:
        from aletheia.storage.repositories import list_committee_reports

        rows = list_committee_reports(
            symbol=symbol or None,
            limit=max(1, min(int(limit), _MAX_LIMIT)),
        )
    except Exception as exc:  # fail-soft: advisory data must never 500 loudly
        logger.warning("committee overview read failed: %s", exc)
        return {
            "status": "read_failed",
            "reason": f"committee reports could not be read: {type(exc).__name__}",
            "evaluations": [],
        }
    evaluations = [_evaluation_from_row(dict(r)) for r in rows]
    return {
        "status": "ok",
        "window": {
            "count": len(evaluations),
            "limit": max(1, min(int(limit), _MAX_LIMIT)),
            "symbol": symbol or None,
        },
        "summary": _summarize(evaluations),
        "evaluations": evaluations,
    }
