"""Dependency-neutral position-closure semantics and explicit read cohorts.

`positions.status='closed'` is a lifecycle fact, not proof of a strategy exit.
This module is the single source of truth shared by analytics, Learning, and later
Operator/Risk adapters.  It deliberately imports no Learning, Risk, Execution, or
exchange module.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

OUTCOME_SEMANTICS_VERSION = "oi-v1"

ClosureCategory = Literal["strategy", "administrative", "maintenance", "unknown"]

ADMINISTRATIVE_CLOSE_REASONS = frozenset({"reconciled_gone"})
MAINTENANCE_CLOSE_REASONS = frozenset({
    "demo_validation_cleanup",
    "units_bug_cleanup",
    "retry_after_fix",
})
SYNTHETIC_CLOSE_REASONS = ADMINISTRATIVE_CLOSE_REASONS | MAINTENANCE_CLOSE_REASONS

OUTCOME_EXCLUSION_REASONS = frozenset({
    "administrative_close",
    "maintenance_close",
    "not_marked_eligible",
    "unverified_entry_fill",
    "unverified_paper_fill_model",  # Preserved historical exclusion reason.
    "archived_execution_mode",
    "unverified_exit_fill",
    "unverified_exit_price_source",
    "unknown_close_category",
    "unknown_semantics_version",
    "legacy_unverified_close",
    "position_not_closed",
    "missing_close_time",
    "missing_outcome_identity",
    "incomplete_realized_economics",
    "incomplete_funding_economics",
    # Terminal state assigned by paper_funding_settler after
    # _FUNDING_GIVE_UP_HOURS of retries — realized economics remain NULL
    # (never fabricated) but the operator/consumer can distinguish it from
    # an in-flight retry. Kept in this whitelist so classify_closure()
    # surfaces the actual reason instead of collapsing to
    # "not_marked_eligible".
    "funding_permanently_unrecoverable",
    "broker_funding_history_expired",
    "pre_clean_window",
    "incident_window",
    "missing_attribution",
    "attribution_read_failed",
})

_VERIFIED_EXIT_PRICE_SOURCES = frozenset({"broker_fill", "local_fill"})
_BROKER_LEDGER_EXECUTION_MODES = frozenset({
    "bitget_demo",
    "bitget_live_limited",
    "okx_demo",
    "okx_live_limited",
    "binance_testnet",
    "binance_live_limited",
})
_STRUCTURED_KEYS = frozenset({
    "close_category",
    "entry_price_source",
    "entry_fill_verified",
    "exit_price_source",
    "exit_fill_verified",
    "outcome_eligible",
    "outcome_exclusion_reason",
    "outcome_semantics_version",
})
_FILTER_UNSET = object()


@dataclass(frozen=True)
class ClosureSemantics:
    category: ClosureCategory
    eligible: bool
    exclusion_reason: str
    semantics_version: str | None
    exit_fill_verified: bool
    exit_price_source: str
    evidence_source: str


@dataclass(frozen=True)
class OutcomeAssessment:
    eligible: bool
    reason: str
    category: str = "unknown"
    semantics_version: str | None = None
    exit_fill_verified: bool = False
    exit_price_source: str = "unavailable"
    evidence_source: str = "unknown"
    outcome_key: str | None = None
    horizon: str | None = None
    net_return_pct: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class CompleteOutcomeCohort:
    """One complete, ordered outcome range and its shared evidence identity."""

    rows: tuple[dict[str, Any], ...]
    assessments: tuple[OutcomeAssessment, ...]
    source_raw_event_count: int | None
    loaded_raw_event_count: int
    cohort_complete: bool
    cohort_state: Literal["available", "unavailable"]
    ordered_source_digest: str | None
    identity: dict[str, str | None]


class AttributionReadError(RuntimeError):
    """The attribution chain could not be read, so absence is not proven."""


def validated_fee(value: Any, *, field: str) -> float | None:
    """Return a persisted fee only when it is finite and non-negative."""
    if value is None:
        return None
    try:
        fee = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be a finite non-negative value") from None
    if not math.isfinite(fee) or fee < 0:
        raise ValueError(f"{field} must be a finite non-negative value")
    return fee


def validated_funding_economics(
    *,
    market_type: Any,
    execution_mode: Any,
    funding_cost: Any,
    funding_source: Any,
) -> tuple[float | None, str]:
    """Return funding only when its provenance matches the account boundary.

    A numeric value alone is not evidence. This reader preserves the recorded
    local funding provenance of archived simulator rows. External demo/live
    futures require broker-ledger evidence. Unknown execution modes do not
    inherit a plausible-looking source label.
    """
    market = _normalize(market_type)
    mode = _normalize(execution_mode)
    source = _normalize(funding_source)
    parsed_cost = _to_float(funding_cost)

    if market == "spot":
        if (
            parsed_cost is not None
            and abs(parsed_cost) <= 1e-12
            and source == "not_applicable"
        ):
            return 0.0, "not_applicable"
        return None, "unavailable"

    if market != "usdt_futures" or parsed_cost is None:
        return None, "unavailable"
    if mode == "paper_internal" and source == "local_simulation":
        return parsed_cost, source
    if mode in _BROKER_LEDGER_EXECUTION_MODES and source == "broker_ledger":
        return parsed_cost, source
    return None, "unavailable"


def outcome_write_eligibility(
    row: dict[str, Any], *, exit_fill_verified: bool
) -> tuple[bool, str]:
    """Evaluate the exact prospective row that a close producer will persist.

    Producers pass the complete closed Position, including realized economics,
    close time, evidence sources, and semantics version.  The temporary positive
    marker lets the shared read-side evaluator test every other requirement; its
    final answer is then persisted and is the only authority for outcome hooks.
    """
    try:
        attribution_complete = attribution_chain_complete(row)
    except AttributionReadError:
        return False, "attribution_read_failed"
    prospective = {
        **row,
        "exit_fill_verified": exit_fill_verified,
        "outcome_eligible": True,
        "outcome_exclusion_reason": "eligible",
        "_attribution_complete": attribution_complete,
    }
    assessment = assess_reportable_outcome(prospective)
    return assessment.eligible, assessment.reason


# One relational rule for prospective close writes and snapshot cohort reads.
# ``p`` is a persisted Position or the prospective Position supplied by a producer.
_ATTRIBUTION_SQL = """
    EXISTS (
        SELECT 1 FROM orders o
        JOIN trade_proposals tp ON tp.id = p.proposal_id
        WHERE o.id = p.order_id AND o.position_id = p.id
          AND o.proposal_id = tp.id
          AND (o.close_reason IS NULL OR trim(o.close_reason) = '')
          AND (
              (
                  p.opportunity_id IS NULL AND tp.opportunity_id IS NULL
                  AND EXISTS (
                      SELECT 1 FROM entry_plans ep
                      JOIN candidate_planning_generations pg
                        ON pg.id = ep.planning_generation_id
                      JOIN universe_members um ON um.id = ep.candidate_id
                      JOIN universe_snapshots us ON us.id = um.snapshot_id
                      WHERE ep.id = tp.entry_plan_id
                        AND o.entry_plan_id = ep.id
                        AND pg.entry_plan_id = ep.id
                        AND pg.last_candidate_id = um.id
                        AND us.source_kind = 'universe'
                        AND json_extract(ep.plan_json, '$.source_kind') = 'universe'
                        AND json_extract(ep.plan_json, '$.source_id') = um.id
                        AND json_extract(ep.plan_json, '$.candidate_id') = um.id
                        AND json_extract(ep.plan_json, '$.planning_generation_id') = pg.id
                  )
              )
              OR (
                  trim(p.opportunity_id) != ''
                  AND tp.opportunity_id = p.opportunity_id
                  AND (
                      tp.entry_plan_id IS NULL
                      OR EXISTS (
                          SELECT 1 FROM entry_plans ep WHERE ep.id = tp.entry_plan_id
                            AND json_extract(ep.plan_json, '$.source_kind') = 'serenity'
                            AND json_extract(ep.plan_json, '$.source_id') = p.opportunity_id
                      )
                  )
                  AND (
                      EXISTS (SELECT 1 FROM opportunities op WHERE op.id = p.opportunity_id)
                      OR EXISTS (SELECT 1 FROM serenity_opportunities sop WHERE sop.id = p.opportunity_id)
                  )
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM orders leg
              WHERE leg.position_id = p.id
                AND (leg.close_reason IS NULL OR trim(leg.close_reason) = '')
                AND (leg.proposal_id IS NULL OR leg.proposal_id <> tp.id)
          )
    )
"""


def attribution_chain_complete(row: dict[str, Any], *, db: Any = None) -> bool:
    """Prove source-specific ancestry and every entry leg's Proposal backlink.

    Universe entries use their immutable Plan and its completed Generation /
    Candidate binding. Opportunity and Serenity source history keeps its existing
    relational rule. Current Candidate eligibility or Generation freshness is not
    a prerequisite for evaluating an already executed trade.
    """
    position_id = str(row.get("position_id") or row.get("id") or "").strip()
    proposal_id = str(row.get("proposal_id") or "").strip()
    order_id = str(row.get("order_id") or "").strip()
    if not position_id or not proposal_id or not order_id:
        return False
    if db is None:
        from aletheia.storage.sqlite import get_db

        db = get_db()
    try:
        matched = db.execute(
            "SELECT " + _ATTRIBUTION_SQL + " FROM "
            "(SELECT ? AS id, ? AS proposal_id, ? AS order_id, ? AS opportunity_id) p",
            (position_id, proposal_id, order_id, row.get("opportunity_id")),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise AttributionReadError(str(exc)) from exc
    return matched is not None and bool(matched[0])


def classify_closure(row: dict[str, Any]) -> ClosureSemantics:
    """Classify one position close without trusting PnL as exit evidence."""
    reason = _normalize(row.get("close_reason"))
    if reason in ADMINISTRATIVE_CLOSE_REASONS:
        return _semantics(
            "administrative", False, "administrative_close", row,
            evidence_source="close_reason",
        )
    if reason in MAINTENANCE_CLOSE_REASONS:
        return _semantics(
            "maintenance", False, "maintenance_close", row,
            evidence_source="close_reason",
        )

    if row.get("status") not in (None, "closed"):
        return _semantics("unknown", False, "position_not_closed", row)

    structured_values = {key: row.get(key) for key in _STRUCTURED_KEYS}
    has_structured_evidence = any(
        value not in (None, "", False, "unknown", "unavailable", "unclassified")
        for value in structured_values.values()
    )
    if not has_structured_evidence:
        return _semantics(
            "unknown", False, "legacy_unverified_close", row,
            evidence_source="legacy_reason",
        )

    version = _normalize(row.get("outcome_semantics_version")) or None
    category = _category(row.get("close_category"))
    verified = _to_bool(row.get("exit_fill_verified"))
    price_source = _normalize(row.get("exit_price_source")) or "unavailable"

    if version != OUTCOME_SEMANTICS_VERSION:
        return _semantics(category, False, "unknown_semantics_version", row)
    if category in {"administrative", "maintenance"}:
        return _semantics(category, False, f"{category}_close", row)
    if category != "strategy":
        return _semantics(category, False, "unknown_close_category", row)
    if not _to_bool(row.get("outcome_eligible")):
        exclusion = _normalize(row.get("outcome_exclusion_reason"))
        if exclusion not in OUTCOME_EXCLUSION_REASONS:
            exclusion = "not_marked_eligible"
        return _semantics(category, False, exclusion, row)
    if not verified:
        return _semantics(category, False, "unverified_exit_fill", row)
    if price_source not in _VERIFIED_EXIT_PRICE_SOURCES:
        return _semantics(category, False, "unverified_exit_price_source", row)
    return _semantics(category, True, "eligible", row)


def assess_position_closure(
    row: dict[str, Any], *, db: Any = None
) -> OutcomeAssessment:
    """Return closure eligibility plus complete realized economics."""
    closure = classify_closure(row)
    if not closure.eligible:
        return _assessment_from_closure(closure)

    if not _to_bool(row.get("entry_fill_verified")) or _normalize(
        row.get("entry_price_source")
    ) not in _VERIFIED_EXIT_PRICE_SOURCES:
        return _assessment_from_closure(closure, reason="unverified_entry_fill")

    if not row.get("closed_at"):
        return _assessment_from_closure(closure, reason="missing_close_time")
    key = row.get("position_id") or row.get("id")
    if not key:
        return _assessment_from_closure(closure, reason="missing_outcome_identity")
    pnl = _to_float(row.get("realized_pnl"))
    entry = _to_float(row.get("entry_price"))
    exit_price = _to_float(
        row.get("current_price")
        if row.get("current_price") is not None
        else row.get("exit_price")
    )
    quantity = _to_float(row.get("quantity"))
    entry_fee = _to_float(row.get("entry_fee"))
    exit_fee = _to_float(row.get("exit_fee"))
    if (
        pnl is None
        or entry is None
        or exit_price is None
        or quantity is None
        or entry_fee is None
        or exit_fee is None
        or entry <= 0
        or exit_price <= 0
        or quantity <= 0
        or entry_fee < 0
        or exit_fee < 0
    ):
        return _assessment_from_closure(
            closure, reason="incomplete_realized_economics"
        )

    market_type = _normalize(row.get("market_type"))
    if market_type not in {"spot", "usdt_futures"}:
        return _assessment_from_closure(
            closure, reason="incomplete_realized_economics"
        )
    funding_cost, _funding_source = validated_funding_economics(
        market_type=market_type,
        execution_mode=row.get("execution_mode"),
        funding_cost=row.get("funding_cost"),
        funding_source=row.get("funding_source"),
    )
    if funding_cost is None:
        return _assessment_from_closure(
            closure, reason="incomplete_funding_economics"
        )

    direction = _normalize(row.get("direction"))
    if direction in {"long", "buy"}:
        gross = (exit_price - entry) * quantity
    elif direction in {"short", "sell"}:
        gross = (entry - exit_price) * quantity
    else:
        return _assessment_from_closure(
            closure, reason="incomplete_realized_economics"
        )
    expected_pnl = gross - entry_fee - exit_fee - funding_cost
    tolerance = max(1e-8, abs(expected_pnl) * 1e-8, entry * quantity * 1e-10)
    if abs(pnl - expected_pnl) > tolerance:
        return _assessment_from_closure(
            closure, reason="incomplete_realized_economics"
        )

    has_attribution_ids = all(
        str(row.get(field) or "").strip()
        for field in ("proposal_id", "order_id")
    )
    marker_was_injected = (
        "_attribution_complete" in row
        and row.get("_attribution_complete") is not None
    )
    if marker_was_injected:
        attribution_complete = _to_bool(row.get("_attribution_complete"))
    else:
        try:
            attribution_complete = attribution_chain_complete(row, db=db)
        except AttributionReadError:
            return _assessment_from_closure(
                closure, reason="attribution_read_failed"
            )
    if not has_attribution_ids or not attribution_complete:
        return _assessment_from_closure(closure, reason="missing_attribution")
    return _assessment_from_closure(
        closure,
        outcome_key=f"position:{key}",
        horizon="realized_close",
        net_return_pct=pnl / (entry * quantity) * 100.0,
    )


def assess_reportable_outcome(
    row: dict[str, Any], *, db: Any = None
) -> OutcomeAssessment:
    """Exclude archived execution and apply the current clean-window gate."""
    assessment = assess_position_closure(row, db=db)
    if not assessment.eligible:
        return assessment
    if _normalize(row.get("execution_mode")) == "paper_internal":
        return replace(
            assessment,
            eligible=False,
            reason="archived_execution_mode",
        )
    from aletheia.analytics.learning_window import assess_outcome_window

    window_reason = assess_outcome_window(row)
    if window_reason != "eligible":
        return replace(assessment, eligible=False, reason=window_reason)
    return assessment


def raw_account_events(
    *,
    execution_mode: str | None = None,
    strategy_id: Any = _FILTER_UNSET,
    closed_after: str | None = None,
    closed_before: str | None = None,
    symbol: str | None = None,
    runtime_session_id: str | None = None,
    limit: int | None = 10_000,
    db: Any = None,
) -> list[dict]:
    """Read immutable closed-position events without implying outcome quality."""
    if limit is not None and (limit < 1 or limit > 10_000):
        raise ValueError("limit must be between 1 and 10000")
    from aletheia.storage.sqlite import (
        get_db,
        iso_instant_us,
        register_instant_us,
    )

    connection = db if db is not None else get_db()
    register_instant_us(connection)
    conditions = ["p.status = 'closed'"]
    params: list[object] = []
    if execution_mode is not None:
        conditions.append("p.execution_mode = ?")
        params.append(execution_mode)
    if strategy_id is not _FILTER_UNSET:
        if strategy_id is None:
            conditions.append("p.strategy_id IS NULL")
        else:
            conditions.append("p.strategy_id = ?")
            params.append(str(strategy_id))
    if closed_after is not None:
        conditions.append("aletheia_instant_us(p.closed_at) >= ?")
        params.append(iso_instant_us(closed_after))
    if closed_before is not None:
        conditions.append("aletheia_instant_us(p.closed_at) < ?")
        params.append(iso_instant_us(closed_before))
    if symbol is not None:
        conditions.append("p.symbol = ?")
        params.append(symbol)
    if runtime_session_id is not None:
        conditions.append("p.runtime_session_id = ?")
        params.append(runtime_session_id)
    params.append(-1 if limit is None else limit)
    rows = connection.execute(
        """
        SELECT recent.* FROM (
        SELECT p.*,
               (
                   SELECT f.paper_fill_model_version
                   FROM fills f
                   WHERE f.order_id = p.order_id
                   ORDER BY aletheia_instant_us(f.filled_at), f.id
                   LIMIT 1
               ) AS _paper_fill_model_version,
               """ + _ATTRIBUTION_SQL + """ AS _attribution_complete
        FROM positions p
        WHERE """ + " AND ".join(conditions)
        + " ORDER BY aletheia_instant_us(p.closed_at) DESC, p.id DESC LIMIT ?"
        + ") AS recent ORDER BY "
        + "aletheia_instant_us(recent.closed_at) ASC, recent.id ASC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def read_complete_outcome_cohort(
    *,
    execution_mode: str | None = None,
    closed_after: str | None = None,
    closed_before: str | None = None,
    runtime_session_id: str | None = None,
    db: Any = None,
) -> CompleteOutcomeCohort:
    """Read a bounded authority cohort to exhaustion in one SQLite snapshot."""
    identity = {
        "execution_mode": execution_mode,
        "runtime_session_id": runtime_session_id,
        "closed_after": closed_after,
        "closed_before": closed_before,
    }
    try:
        rows = tuple(raw_account_events(
            execution_mode=execution_mode,
            closed_after=closed_after,
            closed_before=closed_before,
            runtime_session_id=runtime_session_id,
            limit=None,
            db=db,
        ))
        assessments = tuple(assess_reportable_outcome(row) for row in rows)
        from aletheia.storage.hashing import sha256_json

        digest = sha256_json(list(rows))
    except Exception:
        return CompleteOutcomeCohort(
            rows=(),
            assessments=(),
            source_raw_event_count=None,
            loaded_raw_event_count=0,
            cohort_complete=False,
            cohort_state="unavailable",
            ordered_source_digest=None,
            identity=identity,
        )
    return CompleteOutcomeCohort(
        rows=rows,
        assessments=assessments,
        source_raw_event_count=len(rows),
        loaded_raw_event_count=len(rows),
        cohort_complete=True,
        cohort_state="available",
        ordered_source_digest=digest,
        identity=identity,
    )


def eligible_strategy_outcomes(**filters) -> list[dict]:
    """Read only closures proven eligible by the current central semantics."""
    return [
        row for row in raw_account_events(**filters)
        if assess_reportable_outcome(row).eligible
    ]


def administrative_closures(**filters) -> list[dict]:
    """Read administrative and maintenance closures for audit/diagnosis."""
    return [
        row for row in raw_account_events(**filters)
        if classify_closure(row).category in {"administrative", "maintenance"}
    ]


def build_outcome_cohort_summary(
    *, cohort: CompleteOutcomeCohort | None = None, **filters
) -> dict[str, Any]:
    """Describe raw close events and the verified judgement cohort side by side."""
    from aletheia.analytics.learning_window import outcome_window_metadata
    try:
        rows = raw_account_events(**filters) if cohort is None else cohort.rows
        assessments = (
            [assess_reportable_outcome(row) for row in rows]
            if cohort is None
            else cohort.assessments
        )
        if len(rows) != len(assessments):
            raise ValueError("outcome rows and assessments must have equal length")
    except Exception:
        return {
            "raw_event_count": None,
            "eligible_outcome_count": None,
            "excluded_outcome_count": None,
            "outcome_exclusion_reasons": {"account_event_read_failed": 1},
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            "cohort_state": "unavailable",
            "raw_recorded_realized_pnl": None,
            "raw_economics_unavailable_count": None,
            "eligible_realized_pnl": None,
            **outcome_window_metadata(),
        }

    eligible_count = 0
    eligible_pnl = 0.0
    raw_recorded_pnl = 0.0
    raw_recorded_count = 0
    exclusion_reasons: dict[str, int] = {}
    for row, assessment in zip(rows, assessments, strict=True):
        raw_pnl = _to_float(row.get("realized_pnl"))
        if raw_pnl is not None:
            raw_recorded_pnl += raw_pnl
            raw_recorded_count += 1
        if assessment.eligible:
            eligible_count += 1
            eligible_pnl += float(raw_pnl)
        else:
            exclusion_reasons[assessment.reason] = exclusion_reasons.get(assessment.reason, 0) + 1

    raw_count = len(rows)
    return {
        "raw_event_count": raw_count,
        "eligible_outcome_count": eligible_count,
        "excluded_outcome_count": raw_count - eligible_count,
        "outcome_exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "cohort_state": "available" if eligible_count else "insufficient",
        "raw_recorded_realized_pnl": round(raw_recorded_pnl, 8) if raw_recorded_count else None,
        "raw_economics_unavailable_count": raw_count - raw_recorded_count,
        "eligible_realized_pnl": round(eligible_pnl, 8) if eligible_count else None,
        **outcome_window_metadata(),
    }


def is_current_outcome_artifact(
    artifact: dict[str, Any],
    *,
    max_age: timedelta,
    now: datetime | None = None,
) -> bool:
    """Admit a derived report only when its semantics and freshness are provable."""
    if artifact.get("outcome_semantics_version") != OUTCOME_SEMANTICS_VERSION:
        return False
    from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ID
    if artifact.get("clean_window_anchor_id") != CLEAN_WINDOW_ANCHOR_ID:
        return False
    generated_at = artifact.get("generated_at")
    if not generated_at:
        return False
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = current - generated.astimezone(timezone.utc)
    return timedelta(0) <= age <= max_age


def is_synthetic_close(reason: str | None) -> bool:
    return _normalize(reason) in SYNTHETIC_CLOSE_REASONS


def _semantics(
    category: ClosureCategory,
    eligible: bool,
    exclusion_reason: str,
    row: dict[str, Any],
    *,
    evidence_source: str = "structured",
) -> ClosureSemantics:
    return ClosureSemantics(
        category=category,
        eligible=eligible,
        exclusion_reason=exclusion_reason,
        semantics_version=_normalize(row.get("outcome_semantics_version")) or None,
        exit_fill_verified=_to_bool(row.get("exit_fill_verified")),
        exit_price_source=_normalize(row.get("exit_price_source")) or "unavailable",
        evidence_source=evidence_source,
    )


def _assessment_from_closure(
    closure: ClosureSemantics,
    *,
    reason: str | None = None,
    outcome_key: str | None = None,
    horizon: str | None = None,
    net_return_pct: float | None = None,
) -> OutcomeAssessment:
    eligible = closure.eligible and reason is None
    return OutcomeAssessment(
        eligible=eligible,
        reason=reason or closure.exclusion_reason,
        category=closure.category,
        semantics_version=closure.semantics_version,
        exit_fill_verified=closure.exit_fill_verified,
        exit_price_source=closure.exit_price_source,
        evidence_source=closure.evidence_source,
        outcome_key=outcome_key if eligible else None,
        horizon=horizon if eligible else None,
        net_return_pct=net_return_pct if eligible else None,
        source="realized_position",
    )


def _category(value: Any) -> ClosureCategory:
    normalized = _normalize(value)
    if normalized in {"strategy", "administrative", "maintenance"}:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return _normalize(value) in {"1", "true", "yes"}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
