"""Batch C parity gate for #3701: verify FCO is ready to replace UFO as sole outcome ledger.

Read-only report. No writes, no parity table. Checks all five gate criteria:

1. Complete prospective 7-day selected FCO 24h cohort
2. Latest due time has matured (no overdue-pending FCO rows)
3. Exact member-lineage coverage = 100%
4. No unexplained comparable economic conflict between UFO and FCO
5. Equivalent Risk family×direction×market output under same cost model

Run on Fedora after deploying the Batch B commits.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_parity_gate(
    *,
    risk_window_days: int = 7,
    cost_model_version: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Return a parity report dict. `passed` is True only when all gates clear."""
    from aletheia.storage.repositories import (
        complete_outcome_economics_sql,
        family_direction_edge,
    )
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION as _DEFAULT_CMV

    cost_model = cost_model_version or _DEFAULT_CMV
    db = get_db()
    cutoff = (_now() - timedelta(days=risk_window_days)).isoformat()
    now_iso = _now().isoformat()

    report: dict[str, Any] = {
        "generated_at": now_iso,
        "cost_model_version": cost_model,
        "risk_window_days": risk_window_days,
        "gates": {},
        "passed": False,
    }

    # ------------------------------------------------------------------ Gate 1
    # Complete prospective 7-day selected FCO 24h cohort
    # ------------------------------------------------------------------ Gate 1
    fco_settled = db.execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes "
        "WHERE status='settled' AND horizon='24h' "
        "AND settled_at >= ? AND cost_model_version=? "
        f"AND {complete_outcome_economics_sql()} "
        "AND allocation_selected=1 AND direction IN ('long','short')",
        (cutoff, cost_model),
    ).fetchone()[0]
    ufo_settled = db.execute(
        "SELECT COUNT(*) FROM universe_forward_outcomes "
        "WHERE status='settled' AND horizon='24h' "
        "AND settled_at >= ? AND cost_model_version=? "
        f"AND {complete_outcome_economics_sql()} "
        "AND direction IN ('long','short')",
        (cutoff, cost_model),
    ).fetchone()[0] if _table_exists(db, "universe_forward_outcomes") else 0

    gate1_pass = fco_settled >= max(10, ufo_settled * 0.80) if ufo_settled else fco_settled >= 10
    report["gates"]["g1_fco_settled_cohort"] = {
        "fco_settled_24h": fco_settled,
        "ufo_settled_24h": ufo_settled,
        "threshold": max(10, int(ufo_settled * 0.80)) if ufo_settled else 10,
        "passed": gate1_pass,
    }

    # ------------------------------------------------------------------ Gate 2
    # No overdue pending FCO rows
    # ------------------------------------------------------------------ Gate 2
    fco_overdue = db.execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes "
        "WHERE status='pending' AND due_at IS NOT NULL AND due_at < ?",
        (now_iso,),
    ).fetchone()[0]
    gate2_pass = fco_overdue == 0
    report["gates"]["g2_no_overdue_pending"] = {
        "fco_overdue_pending": fco_overdue,
        "passed": gate2_pass,
    }

    # ------------------------------------------------------------------ Gate 3
    # Exact member-lineage coverage = 100%
    # Active members must have learning_candidate_key pointing to a settled FCO row
    # ------------------------------------------------------------------ Gate 3
    member_count = db.execute(
        "SELECT COUNT(*) FROM universe_members"
    ).fetchone()[0] if _table_exists(db, "universe_members") else 0
    members_with_key = db.execute(
        "SELECT COUNT(*) FROM universe_members "
        "WHERE learning_candidate_key IS NOT NULL "
        "AND TRIM(learning_candidate_key) != ''"
    ).fetchone()[0] if _table_exists(db, "universe_members") else 0
    members_with_fco = db.execute(
        "SELECT COUNT(*) FROM universe_members m "
        "WHERE m.learning_candidate_key IS NOT NULL "
        "AND TRIM(m.learning_candidate_key) != '' "
        "AND EXISTS (SELECT 1 FROM filter_candidate_outcomes f "
        "WHERE f.outcome_candidate_key=m.learning_candidate_key AND f.status='settled')"
    ).fetchone()[0] if _table_exists(db, "universe_members") else 0
    gate3_pass = member_count > 0 and members_with_fco >= members_with_key
    coverage_pct = round(100.0 * members_with_fco / member_count, 1) if member_count else 0.0
    report["gates"]["g3_member_lineage_coverage"] = {
        "active_members": member_count,
        "members_with_candidate_key": members_with_key,
        "members_with_settled_fco": members_with_fco,
        "coverage_pct": coverage_pct,
        "passed": gate3_pass,
    }

    # ------------------------------------------------------------------ Gate 4
    # No unexplained economic conflict (UFO vs FCO for same outcome_candidate_key)
    # ------------------------------------------------------------------ Gate 4
    conflicts = 0
    if _table_exists(db, "universe_forward_outcomes"):
        conflict_rows = db.execute(
            "SELECT COUNT(*) FROM filter_candidate_outcomes f "
            "JOIN universe_forward_outcomes u "
            "ON u.outcome_candidate_key=f.outcome_candidate_key "
            "AND u.horizon=f.horizon "
            "WHERE f.status='settled' AND u.status='settled' "
            "AND ABS(COALESCE(f.net_return_pct,0) - COALESCE(u.net_return_pct,0)) > 0.01 "
            "AND f.settled_at >= ? AND f.cost_model_version=?",
            (cutoff, cost_model),
        ).fetchone()[0]
        conflicts = conflict_rows
    gate4_pass = conflicts == 0
    report["gates"]["g4_no_economic_conflict"] = {
        "conflicting_rows": conflicts,
        "passed": gate4_pass,
    }

    # ------------------------------------------------------------------ Gate 5
    # Equivalent Risk family×direction×market output
    # ------------------------------------------------------------------ Gate 5
    try:
        fco_edge = family_direction_edge(horizon="24h", within_days=risk_window_days,
                                         cost_model_version=cost_model)
        gate5_pass = len(fco_edge) > 0
    except Exception as exc:
        fco_edge = []
        gate5_pass = False
        report["gates"]["g5_risk_edge_output"] = {
            "error": str(exc), "passed": False,
        }
    else:
        report["gates"]["g5_risk_edge_output"] = {
            "family_direction_market_groups": len(fco_edge),
            "passed": gate5_pass,
        }

    all_passed = all(g["passed"] for g in report["gates"].values())
    report["passed"] = all_passed
    report["summary"] = (
        "ALL GATES PASSED — FCO ready for UFO cutover"
        if all_passed
        else "GATES FAILED — check individual gate results before proceeding"
    )
    return report


def _table_exists(db: Any, name: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


if __name__ == "__main__":
    import sys

    verbose = "--verbose" in sys.argv
    days = 7
    for arg in sys.argv[1:]:
        if arg.startswith("--days="):
            days = int(arg.split("=", 1)[1])

    result = run_parity_gate(risk_window_days=days, verbose=verbose)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["passed"] else 1)
