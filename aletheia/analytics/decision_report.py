"""Read-only decision report aggregator.

Assembles the complete "why did we buy" explanation for one opportunity by
joining already-persisted rows: opportunity, PI decision, trade proposal,
risk decision, filter decisions, position/PnL outcome and audit events.

Honesty contract (mirrors the Opportunity Flow Ledger):
- This module never writes. Every statement is a SELECT; there is no
  INSERT/UPDATE/DELETE anywhere in the read path.
- Missing evidence is ``null`` or ``"not_recorded"`` — never coerced or
  fabricated (a NULL ``realized_pnl`` stays null, never 0).
- Committee evaluations report ``"not_available"`` when no persisted
  committee_reports row exists for the opportunity (pre-persistence history);
  nothing is invented.
- A section-level read failure degrades to ``{"status": "read_failed", ...}``
  without destroying the rest of the report.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.analytics.decision_report")

SCHEMA_VERSION = 1
_AUDIT_EVENT_LIMIT = 50


def assemble_decision_report(opportunity_id: str) -> dict[str, Any]:
    """Build the full read-only decision explanation for one opportunity.

    Returns HTTP-200-friendly payloads in every case: an unknown id yields
    ``found=false`` (same convention as opportunity-trace), never an error.
    """
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report: dict[str, Any] = {
        "opportunity_id": opportunity_id,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "read_only": True,
        "found": False,
    }

    db = _open_db()
    if db is None:
        report["reason"] = "Database is not initialized."
        return report

    opportunity = _safe_section(lambda: _opportunity_section(db, opportunity_id))
    report["opportunity"] = opportunity
    if not opportunity or opportunity.get("status") != "ok":
        # Neither opportunities nor serenity_opportunities knows this id.
        return report
    report["found"] = True

    decision_rows = _safe_section(lambda: _pi_decision_rows(db, opportunity_id), default=[])
    report["pi_decision"] = _safe_section(lambda: _pi_decision_section(decision_rows))
    decision_ids = [str(r["id"]) for r in decision_rows if r.get("id")]

    proposal_rows = _safe_section(lambda: _proposal_rows(db, opportunity_id), default=[])
    report["proposal"] = _safe_section(lambda: _proposal_section(proposal_rows))
    proposal_ids = [str(r["id"]) for r in proposal_rows if r.get("id")]

    risk_ids_holder: list[str] = []
    report["risk_decision"] = _safe_section(
        lambda: _risk_decision_section(db, proposal_ids, risk_ids_holder)
    )

    report["filter_decisions"] = _safe_section(
        lambda: _filter_decisions_section(db, opportunity_id, decision_ids)
    )

    report["outcome"] = _safe_section(
        lambda: _outcome_section(db, opportunity_id, proposal_ids)
    )

    report["audit_events"] = _safe_section(
        lambda: _audit_events_section(
            db, [opportunity_id, *proposal_ids, *risk_ids_holder]
        )
    )

    # AI Investment Committee evaluations: persisted advisory explainability
    # records (committee_reports). Absence is reported honestly for
    # opportunities evaluated before persistence existed.
    report["committee_report"] = _safe_section(
        lambda: _committee_report_section(db, opportunity_id)
    )
    return report


# ---------------------------------------------------------------------------
# Section builders (each is read-only and individually failure-isolated)
# ---------------------------------------------------------------------------


def _committee_report_section(db, opportunity_id: str) -> dict[str, Any]:
    """Most recent persisted AI committee evaluations for this opportunity.

    Advisory explainability data (direction, confidence, explainability score,
    LLM reasoning, evidence). Read-only; absence is honest "not_available".
    """
    rows = db.execute(
        "SELECT * FROM committee_reports WHERE opportunity_id = ? "
        "ORDER BY created_at DESC LIMIT 5",
        (opportunity_id,),
    ).fetchall()
    if not rows:
        return {
            "status": "not_available",
            "reason": "no committee evaluation was persisted for this opportunity",
        }
    evaluations: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        try:
            opinions = json.loads(row.get("agent_opinions_json") or "[]")
        except (TypeError, ValueError):
            opinions = []
        try:
            indicators = json.loads(row.get("indicators_json") or "{}")
        except (TypeError, ValueError):
            indicators = {}
        try:
            risks = json.loads(row.get("risks_json") or "[]")
        except (TypeError, ValueError):
            risks = []
        evaluations.append({
            "created_at": row.get("created_at"),
            "decision_id": row.get("decision_id"),
            "direction": row.get("direction"),
            "confidence": row.get("confidence"),
            "explainability_score": row.get("explainability_score"),
            "blocked": bool(row.get("blocked")),
            "model_used": row.get("model_used"),
            "fallback_used": bool(row.get("fallback_used")),
            "indicators": indicators if isinstance(indicators, dict) else {},
            "agent_opinions": opinions if isinstance(opinions, list) else [],
            "risks": risks if isinstance(risks, list) else [],
        })
    return {"status": "ok", "evaluations": evaluations}


def _opportunity_section(db, opportunity_id: str) -> dict[str, Any] | None:
    row = _fetch_one(db, "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,))
    if row:
        metadata = _parse_json(row.get("metadata"), {})
        manifest = None
        if isinstance(metadata, dict):
            manifest = metadata.get("evidence_manifest")
        return {
            "status": "ok",
            "source": "market_observer",
            "symbol": row.get("symbol"),
            "market": row.get("market"),
            "direction_bias": row.get("direction_bias"),
            "thesis": row.get("thesis"),
            "confidence": row.get("confidence"),
            "confidence_score": row.get("confidence_score"),
            "key_observations": _parse_observations(row.get("key_observations")),
            "scanner_family": row.get("scanner_family"),
            "evidence_manifest": manifest,
            "created_at": row.get("created_at"),
        }

    row = _fetch_one(
        db, "SELECT * FROM serenity_opportunities WHERE id = ?", (opportunity_id,)
    )
    if row:
        return {
            "status": "ok",
            "source": "serenity",
            "symbol": row.get("symbol"),
            "market": row.get("market_type"),
            "direction_bias": row.get("direction"),
            "thesis": row.get("thesis"),
            # Serenity self-grades evidence tiers instead of categorical confidence.
            "confidence": row.get("evidence_tier"),
            "confidence_score": row.get("confidence_score"),
            "key_observations": None,
            "scanner_family": "serenity",
            "evidence_manifest": None,
            "created_at": row.get("created_at"),
            "stage": row.get("stage"),
            "action_intent": row.get("action_intent"),
            "assessed_tier": row.get("assessed_tier"),
            "risk_flags": _parse_json(row.get("risk_flags"), []),
            "verdict": _parse_json(row.get("verdict_json"), None),
        }
    return None


def _pi_decision_rows(db, opportunity_id: str) -> list[dict[str, Any]]:
    # opportunity_id may point at either table (PiDecision.signal_source tags
    # which one); a plain id match covers both without fabricating a source.
    return _fetch_all(
        db,
        "SELECT * FROM pi_decisions WHERE opportunity_id = ? "
        "ORDER BY datetime(created_at) DESC, rowid DESC",
        (opportunity_id,),
    )


def _pi_decision_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "not_recorded"}
    row = rows[0]  # most recent decision is the one the chain acted on
    return {
        "status": "ok",
        "decision_id": row.get("id"),
        "created_at": row.get("created_at"),
        "action": row.get("action"),
        "conviction": row.get("conviction"),
        "reasoning": row.get("reasoning") or None,
        "bear_case": row.get("bear_case") or None,
        "key_assumptions": _parse_json(row.get("key_assumptions"), []),
        "evidence_manifest": _parse_json(row.get("evidence_manifest_json"), None),
        "proposal_id": row.get("proposal_id"),
        "proposal_status": row.get("proposal_status"),
        "signal_source": row.get("signal_source"),
        "debate": {
            "ran": bool(row.get("debate_ran")),
            "initial_conviction": row.get("debate_initial_conviction"),
            "bull_confidence": row.get("debate_bull_confidence"),
            "bear_confidence": row.get("debate_bear_confidence"),
            "adjustment": row.get("debate_adjustment"),
        },
        "decision_count": len(rows),
    }


def _proposal_rows(db, opportunity_id: str) -> list[dict[str, Any]]:
    return _fetch_all(
        db,
        "SELECT * FROM trade_proposals WHERE opportunity_id = ? "
        "ORDER BY datetime(created_at) ASC, rowid ASC",
        (opportunity_id,),
    )


def _proposal_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"status": "not_recorded", "proposals": []}
    proposals = [
        {
            "proposal_id": row.get("id"),
            "created_at": row.get("created_at"),
            "status": row.get("status"),
            "execution_mode": row.get("execution_mode"),
            "direction": row.get("direction"),
            "quantity": row.get("quantity"),
            "price": row.get("price"),
            "stop_price": row.get("stop_price"),
            "take_profit": row.get("take_profit"),
            "risk_reward": row.get("risk_reward"),
            "agent_rationale": row.get("agent_rationale"),
            "attribution_snapshot": _parse_json(row.get("attribution_snapshot"), None),
        }
        for row in rows
    ]
    return {"status": "ok", "proposals": proposals}


def _risk_decision_section(
    db, proposal_ids: list[str], risk_ids_holder: list[str]
) -> dict[str, Any]:
    if not proposal_ids:
        return {"status": "not_recorded"}
    placeholders = ",".join("?" for _ in proposal_ids)
    rows = _fetch_all(
        db,
        "SELECT * FROM risk_decisions WHERE proposal_id IN "
        f"({placeholders}) ORDER BY datetime(created_at) ASC, rowid ASC",
        tuple(proposal_ids),
    )
    if not rows:
        return {"status": "not_recorded"}
    risk_ids_holder.extend(str(r["id"]) for r in rows if r.get("id"))
    # Prefer the latest authoritative decision; fall back to the latest row.
    authoritative = [r for r in rows if r.get("decision_role") == "authoritative"]
    row = (authoritative or rows)[-1]
    return {
        "status": "ok",
        "risk_decision_id": row.get("id"),
        "proposal_id": row.get("proposal_id"),
        "created_at": row.get("created_at"),
        "approved": bool(row.get("approved")),
        "violations": _parse_json(row.get("violations"), []),
        "message": row.get("message"),
        "decision_role": row.get("decision_role"),
        "policy_id": row.get("policy_id"),
        "policy_revision": row.get("policy_revision"),
        "policy_hash": row.get("policy_hash"),
        "decision_count": len(rows),
    }


def _filter_decisions_section(
    db, opportunity_id: str, decision_ids: list[str]
) -> dict[str, Any]:
    candidate_keys = [f"opportunity:{opportunity_id}"]
    candidate_keys.extend(f"pi_decision:{d}" for d in decision_ids)
    ck_placeholders = ",".join("?" for _ in candidate_keys)
    rows = _fetch_all(
        db,
        "SELECT filter_name, stage, decision, reason_code, reason_detail, "
        "candidate_key, evaluated_at FROM filter_decisions "
        "WHERE opportunity_id = ? OR candidate_key IN "
        f"({ck_placeholders}) "
        "ORDER BY datetime(evaluated_at) ASC, rowid ASC",
        (opportunity_id, *candidate_keys),
    )
    if not rows:
        return {"status": "no_data", "items": []}
    items = [
        {
            "filter_name": row.get("filter_name"),
            "stage": row.get("stage"),
            "decision": row.get("decision"),
            "reason_code": row.get("reason_code"),
            "reason_detail": row.get("reason_detail"),
            "candidate_key": row.get("candidate_key"),
            "evaluated_at": row.get("evaluated_at"),
        }
        for row in rows
    ]
    return {"status": "ok", "items": items}


def _outcome_section(
    db, opportunity_id: str, proposal_ids: list[str]
) -> dict[str, Any]:
    clauses = ["opportunity_id = ?"]
    params: list[Any] = [opportunity_id]
    if proposal_ids:
        clauses.append(f"proposal_id IN ({','.join('?' for _ in proposal_ids)})")
        params.extend(proposal_ids)
    rows = _fetch_all(
        db,
        "SELECT * FROM positions WHERE "
        f"{' OR '.join(clauses)} ORDER BY datetime(opened_at) ASC, rowid ASC",
        tuple(params),
    )
    if not rows:
        return {"status": "not_recorded", "positions": None}
    positions = []
    for row in rows:
        # Position rows carry no exit price column; the per-close ledger
        # (position_close_events) records it. Absent there → stays null.
        exit_price = None
        close_row = _fetch_one(
            db,
            "SELECT exit_price FROM position_close_events WHERE position_id = ? "
            "ORDER BY datetime(closed_at) DESC, rowid DESC LIMIT 1",
            (row.get("id"),),
        )
        if close_row:
            exit_price = close_row.get("exit_price")
        positions.append(
            {
                "position_id": row.get("id"),
                "symbol": row.get("symbol"),
                "status": row.get("status"),
                "entry_price": row.get("entry_price"),
                "exit_price": exit_price,
                # NULL realized_pnl must stay null — coercing to 0 would
                # fabricate account economics (see Position.realized_pnl).
                "realized_pnl": row.get("realized_pnl"),
                "close_reason": row.get("close_reason"),
                "opened_at": row.get("opened_at"),
                "closed_at": row.get("closed_at"),
            }
        )
    return {"status": "ok", "positions": positions}


def _audit_events_section(db, entity_ids: list[str]) -> dict[str, Any]:
    clean = [e for e in entity_ids if e]
    if not clean:
        return {"status": "no_data", "items": []}
    placeholders = ",".join("?" for _ in clean)
    rows = _fetch_all(
        db,
        "SELECT event_type, actor, source, message, timestamp FROM audit_events "
        f"WHERE entity_id IN ({placeholders}) "
        "ORDER BY datetime(timestamp) ASC, rowid ASC LIMIT ?",
        (*clean, _AUDIT_EVENT_LIMIT),
    )
    if not rows:
        return {"status": "no_data", "items": []}
    items = [
        {
            "event_type": row.get("event_type"),
            "actor": row.get("actor"),
            "source": row.get("source"),
            "message": row.get("message"),
            "timestamp": row.get("timestamp"),
        }
        for row in rows
    ]
    return {"status": "ok", "items": items}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_db():
    import aletheia.storage.sqlite as sqlite_store

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        return None
    try:
        return sqlite_store.get_db()
    except Exception:
        logger.warning("decision report: database could not be opened", exc_info=True)
        return None


def _safe_section(builder, default=None):
    """Run one section builder; a failure degrades instead of killing the report."""
    try:
        result = builder()
        return default if result is None else result
    except Exception as exc:  # pragma: no cover - defensive degradation path
        logger.warning("decision report section failed: %s", exc, exc_info=True)
        return {"status": "read_failed", "error": str(exc)}


def _fetch_one(db, sql: str, params: tuple) -> dict[str, Any] | None:
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _fetch_all(db, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def _parse_json(value: Any, fallback: Any) -> Any:
    """Tolerant JSON parse: NULL/broken payloads fall back, never raise."""
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _parse_observations(value: Any) -> Any:
    """key_observations is a JSON list; broken legacy text is surfaced verbatim."""
    if value in (None, ""):
        return None
    parsed = _parse_json(value, None)
    if isinstance(parsed, list):
        return parsed
    return value
