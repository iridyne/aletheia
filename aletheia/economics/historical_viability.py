"""Read-only access to archived Paper Internal viability reports.

No report generation, revaluation or persistence is available here.
"""

from __future__ import annotations

from typing import Any

from aletheia.storage.sqlite import readonly_db


def list_viability_reports(
    *,
    scope: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List persisted viability reports for CLI/UI inspection."""
    where: list[str] = []
    params: list[Any] = []
    if scope:
        if scope not in ("system", "operator"):
            raise ValueError("scope must be 'system' or 'operator'")
        where.append("scope=?")
        params.append(scope)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with readonly_db(allow_missing=True) as db:
        if db is None or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='viability_reports'"
        ).fetchone():
            return []
        rows = db.execute(
            f"SELECT * FROM viability_reports {where_sql} "
            f"ORDER BY replace(window_end,' ','T') DESC, generated_at DESC "
            f"LIMIT ? OFFSET ?",
            (*params, max(1, min(int(limit), 500)), max(0, int(offset))),
        ).fetchall()
    return [dict(row) for row in rows]


def get_viability_report(report_id: str) -> dict[str, Any] | None:
    """Fetch one persisted viability report by id."""
    with readonly_db(allow_missing=True) as db:
        if db is None or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='viability_reports'"
        ).fetchone():
            return None
        row = db.execute(
            "SELECT * FROM viability_reports WHERE id=?", (report_id,)
        ).fetchone()
    return dict(row) if row else None


def render_viability(report: dict[str, Any]) -> str:
    def money(value: Any) -> str:
        return "unknown" if value is None else f"${float(value):.6f}"

    lines = [
        f"Archived Economic Viability Audit — {report['scope']}",
        "",
        f"Window: {report['window_start']} → {report['window_end']}",
        f"Execution mode: {report['execution_mode']}",
        f"Verdict: {report['verdict']}",
        f"Cost completeness: {report['cost_completeness']}",
        f"Sample count: {report['sample_count']}",
        "",
        f"Trading result: {money(report.get('net_trading_result_usd'))}",
        f"Baseline result: {money(report.get('baseline_result_usd'))}",
        f"Incremental value: {money(report.get('incremental_value_usd'))}",
        f"LLM cost: {money(report.get('intelligence_cost_usd'))}",
        f"Tool cost: {money(report.get('tool_cost_usd'))}",
        f"Data cost: {money(report.get('data_cost_usd'))}",
        f"Infrastructure cost: {money(report.get('infrastructure_cost_usd'))}",
        f"Other cost: {money(report.get('other_cost_usd'))}",
        f"Total charged cost: {money(report.get('deployment_cost_usd'))}",
        f"Viability amount: {money(report.get('viability_amount_usd'))}",
        "",
        f"Reason: {report['reason']}",
    ]
    exclusions = report.get("exclusion_counts") or {}
    if exclusions:
        lines.append(
            "Exclusions: "
            + ", ".join(f"{key}={value}" for key, value in sorted(exclusions.items()))
        )
    return "\n".join(lines)
