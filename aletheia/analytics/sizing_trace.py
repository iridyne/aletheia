"""Read-only Sizing Trace Audit.

Explains why recent proposals/positions are small using observed DB facts plus a
clearly marked approximation from current sizing config. It never mutates config,
orders, proposals, positions, risk state, prompts, or persona settings.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any


_CORE_TABLES = ("trade_proposals",)
_OPTIONAL_TABLES = ("pi_decisions", "positions", "orders", "fills", "risk_decisions")
_LOW_BASE_NOTIONAL_USDT = 5.0
_LOW_CONVICTION = 50
_TINY_EXECUTED_NOTIONAL_USDT = 5.0
_MAX_MULTIPLIER = 2.5


def build_sizing_trace_audit(
    *,
    hours: int | None = 24,
    days: int | None = None,
    min_sample_size: int = 3,
) -> dict:
    """Build a JSON-serializable sizing trace report."""
    import aletheia.storage.sqlite as sqlite_store

    window = _window(hours=hours, days=days)
    generated_at = _utcnow().isoformat(timespec="seconds")
    report = _base_report(generated_at, window)

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        report["schema_errors"].append("Database is not initialized.")
        report["main_sizing_bottleneck"] = _bottleneck(
            "insufficient_data",
            "insufficient_data",
            "No local trading database is available.",
            "Collect proposals or positions before auditing sizing.",
        )
        return report

    try:
        db = sqlite_store.get_db()
    except sqlite3.OperationalError as exc:
        report["schema_errors"].append(str(exc))
        report["main_sizing_bottleneck"] = _bottleneck(
            "insufficient_data",
            "insufficient_data",
            f"Trading database could not be opened: {exc}",
            "Fix database availability before auditing sizing.",
        )
        return report

    missing_core = [table for table in _CORE_TABLES if not _table_exists(db, table)]
    missing_optional = [table for table in _OPTIONAL_TABLES if not _table_exists(db, table)]
    if missing_optional:
        report["schema_errors"].append(f"Missing optional evidence table(s): {', '.join(missing_optional)}")
    missing = missing_core
    if missing:
        report["schema_errors"].append(f"Missing required table(s): {', '.join(missing)}")
        report["main_sizing_bottleneck"] = _bottleneck(
            "insufficient_data",
            "insufficient_data",
            f"Required sizing evidence table(s) are missing: {', '.join(missing)}.",
            "Run migrations or collect proposal/position evidence before interpreting sizing.",
        )
        return report

    try:
        cfg = _load_config()
        traces = _build_traces(db, window["cutoff"], cfg)
        report["traces"] = traces
        report["summary"] = _summary(traces)
        report["compression_reasons"] = _compression_reasons(traces)
        report["main_sizing_bottleneck"] = _main_sizing_bottleneck(report, min_sample_size)
    except sqlite3.OperationalError as exc:
        report["schema_errors"].append(str(exc))
        report["main_sizing_bottleneck"] = _bottleneck(
            "insufficient_data",
            "insufficient_data",
            f"Required sizing evidence is unavailable: {exc}",
            "Run migrations or collect more sizing evidence before interpreting.",
        )
    return report


def render_sizing_trace_audit(report: dict) -> str:
    """Render the report for the standalone CLI."""
    window = report.get("window") or {}
    summary = report.get("summary") or {}
    lines = ["Sizing Trace Audit", "", f"Window: {_window_label(window)}", ""]
    if report.get("schema_errors"):
        lines.append("Evidence:")
        for err in report["schema_errors"]:
            lines.append(f"  {err}")
        lines.append("")
    lines.extend([
        "Summary:",
        f"  sample_size: {summary.get('sample_size', 0)}",
        f"  median_proposal_notional: {_fmt_usdt(summary.get('median_intended_notional_usdt'))}",
        f"  median_executed_notional: {_fmt_usdt(summary.get('median_executed_notional_usdt'))}",
        f"  median_current_policy_projection: {_fmt_usdt(summary.get('median_current_policy_projection_usdt'))}",
        f"  median_conviction: {_fmt_num(summary.get('median_conviction'))}",
        f"  median_leverage: {_fmt_num(summary.get('median_leverage'))}",
        "",
    ])
    bottleneck = report.get("main_sizing_bottleneck") or {}
    lines.extend([
        "Current-policy projection bottleneck:",
        f"  {bottleneck.get('reason', 'insufficient_data')}",
        "",
        "Reason:",
        f"  {bottleneck.get('summary', 'Not enough sizing evidence.')}",
        "",
        "Next action:",
        f"  {bottleneck.get('next_action', 'Collect more evidence before changing risk or sizing behavior.')}",
    ])
    traces = report.get("traces") or []
    if traces:
        lines.extend(["", "Recent traces:"])
        for trace in traces[:10]:
            lines.append(
                "  "
                f"{trace.get('proposal_id')} {trace.get('symbol')} {trace.get('direction')} "
                f"reason={trace.get('main_reason')} "
                f"proposal={_fmt_usdt(trace.get('proposal_notional_usdt'))} "
                f"executed={_fmt_usdt(trace.get('executed_notional_usdt'))} "
                f"current_policy={_fmt_usdt(trace.get('current_policy_projection_usdt'))}"
            )
    return "\n".join(lines)


def compact_sizing_trace_summary(report: dict) -> dict:
    """Small object suitable for doctor JSON output."""
    has_evidence = int((report.get("summary") or {}).get("sample_size") or 0) > 0
    return {
        "available": has_evidence or not bool(report.get("schema_errors")),
        "reason": "; ".join(report.get("schema_errors") or []),
        "main_sizing_bottleneck": report.get("main_sizing_bottleneck"),
        "summary": report.get("summary"),
        "compression_reasons": report.get("compression_reasons"),
    }


def _base_report(generated_at: str, window: dict) -> dict:
    return {
        "generated_at": generated_at,
        "window": window,
        "summary": {
            "sample_size": 0,
            "avg_intended_notional_usdt": None,
            "median_intended_notional_usdt": None,
            "avg_executed_notional_usdt": None,
            "median_executed_notional_usdt": None,
            "avg_current_policy_projection_usdt": None,
            "median_current_policy_projection_usdt": None,
            "avg_conviction": None,
            "median_conviction": None,
            "avg_leverage": None,
            "median_leverage": None,
        },
        "compression_reasons": [],
        "traces": [],
        "main_sizing_bottleneck": _bottleneck(
            "insufficient_data",
            "insufficient_data",
            "Not enough proposal or position evidence to trace sizing.",
            "Collect executed/rejected proposals before changing sizing or risk behavior.",
        ),
        "schema_errors": [],
    }


def _build_traces(db, cutoff: str, cfg) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM trade_proposals "
        "WHERE datetime(created_at) >= datetime(?) "
        "ORDER BY datetime(created_at) DESC LIMIT 100",
        (cutoff,),
    ).fetchall()
    traces = []
    for row in rows:
        proposal = dict(row)
        position = _position_for_proposal(db, proposal["id"])
        conviction = _conviction_for_proposal(db, proposal)
        paper_trade_count = _paper_trade_count(db, proposal.get("strategy_id"), proposal.get("created_at"))
        atr_pct = _latest_atr_pct(db, proposal.get("symbol"))
        sizing = _reconstruct_current_config_size(
            cfg,
            symbol=proposal.get("symbol"),
            conviction=conviction,
            paper_trade_count=paper_trade_count,
            atr_pct=atr_pct,
        )
        proposal_notional = _notional(proposal.get("quantity"), proposal.get("price"))
        executed_notional = _executed_notional(db, proposal["id"], position)
        risk = _risk_cap_hit(db, proposal["id"], proposal_notional, cfg)
        leverage = _first_number(position.get("leverage") if position else None, proposal.get("leverage"), 1)
        reason, notes = _reason(
            cfg=cfg,
            conviction=conviction,
            base_notional=sizing["base_notional_usdt"],
            effective_kelly=sizing["effective_kelly"],
            paper_trade_count=paper_trade_count,
            atr_pct=atr_pct,
            raw_vol_size=sizing["raw_vol_size"],
            risk_cap_hit=risk,
            proposal_notional=proposal_notional,
            executed_notional=executed_notional,
        )
        notes.append("reconstructed_from_current_config")
        notes.append("current_policy_projection_not_historical_intent")
        traces.append({
            "proposal_id": proposal["id"],
            "position_id": position.get("id") if position else None,
            "symbol": proposal.get("symbol"),
            "direction": proposal.get("direction"),
            "created_at": proposal.get("created_at"),
            "status": proposal.get("status"),
            "conviction": conviction,
            "base_notional_usdt": sizing["base_notional_usdt"],
            "effective_scaling_fraction": sizing["effective_kelly"],
            "effective_kelly": sizing["effective_kelly"],
            "paper_trade_count": paper_trade_count,
            "atr_pct": atr_pct,
            "raw_vol_size": sizing["raw_vol_size"],
            "legacy_scaling_cap_usdt": sizing["kelly_cap"],
            "kelly_cap": sizing["kelly_cap"],
            "legacy_parameter_name": "kelly_fraction",
            # Compatibility field now carries the persisted historical proposal
            # notional. The current config reconstruction has its own honest name.
            "intended_position_size_usdt": proposal_notional,
            "proposal_notional_usdt": proposal_notional,
            "current_policy_projection_usdt": sizing["intended_position_size_usdt"],
            "executed_notional_usdt": executed_notional,
            "leverage": leverage,
            "risk_cap_hit": risk,
            "main_reason": reason,
            "notes": notes,
        })
    return traces


def _position_for_proposal(db, proposal_id: str) -> dict | None:
    if not _table_exists(db, "positions"):
        return None
    row = db.execute(
        "SELECT * FROM positions WHERE proposal_id = ? ORDER BY datetime(opened_at) DESC LIMIT 1",
        (proposal_id,),
    ).fetchone()
    return dict(row) if row else None


def _conviction_for_proposal(db, proposal: dict) -> int | None:
    if not _table_exists(db, "pi_decisions"):
        return None
    row = db.execute(
        "SELECT conviction FROM pi_decisions "
        "WHERE proposal_id = ? ORDER BY datetime(created_at) DESC LIMIT 1",
        (proposal.get("id"),),
    ).fetchone()
    if not row and proposal.get("opportunity_id"):
        row = db.execute(
            "SELECT conviction FROM pi_decisions "
            "WHERE opportunity_id = ? AND action IN ('trade', 'scale_in', 'flip') "
            "ORDER BY datetime(created_at) DESC LIMIT 1",
            (proposal.get("opportunity_id"),),
        ).fetchone()
    return int(row["conviction"]) if row and row["conviction"] is not None else None


def _paper_trade_count(db, strategy_id: str | None, created_at: str | None) -> int:
    if not strategy_id or not _table_exists(db, "positions"):
        return 0
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
    rows = eligible_strategy_outcomes(closed_before=created_at) if created_at else eligible_strategy_outcomes()
    return sum(
        1 for row in rows
        if str(row.get("strategy_id") or "") == strategy_id
        and "live" not in str(row.get("execution_mode") or "")
    )


def _latest_atr_pct(db, symbol: str | None) -> float | None:
    if not symbol or not _table_exists(db, "universe_feature_snapshots") or not _table_exists(db, "universe_registry"):
        return None
    row = db.execute(
        "SELECT ufs.atr_pct FROM universe_feature_snapshots ufs "
        "JOIN universe_registry ur ON ufs.asset_id = ur.id "
        "WHERE ur.symbol = ? ORDER BY ufs.ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return _float_or_none(row["atr_pct"]) if row and row["atr_pct"] is not None else None


def _reconstruct_current_config_size(
    cfg,
    *,
    symbol: str | None,
    conviction: int | None,
    paper_trade_count: int,
    atr_pct: float | None,
) -> dict:
    from aletheia.trading.sizing import compute_position_size

    base = _effective_base_notional(cfg, symbol)
    base_kelly = _effective_kelly_base(cfg, symbol)
    effective_kelly = _effective_kelly(base_kelly, paper_trade_count)
    conviction_i = int(conviction if conviction is not None else 70)
    raw_vol_size = None
    kelly_cap = None
    sizing_base = base

    if bool(getattr(cfg.trading, "vol_sizing_enabled", False)) and atr_pct and atr_pct > 0:
        risk_budget = float(getattr(cfg.trading, "vol_target_risk_usdt", 25.0))
        raw_vol_size = risk_budget / (atr_pct / 100.0)
        kelly_cap = base * (effective_kelly * 4) * _MAX_MULTIPLIER
        sizing_base = min(raw_vol_size, kelly_cap)

    intended = compute_position_size(conviction_i, sizing_base, kelly_fraction=effective_kelly)
    return {
        "base_notional_usdt": _round(base),
        "effective_kelly": _round(effective_kelly),
        "raw_vol_size": _round(raw_vol_size),
        "kelly_cap": _round(kelly_cap),
        "intended_position_size_usdt": _round(intended),
    }


def _effective_base_notional(cfg, symbol: str | None) -> float:
    if bool(getattr(cfg.trading, "persona_sizing_enabled", False)):
        persona = (getattr(cfg.trading, "personas", {}) or {}).get(getattr(cfg.trading, "active_persona", ""))
        value = _persona_param_init(persona, "notional_usdt")
        if value is not None:
            return float(value)
    return float(getattr(cfg.trading, "default_notional_usdt", 25.0))


def _effective_kelly_base(cfg, symbol: str | None) -> float:
    if bool(getattr(cfg.trading, "persona_sizing_enabled", False)):
        persona = (getattr(cfg.trading, "personas", {}) or {}).get(getattr(cfg.trading, "active_persona", ""))
        value = _persona_param_init(persona, "kelly_fraction")
        if value is not None:
            return float(value)
    return float(getattr(cfg.trading, "vol_kelly_cap_fraction", 0.25))


def _persona_param_init(persona: Any, key: str) -> float | None:
    if persona is None:
        return None
    params = getattr(persona, "params", {}) or {}
    param = params.get(key)
    if param is None:
        return None
    return _float_or_none(getattr(param, "init", None))


def _effective_kelly(base_kelly: float, paper_trade_count: int) -> float:
    if paper_trade_count < 10:
        return 0.1
    if paper_trade_count < 30:
        t = (paper_trade_count - 10) / 20.0
        return 0.1 + t * (base_kelly - 0.1)
    return base_kelly


def _executed_notional(db, proposal_id: str, position: dict | None) -> float | None:
    # Fills are immutable and proposal-scoped. A position can later be resized,
    # so it is only a fallback for legacy paths that never persisted fills.
    if _table_exists(db, "fills") and _table_exists(db, "orders"):
        rows = db.execute(
            "SELECT f.fill_price, f.fill_quantity FROM fills f "
            "JOIN orders o ON o.id = f.order_id "
            "WHERE o.proposal_id = ?",
            (proposal_id,),
        ).fetchall()
        total = sum(float(r["fill_price"] or 0) * float(r["fill_quantity"] or 0) for r in rows)
        if total > 0:
            return _round(total)
    if position:
        return _notional(position.get("quantity"), position.get("entry_price"))
    return None


def _risk_cap_hit(db, proposal_id: str, proposal_notional: float | None, cfg) -> bool | None:
    rows = []
    if _table_exists(db, "risk_decisions"):
        rows = db.execute(
            "SELECT violations, message FROM risk_decisions WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchall()
    text = " ".join(
        " ".join(str(x) for x in _json_list(row["violations"])) + " " + str(row["message"] or "")
        for row in rows
    ).lower()
    if any(token in text for token in ("max_demo_position_notional", "max_live_position_notional", "max_demo_position_margin", "max_live_position_margin", "single_trade_risk", "max_leverage", "account_leverage")):
        return True
    cap = _float_or_none(getattr(cfg.risk, "max_demo_position_notional_usdt", None))
    if cap is not None and proposal_notional is not None and proposal_notional >= cap:
        return True
    return False if rows or proposal_notional is not None else None


def _reason(
    *,
    cfg,
    conviction: int | None,
    base_notional: float | None,
    effective_kelly: float | None,
    paper_trade_count: int,
    atr_pct: float | None,
    raw_vol_size: float | None,
    risk_cap_hit: bool | None,
    proposal_notional: float | None,
    executed_notional: float | None,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if risk_cap_hit:
        notes.append("Risk decision or configured notional cap constrained the proposal.")
        return "risk_cap", notes
    if base_notional is not None and base_notional <= _LOW_BASE_NOTIONAL_USDT:
        notes.append(f"Base notional is {base_notional}U.")
        return "low_base_notional", notes
    if conviction is not None and conviction < _LOW_CONVICTION:
        notes.append(f"System Operator conviction is {conviction}.")
        return "low_conviction", notes
    if paper_trade_count < 10 and effective_kelly == 0.1:
        notes.append("Fewer than 10 closed paper samples compress the legacy scaling factor to 0.1.")
        return "sample_aware_scaling", notes
    if atr_pct is not None and atr_pct >= 6.0:
        notes.append(f"ATR is high at {atr_pct}%.")
        return "high_atr", notes
    if bool(getattr(cfg.trading, "persona_sizing_enabled", False)) and getattr(cfg.trading, "active_persona", "") == "conservative":
        notes.append("Active persona is conservative.")
        return "persona_conservative", notes
    if proposal_notional and executed_notional and executed_notional < proposal_notional * 0.8:
        notes.append("Executed notional is materially below proposal notional.")
        return "exchange_minimum", notes
    return "unknown", notes


def _summary(traces: list[dict]) -> dict:
    intended = [t["proposal_notional_usdt"] for t in traces if t.get("proposal_notional_usdt") is not None]
    executed = [t["executed_notional_usdt"] for t in traces if t.get("executed_notional_usdt") is not None]
    projected = [t["current_policy_projection_usdt"] for t in traces if t.get("current_policy_projection_usdt") is not None]
    convictions = [t["conviction"] for t in traces if t.get("conviction") is not None]
    leverages = [t["leverage"] for t in traces if t.get("leverage") is not None]
    return {
        "sample_size": len(traces),
        "avg_intended_notional_usdt": _stat_mean(intended),
        "median_intended_notional_usdt": _stat_median(intended),
        "avg_executed_notional_usdt": _stat_mean(executed),
        "median_executed_notional_usdt": _stat_median(executed),
        "avg_current_policy_projection_usdt": _stat_mean(projected),
        "median_current_policy_projection_usdt": _stat_median(projected),
        "avg_conviction": _stat_mean(convictions),
        "median_conviction": _stat_median(convictions),
        "avg_leverage": _stat_mean(leverages),
        "median_leverage": _stat_median(leverages),
    }


def _compression_reasons(traces: list[dict]) -> list[dict]:
    counter = Counter(t.get("main_reason") or "unknown" for t in traces)
    rows = []
    for reason, count in counter.most_common():
        rows.append({
            "reason": reason,
            "count": count,
            "evidence": _reason_evidence(reason, traces),
        })
    return rows


def _reason_evidence(reason: str, traces: list[dict]) -> str:
    subset = [t for t in traces if t.get("main_reason") == reason]
    if reason == "sample_aware_scaling":
        counts = [t.get("paper_trade_count") for t in subset if t.get("paper_trade_count") is not None]
        return f"{len(subset)} trace(s) have paper_trade_count below 10; min={min(counts) if counts else 'n/a'}."
    if reason == "low_base_notional":
        bases = [t.get("base_notional_usdt") for t in subset if t.get("base_notional_usdt") is not None]
        return f"{len(subset)} trace(s) use base notional <= {_LOW_BASE_NOTIONAL_USDT}U; median={_stat_median(bases)}."
    if reason == "low_conviction":
        convictions = [t.get("conviction") for t in subset if t.get("conviction") is not None]
        return f"{len(subset)} trace(s) have conviction below {_LOW_CONVICTION}; median={_stat_median(convictions)}."
    if reason == "risk_cap":
        return f"{len(subset)} trace(s) hit risk or leverage cap evidence."
    if reason == "high_atr":
        atrs = [t.get("atr_pct") for t in subset if t.get("atr_pct") is not None]
        return f"{len(subset)} trace(s) have high ATR; median={_stat_median(atrs)}%."
    return f"{len(subset)} trace(s) classified as {reason}."


def _main_sizing_bottleneck(report: dict, min_sample_size: int) -> dict:
    summary = report["summary"]
    if summary["sample_size"] < min_sample_size:
        return _bottleneck(
            "insufficient_data",
            "insufficient_data",
            f"Only {summary['sample_size']} proposal trace(s) in the window.",
            "Collect more proposals or run a longer sizing trace window before changing sizing.",
        )
    reasons = report.get("compression_reasons") or []
    top = reasons[0] if reasons else {"reason": "unknown", "count": 0, "evidence": ""}
    severity = "warn" if top["reason"] in {"sample_aware_scaling", "low_base_notional", "risk_cap", "low_conviction"} else "info"
    median_exec = summary.get("median_executed_notional_usdt")
    tiny = median_exec is not None and median_exec <= _TINY_EXECUTED_NOTIONAL_USDT
    if top["reason"] == "unknown" and not tiny:
        severity = "ok"
    return _bottleneck(
        top["reason"],
        severity,
        f"Current config projection only, not historical intent. {top['evidence']} "
        f"Median executed notional={_fmt_usdt(median_exec)}.",
        _next_action(top["reason"]),
        evidence_basis="current_config_projection",
    )


def _next_action(reason: str) -> str:
    return {
        "sample_aware_scaling": "Do not change risk blindly. Review funnel throughput, then run a controlled sample-building experiment.",
        "low_base_notional": "Inspect trading.default_notional_usdt or active persona init values before changing risk caps.",
        "low_conviction": "Inspect System Operator decision evidence; sizing is reflecting low conviction rather than failing downstream.",
        "risk_cap": "Inspect risk rejection reasons and leverage/notional caps; do not auto-raise caps from doctor.",
        "high_atr": "Inspect ATR source and volatility-target settings; high volatility may be intentionally compressing size.",
        "persona_conservative": "Confirm whether conservative persona is intended; doctor must not switch persona automatically.",
        "exchange_minimum": "Compare proposal notional to fills and demo exchange minimum behavior.",
    }.get(reason, "Review traces before changing sizing, leverage, legacy scaling, or persona settings.")


def _bottleneck(
    reason: str,
    severity: str,
    summary: str,
    next_action: str,
    *,
    evidence_basis: str = "persisted_or_insufficient",
) -> dict:
    return {
        "reason": reason,
        "severity": severity,
        "summary": summary,
        "next_action": next_action,
        "evidence_basis": evidence_basis,
    }


def _window(*, hours: int | None, days: int | None) -> dict:
    if days is not None:
        lookback_hours = max(1, int(days) * 24)
        display_hours = None
    else:
        lookback_hours = max(1, int(hours or 24))
        display_hours = lookback_hours
    return {
        "hours": display_hours,
        "days": int(days) if days is not None else None,
        "cutoff": (_utcnow() - timedelta(hours=lookback_hours)).isoformat(),
    }


def _table_exists(db, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _load_config():
    from aletheia.config.loader import load_config

    return load_config()


def _notional(quantity: Any, price: Any) -> float | None:
    q = _float_or_none(quantity)
    p = _float_or_none(price)
    if q is None or p is None or q <= 0 or p <= 0:
        return None
    return _round(q * p)


def _first_number(*values: Any) -> float | None:
    for value in values:
        converted = _float_or_none(value)
        if converted is not None:
            return _round(converted)
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return [str(raw)] if str(raw).strip() else []
    if isinstance(value, list):
        return value
    return [value]


def _stat_mean(values: list) -> float | None:
    return _round(mean(values)) if values else None


def _stat_median(values: list) -> float | None:
    return _round(median(values)) if values else None


def _round(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _fmt_usdt(value: Any) -> str:
    return "n/a" if value is None else f"{value}U"


def _fmt_num(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _window_label(window: dict) -> str:
    if window.get("days") is not None:
        return f"{window['days']}d"
    return f"{window.get('hours', 24)}h"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
