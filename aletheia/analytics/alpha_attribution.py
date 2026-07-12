"""Read-only Alpha Attribution Audit.

This module answers one question: which layer appears to add or destroy alpha?
It does not write to the database, call agents, submit orders, change risk, or
modify ranker/System Operator behavior.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any


VERDICT_NAMES = (
    "Universe Alpha",
    "Ranker Alpha",
    "System Operator Alpha",
    "Learning Alpha",
    "Serenity Alpha",
)

MIN_SAMPLE_SIZE = 5
ALPHA_THRESHOLD_PCT = 0.10

_MODULES_BY_LAYER = {
    "Universe Alpha": [
        "aletheia/universe/scanners.py",
        "aletheia/universe/allocator.py",
        "aletheia/universe/filters.py",
        "aletheia/universe/service.py",
    ],
    "Ranker Alpha": [
        "aletheia/agents/paper_trader_ranker.py",
        "aletheia/analytics/factor_feedback.py",
        "aletheia/knowledge/guards.py",
    ],
    "System Operator Alpha": [
        "aletheia/agent_shell/trading_decision.py",
        "aletheia/agent_shell/trading_context.py",
        "aletheia/agent_runtime/pi_daemon.py",
    ],
    "Learning Alpha": [
        "aletheia/learning/lessons.py",
        "aletheia/analytics/memo_effectiveness.py",
        "aletheia/knowledge/guards.py",
        "aletheia/analytics/guard_effectiveness.py",
    ],
    "Serenity Alpha": [
        "aletheia/agents/research_worker.py",
        "aletheia/trading/serenity_scoring.py",
        "aletheia/trading/serenity_evidence.py",
    ],
}

_HORIZON_SECONDS = {
    "1h": 3600,
    "4h": 4 * 3600,
    "24h": 24 * 3600,
    "1d": 24 * 3600,
    "3d": 3 * 24 * 3600,
    "7d": 7 * 24 * 3600,
}


def build_alpha_attribution_audit(days: int = 30, min_sample_size: int = MIN_SAMPLE_SIZE) -> dict:
    """Build the five-layer alpha attribution report.

    Returns a JSON-serializable dict. Every layer has a verdict, baseline,
    sample_size, delta, confidence_label, and reason. ``insufficient_data`` is a
    first-class verdict because forcing positive/neutral/negative from thin data
    would create false precision.
    """
    import aletheia.storage.sqlite as sqlite_store

    days = _normalize_days(days)
    generated_at = _utcnow().isoformat(timespec="seconds")
    cutoff = (_utcnow() - timedelta(days=days)).isoformat()

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        return _empty_report(generated_at, days, "Database is not initialized; no alpha evidence is available.")

    try:
        db = sqlite_store.get_db()
    except sqlite3.OperationalError:
        return _empty_report(generated_at, days, "Database could not be opened; no alpha evidence is available.")

    verdicts = {
        "universe": _safe_layer("Universe Alpha", lambda: _universe_alpha(db, cutoff, min_sample_size)),
        "ranker": _safe_layer("Ranker Alpha", lambda: _ranker_alpha(db, cutoff, min_sample_size)),
        "system_operator": _safe_layer("System Operator Alpha", lambda: _system_operator_alpha(db, cutoff, min_sample_size)),
        "learning": _safe_layer("Learning Alpha", lambda: _learning_alpha(db, cutoff, days, min_sample_size)),
        "serenity": _safe_layer("Serenity Alpha", lambda: _serenity_alpha(db, cutoff, min_sample_size)),
    }

    return {
        "generated_at": generated_at,
        "window_days": days,
        "verdicts": verdicts,
        "main_bottleneck": _main_bottleneck(verdicts),
    }


def _empty_report(generated_at: str, days: int, reason: str) -> dict:
    verdicts = {
        "universe": _empty_layer("Universe Alpha", reason),
        "ranker": _empty_layer("Ranker Alpha", reason),
        "system_operator": _empty_layer("System Operator Alpha", reason),
        "learning": _empty_layer("Learning Alpha", reason),
        "serenity": _empty_layer("Serenity Alpha", reason),
    }
    return {
        "generated_at": generated_at,
        "window_days": days,
        "verdicts": verdicts,
        "main_bottleneck": _main_bottleneck(verdicts),
    }


def _safe_layer(name: str, fn) -> dict:
    try:
        return fn()
    except sqlite3.OperationalError as exc:
        return _empty_layer(name, f"Required alpha evidence table is unavailable: {exc}")


def _empty_layer(name: str, reason: str) -> dict:
    return _verdict(
        name,
        "insufficient_data",
        0,
        None,
        None,
        None,
        "insufficient",
        reason,
        evidence_type="none",
        causal_grade="insufficient",
    )


def render_alpha_audit(report: dict) -> str:
    """Render the audit in the owner-facing CLI format."""
    lines = ["Alpha Attribution Audit", "", f"Window: {report.get('window_days')}d", ""]
    ordered = [
        ("Universe", "universe"),
        ("Ranker", "ranker"),
        ("System Operator", "system_operator"),
        ("Learning", "learning"),
        ("Serenity", "serenity"),
    ]
    for label, key in ordered:
        item = report["verdicts"][key]
        lines.append(f"{label}: {item['verdict']}")
        lines.append(
            "  "
            f"sample_size={item['sample_size']} "
            f"baseline={_fmt_metric(item.get('baseline'))} "
            f"observed={_fmt_metric(item.get('observed'))} "
            f"delta={_fmt_metric(item.get('delta'))} "
            f"confidence={item['confidence_label']} "
            f"evidence={item.get('causal_grade', 'unknown')} "
            f"contribution={item.get('contribution_verdict', 'insufficient_data')}"
        )
        lines.append(f"  {item['reason']}")
        modules = item.get("suspected_modules") or []
        if item["verdict"] == "negative" and modules:
            lines.append(f"  Suspect modules: {', '.join(modules[:3])}")
    bottleneck = report["main_bottleneck"]
    lines.extend([
        "",
        "Main bottleneck:",
        bottleneck["summary"],
        "",
        "Next action:",
        bottleneck["next_action"],
    ])
    return "\n".join(lines)


def _universe_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    causal = _causal_control_alpha(db, "universe", "Universe Alpha", cutoff, min_sample_size)
    if causal is not None:
        return causal

    rows = db.execute(
        "SELECT symbol, direction, horizon, entry_ts, forward_return_pct "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND horizon IN ('1h', '4h', '24h') "
        "AND forward_return_pct IS NOT NULL "
        "AND ABS(forward_return_pct) <= 100 "
        "AND datetime(entry_ts) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    selected: list[float] = []
    baseline: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        direction = row["direction"] or "long"
        selected_ret = _directional_return(row["forward_return_pct"], direction)
        selected.append(selected_ret)
        base_returns = _deterministic_baseline_returns(
            db,
            entry_at=row["entry_ts"],
            horizon=row["horizon"],
            direction=direction,
            exclude_symbol=row["symbol"],
            seed=f"universe:{row['entry_ts']}:{row['horizon']}:{row['symbol']}",
            equity_like=False,
        )
        baseline.extend(base_returns)

        h = by_horizon.setdefault(row["horizon"], {"selected": [], "baseline": []})
        h["selected"].append(selected_ret)
        h["baseline"].extend(base_returns)

    if len(selected) < min_sample_size or len(baseline) < min_sample_size:
        return _verdict(
            "Universe Alpha",
            "insufficient_data",
            len(selected),
            _safe_mean(baseline),
            _safe_mean(selected),
            None,
            "insufficient",
            "Not enough settled universe forward outcomes with a same-period tradable baseline.",
            {"baseline_sample_size": len(baseline), "horizons": _horizon_details(by_horizon)},
        )

    observed = mean(selected)
    base = mean(baseline)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Universe Alpha",
        status,
        len(selected),
        base,
        observed,
        delta,
        _confidence_label(len(selected), min_sample_size),
        f"Selected universe members {'outperformed' if delta > 0 else 'underperformed' if delta < 0 else 'matched'} deterministic same-period tradable samples.",
        {"baseline_sample_size": len(baseline), "horizons": _horizon_details(by_horizon)},
        evidence_type="reconstructed_ohlcv_baseline",
        causal_grade="weak_reconstructed",
    )


def _ranker_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    rows = db.execute(
        "SELECT batch_id, rank, net_pnl_pct FROM shadow_trades "
        "WHERE batch_id IS NOT NULL "
        "AND resolved_at IS NOT NULL "
        "AND net_pnl_pct IS NOT NULL "
        "AND datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    batches: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        batches[row["batch_id"]].append(dict(row))

    top1: list[float] = []
    top2_5: list[float] = []
    eligible: list[float] = []
    usable_batches = 0
    for batch_rows in batches.values():
        rank1 = [float(r["net_pnl_pct"]) for r in batch_rows if int(r["rank"] or 0) == 1]
        rest = [float(r["net_pnl_pct"]) for r in batch_rows if 2 <= int(r["rank"] or 0) <= 5]
        if not rank1 or not rest:
            continue
        usable_batches += 1
        top1.append(rank1[0])
        top2_5.extend(rest)
        eligible.extend(float(r["net_pnl_pct"]) for r in batch_rows if int(r["rank"] or 0) >= 1)

    if usable_batches < min_sample_size or len(top2_5) < min_sample_size:
        return _verdict(
            "Ranker Alpha",
            "insufficient_data",
            usable_batches,
            _safe_mean(top2_5),
            _safe_mean(top1),
            None,
            "insufficient",
            "Not enough cost-aware net shadow batches with rank1 and rank2-5 outcomes.",
            {"top2_5_sample_size": len(top2_5), "eligible_average": _safe_mean(eligible)},
        )

    observed = mean(top1)
    base = mean(top2_5)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Ranker Alpha",
        status,
        usable_batches,
        base,
        observed,
        delta,
        _confidence_label(usable_batches, min_sample_size),
        f"Ranker top1 {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} the rank2-5 shadow average.",
        {"top2_5_sample_size": len(top2_5), "eligible_average": _safe_mean(eligible)},
    )


def _system_operator_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    rows = db.execute(
        "SELECT tp.id AS proposal_id, tp.opportunity_id, p.realized_pnl, "
        "p.entry_price, p.quantity, p.closed_at "
        "FROM trade_proposals tp "
        "JOIN positions p ON p.proposal_id = tp.id "
        "WHERE tp.status = 'executed' "
        "AND p.status = 'closed' "
        "AND tp.opportunity_id IS NOT NULL "
        "AND datetime(p.closed_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    accepted: list[float] = []
    shadows: list[float] = []
    compared_proposals = 0

    for row in rows:
        opp_id = row["opportunity_id"]
        batch = db.execute(
            "SELECT batch_id FROM shadow_trades "
            "WHERE opportunity_id = ? AND batch_id IS NOT NULL LIMIT 1",
            (opp_id,),
        ).fetchone()
        if not batch or not batch["batch_id"]:
            continue
        shadow_rows = db.execute(
            "SELECT net_pnl_pct FROM shadow_trades "
            "WHERE batch_id = ? "
            "AND resolved_at IS NOT NULL "
            "AND net_pnl_pct IS NOT NULL "
            "AND opportunity_id != ?",
            (batch["batch_id"], opp_id),
        ).fetchall()
        if not shadow_rows:
            continue
        pnl_pct = _position_pnl_pct(row)
        if pnl_pct is None:
            continue
        compared_proposals += 1
        accepted.append(pnl_pct)
        shadows.extend(float(r["net_pnl_pct"]) for r in shadow_rows)

    if compared_proposals < min_sample_size or len(shadows) < min_sample_size:
        return _verdict(
            "System Operator Alpha",
            "insufficient_data",
            compared_proposals,
            _safe_mean(shadows),
            _safe_mean(accepted),
            None,
            "insufficient",
            "Not enough executed closed proposals linked to resolved same-cycle shadow candidates.",
            {"shadow_sample_size": len(shadows)},
        )

    observed = mean(accepted)
    base = mean(shadows)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "System Operator Alpha",
        status,
        compared_proposals,
        base,
        observed,
        delta,
        _confidence_label(compared_proposals, min_sample_size),
        f"Accepted System Operator trades {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} same-cycle unselected shadows.",
        {"shadow_sample_size": len(shadows)},
    )


def _learning_alpha(db, cutoff: str, days: int, min_sample_size: int) -> dict:
    raw_events = _learning_events(db, cutoff)
    events = _dedupe_learning_events(raw_events)
    if not raw_events:
        return _verdict(
            "Learning Alpha",
            "insufficient_data",
            0,
            None,
            None,
            None,
            "insufficient",
            "No structured learning/memo/guard events in the audit window.",
            {"event_count": 0, "raw_event_count": 0, "unique_event_count": 0},
        )

    before_avgs: list[float] = []
    after_avgs: list[float] = []
    raw_after_count = 0
    unique_after: dict[str, float] = {}
    unique_before: dict[str, float] = {}
    unique_control_after: dict[str, float] = {}
    unique_control_before: dict[str, float] = {}
    after_membership: dict[str, int] = defaultdict(int)
    usable_events = 0
    did_events = 0
    total_events_with_group = 0

    for event in events:
        group = event["group"]
        if not group:
            continue
        total_events_with_group += 1
        before, after, control_before, control_after = _trade_pnl_before_after(db, event["event_at"], days, group)
        raw_after_count += len(after)
        for key, value in before.items():
            unique_before.setdefault(key, value)
        for key, value in after.items():
            unique_after.setdefault(key, value)
            after_membership[key] += 1
        for key, value in control_before.items():
            unique_control_before.setdefault(key, value)
        for key, value in control_after.items():
            unique_control_after.setdefault(key, value)
        if len(before) < min_sample_size or len(after) < min_sample_size:
            continue
        before_avgs.append(mean(before.values()))
        after_avgs.append(mean(after.values()))
        usable_events += 1
        if len(control_before) >= min_sample_size and len(control_after) >= min_sample_size:
            did_events += 1

    if usable_events == 0:
        return _verdict(
            "Learning Alpha",
            "insufficient_data",
            len(unique_after),
            _safe_mean(before_avgs),
            _safe_mean(after_avgs),
            None,
            "insufficient",
            "Learning events exist, but there are not enough before/after same-pattern closed trades to judge behavior change.",
            {
                "event_count": len(events),
                "raw_event_count": len(raw_events),
                "unique_event_count": len(events),
                "duplicate_event_count": len(raw_events) - len(events),
                "events_with_group": total_events_with_group,
                "usable_events": 0,
                "raw_after_sample_size": raw_after_count,
                "unique_after_sample_size": len(unique_after),
            },
            evidence_type="same_pattern_before_after",
            causal_grade="insufficient",
        )

    if (
        len(unique_control_before) >= min_sample_size
        and len(unique_control_after) >= min_sample_size
    ):
        observed = mean(unique_after.values()) - mean(unique_before.values())
        base = mean(unique_control_after.values()) - mean(unique_control_before.values())
        reason_basis = "difference-in-differences against contemporaneous non-matching trade controls"
        evidence_type = "difference_in_differences"
        causal_grade = "moderate_quasi_causal"
    else:
        observed = mean(unique_after.values())
        base = mean(unique_before.values())
        reason_basis = "same-pattern before/after comparison without enough controls"
        evidence_type = "same_pattern_before_after"
        causal_grade = "weak_before_after"
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Learning Alpha",
        status,
        len(unique_after),
        base,
        observed,
        delta,
        _confidence_label(len(unique_after), min_sample_size),
        f"Learning-treated patterns {'improved' if delta > 0 else 'worsened' if delta < 0 else 'matched'} using {reason_basis}.",
        {
            "event_count": len(events),
            "raw_event_count": len(raw_events),
            "unique_event_count": len(events),
            "duplicate_event_count": len(raw_events) - len(events),
            "events_with_group": total_events_with_group,
            "usable_events": usable_events,
            "did_events": did_events,
            "raw_after_sample_size": raw_after_count,
            "unique_after_sample_size": len(unique_after),
            "unique_before_sample_size": len(unique_before),
            "unique_control_after_sample_size": len(unique_control_after),
            "unique_control_before_sample_size": len(unique_control_before),
            "overlapping_after_sample_size": sum(1 for count in after_membership.values() if count > 1),
        },
        evidence_type=evidence_type,
        causal_grade=causal_grade,
    )


def _serenity_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    causal = _causal_control_alpha(db, "serenity", "Serenity Alpha", cutoff, min_sample_size)
    if causal is not None:
        return causal

    rows = db.execute(
        "SELECT symbol, direction, created_at, asset_class FROM serenity_opportunities "
        "WHERE datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    observed_returns: list[float] = []
    baseline_returns: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        equity_like = (row["asset_class"] or "").lower() == "equity" or "/" not in (row["symbol"] or "")
        for horizon in ("1d", "3d", "7d"):
            ret = _forward_return_from_ohlcv(db, row["symbol"], row["created_at"], horizon, row["direction"] or "long")
            if ret is None:
                continue
            observed_returns.append(ret)
            base = _deterministic_baseline_returns(
                db,
                entry_at=row["created_at"],
                horizon=horizon,
                direction=row["direction"] or "long",
                exclude_symbol=row["symbol"],
                seed=f"serenity:{row['created_at']}:{horizon}:{row['symbol']}",
                equity_like=equity_like,
            )
            baseline_returns.extend(base)
            h = by_horizon.setdefault(horizon, {"selected": [], "baseline": []})
            h["selected"].append(ret)
            h["baseline"].extend(base)

    if len(observed_returns) < min_sample_size or len(baseline_returns) < min_sample_size:
        return _verdict(
            "Serenity Alpha",
            "insufficient_data",
            len(observed_returns),
            _safe_mean(baseline_returns),
            _safe_mean(observed_returns),
            None,
            "insufficient",
            "Not enough Serenity opportunities with 1d/3d/7d forward returns and an observed baseline.",
            {"baseline_sample_size": len(baseline_returns), "horizons": _horizon_details(by_horizon)},
        )

    observed = mean(observed_returns)
    base = mean(baseline_returns)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Serenity Alpha",
        status,
        len(observed_returns),
        base,
        observed,
        delta,
        _confidence_label(len(observed_returns), min_sample_size),
        f"Serenity opportunities {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} observed same-period baselines.",
        {"baseline_sample_size": len(baseline_returns), "horizons": _horizon_details(by_horizon)},
        evidence_type="reconstructed_ohlcv_baseline",
        causal_grade="weak_reconstructed",
    )


def _learning_events(db, cutoff: str) -> list[dict]:
    events: list[dict] = []
    lesson_rows = db.execute(
        "SELECT lesson_id AS id, source_type, source_id, evidence_json, recommendation_json, "
        "COALESCE(activated_at, created_at) AS event_at "
        "FROM ps_lessons "
        "WHERE status IN ('accepted', 'applied') "
        "AND datetime(COALESCE(activated_at, created_at)) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    for row in lesson_rows:
        evidence = _parse_json(row["evidence_json"])
        recommendation = _parse_json(row["recommendation_json"])
        events.append({
            "id": row["id"],
            "event_kind": "lesson",
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "event_at": row["event_at"],
            "group": _extract_learning_group(evidence, recommendation),
            "action": recommendation.get("action"),
        })

    knowledge_rows = db.execute(
        "SELECT id, item_type, tags, created_at AS event_at FROM knowledge_items "
        "WHERE item_type IN ('trading_memo', 'trading_guard') "
        "AND datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    for row in knowledge_rows:
        tags = _parse_json(row["tags"])
        events.append({
            "id": row["id"],
            "event_kind": row["item_type"],
            "source_type": row["item_type"],
            "source_id": row["id"],
            "event_at": row["event_at"],
            "group": _extract_learning_group(tags),
            "action": tags.get("action") if isinstance(tags, dict) else None,
        })
    return events


def _dedupe_learning_events(events: list[dict]) -> list[dict]:
    """Collapse repeated artifact rows into one scope/action cohort.

    Prose, artifact ids, and retry timestamps are intentionally excluded. The
    earliest activation anchors the weak before/after window; later duplicates
    cannot reset the clock or multiply the same closed positions.
    """
    by_fingerprint: dict[str, dict] = {}
    for event in events:
        payload = {
            "event_kind": event.get("event_kind"),
            "source_type": event.get("source_type"),
            "group": event.get("group") or {},
            "action": event.get("action"),
        }
        fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        current = by_fingerprint.get(fingerprint)
        if current is None or str(event.get("event_at") or "") < str(current.get("event_at") or ""):
            by_fingerprint[fingerprint] = event
    return sorted(by_fingerprint.values(), key=lambda row: str(row.get("event_at") or ""))


def _trade_pnl_before_after(db, event_at: str, days: int, group: dict) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    from aletheia.learning.outcomes import assess_realized_position

    event_dt = _parse_dt(event_at)
    if event_dt is None:
        return {}, {}, {}, {}
    before_start = (event_dt - timedelta(days=days)).isoformat()
    after_end = _utcnow().isoformat()
    rows = db.execute(
        "SELECT p.id AS position_id, p.symbol, p.direction, p.realized_pnl, p.entry_price, p.quantity, "
        "p.close_reason, p.closed_at, p.close_category, p.exit_price_source, "
        "p.exit_fill_verified, p.outcome_eligible, p.outcome_exclusion_reason, "
        "p.outcome_semantics_version, o.scanner_family "
        "FROM positions p "
        "LEFT JOIN trade_proposals tp ON tp.id = p.proposal_id "
        "LEFT JOIN opportunities o ON o.id = tp.opportunity_id "
        "WHERE p.status = 'closed' "
        "AND p.closed_at IS NOT NULL "
        "AND datetime(p.closed_at) >= datetime(?) "
        "AND datetime(p.closed_at) <= datetime(?)",
        (before_start, after_end),
    ).fetchall()

    before: dict[str, float] = {}
    after: dict[str, float] = {}
    control_before: dict[str, float] = {}
    control_after: dict[str, float] = {}
    for row in rows:
        row_dict = dict(row)
        outcome = assess_realized_position(row_dict)
        if not outcome.eligible or outcome.net_return_pct is None or not outcome.outcome_key:
            continue
        pnl_pct = outcome.net_return_pct
        closed_at = _parse_dt(row["closed_at"])
        if closed_at is None:
            continue
        matches = _matches_learning_group(row, group)
        position_id = outcome.outcome_key
        if matches and closed_at < event_dt:
            before[position_id] = pnl_pct
        elif matches:
            after[position_id] = pnl_pct
        elif closed_at < event_dt:
            control_before[position_id] = pnl_pct
        else:
            control_after[position_id] = pnl_pct
    return before, after, control_before, control_after


def _matches_learning_group(row, group: dict) -> bool:
    for key in ("scanner_family", "direction", "symbol"):
        expected = group.get(key)
        if expected and str(row[key] or "").lower() != str(expected).lower():
            return False
    bucket = group.get("failure_bucket") or group.get("close_reason")
    if bucket and str(row["close_reason"] or "").lower() != str(bucket).lower():
        return False
    return True


def _extract_learning_group(*objects: Any) -> dict:
    group = {
        "scanner_family": None,
        "direction": None,
        "symbol": None,
        "failure_bucket": None,
        "close_reason": None,
    }
    for key in list(group):
        for obj in objects:
            value = _find_key(obj, key)
            if value is not None:
                group[key] = value
                break
    if group["close_reason"] and not group["failure_bucket"]:
        group["failure_bucket"] = group["close_reason"]
    return {k: v for k, v in group.items() if v not in (None, "")}


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def _deterministic_baseline_returns(
    db,
    *,
    entry_at: str,
    horizon: str,
    direction: str,
    exclude_symbol: str,
    seed: str,
    equity_like: bool,
    sample_size: int = 5,
) -> list[float]:
    symbols = _baseline_symbols(db, exclude_symbol=exclude_symbol, equity_like=equity_like)
    ranked = sorted(symbols, key=lambda s: hashlib.sha256(f"{seed}:{s}".encode("utf-8")).hexdigest())
    returns: list[float] = []
    for symbol in ranked:
        ret = _forward_return_from_ohlcv(db, symbol, entry_at, horizon, direction)
        if ret is None:
            continue
        returns.append(ret)
        if len(returns) >= sample_size:
            break
    return returns


def _causal_control_alpha(db, layer: str, name: str, cutoff: str, min_sample_size: int) -> dict | None:
    rows = db.execute(
        "SELECT * FROM alpha_control_observations "
        "WHERE layer = ? AND datetime(entry_ts) >= datetime(?) "
        "ORDER BY entry_ts ASC",
        (layer, cutoff),
    ).fetchall()
    if not rows:
        return None

    selected: list[float] = []
    controls: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        ret = _control_observation_return(db, row)
        if ret is None:
            continue
        horizon = row["horizon"]
        h = by_horizon.setdefault(horizon, {"selected": [], "baseline": []})
        if row["role"] == "selected":
            selected.append(ret)
            h["selected"].append(ret)
        elif row["role"] == "control":
            controls.append(ret)
            h["baseline"].append(ret)

    if len(selected) < min_sample_size or len(controls) < min_sample_size:
        return _verdict(
            name,
            "insufficient_data",
            len(selected),
            _safe_mean(controls),
            _safe_mean(selected),
            None,
            "insufficient",
            "Prospective control cohorts exist, but not enough selected/control outcomes have matured.",
            {"baseline_sample_size": len(controls), "horizons": _horizon_details(by_horizon)},
            evidence_type="prospective_control_cohort",
            causal_grade="insufficient",
        )

    observed = mean(selected)
    base = mean(controls)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        name,
        status,
        len(selected),
        base,
        observed,
        delta,
        _confidence_label(len(selected), min_sample_size),
        f"{name} selected observations {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} prospectively logged same-time controls.",
        {"baseline_sample_size": len(controls), "horizons": _horizon_details(by_horizon)},
        evidence_type="prospective_control_cohort",
        causal_grade="strong_prospective_control",
    )


def _control_observation_return(db, row) -> float | None:
    outcome_key = row["outcome_candidate_key"]
    if not outcome_key:
        return None
    outcome = db.execute(
        "SELECT net_return_pct FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon=? AND status='settled' "
        "AND net_return_pct IS NOT NULL LIMIT 1",
        (outcome_key, row["horizon"]),
    ).fetchone()
    return float(outcome["net_return_pct"]) if outcome else None


def _exit_price_from_ohlcv(db, symbol: str, due_at: str) -> float | None:
    due_dt = _parse_dt(due_at)
    if due_dt is None:
        return None
    target_ms = int(due_dt.timestamp() * 1000)
    row = db.execute(
        "SELECT close FROM market_ohlcv "
        "WHERE symbol = ? AND ts >= ? ORDER BY ts ASC LIMIT 1",
        (symbol, target_ms),
    ).fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


def _baseline_symbols(db, *, exclude_symbol: str, equity_like: bool) -> list[str]:
    registry = db.execute(
        "SELECT DISTINCT symbol FROM universe_registry "
        "WHERE status = 'active' AND COALESCE(is_blacklisted, 0) = 0"
    ).fetchall()
    symbols = {r["symbol"] for r in registry if r["symbol"]}
    ohlcv = db.execute("SELECT DISTINCT symbol FROM market_ohlcv").fetchall()
    symbols.update(r["symbol"] for r in ohlcv if r["symbol"])
    symbols.discard(exclude_symbol)
    if equity_like:
        filtered = {s for s in symbols if "/" not in s}
        if filtered:
            symbols = filtered
    return sorted(symbols)


def _forward_return_from_ohlcv(db, symbol: str, entry_at: str, horizon: str, direction: str) -> float | None:
    entry_dt = _parse_dt(entry_at)
    seconds = _HORIZON_SECONDS.get(horizon)
    if entry_dt is None or seconds is None:
        return None
    entry_ms = int(entry_dt.timestamp() * 1000)
    exit_ms = int((entry_dt.timestamp() + seconds) * 1000)
    timeframes = ("1h", "1d") if horizon in {"1h", "4h", "24h"} else ("1d", "1h")
    for timeframe in timeframes:
        entry = db.execute(
            "SELECT close FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = ? AND ts >= ? "
            "ORDER BY ts ASC LIMIT 1",
            (symbol, timeframe, entry_ms),
        ).fetchone()
        exit_row = db.execute(
            "SELECT close FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = ? AND ts >= ? "
            "ORDER BY ts ASC LIMIT 1",
            (symbol, timeframe, exit_ms),
        ).fetchone()
        if not entry or not exit_row:
            continue
        entry_price = float(entry["close"] or 0)
        exit_price = float(exit_row["close"] or 0)
        if entry_price <= 0 or exit_price <= 0:
            continue
        raw = (exit_price - entry_price) / entry_price * 100.0
        return _directional_return(raw, direction)
    return None


def _position_pnl_pct(row) -> float | None:
    entry = float(row["entry_price"] or 0)
    qty = float(row["quantity"] or 0)
    pnl = float(row["realized_pnl"] or 0)
    notional = entry * qty
    if notional <= 0:
        return None
    return pnl / notional * 100.0


def _directional_return(raw_return_pct: float, direction: str | None) -> float:
    value = float(raw_return_pct)
    return -value if (direction or "").lower() == "short" else value


def _main_bottleneck(verdicts: dict) -> dict:
    candidates = [
        (key, item)
        for key, item in verdicts.items()
        if item["verdict"] != "insufficient_data" and item.get("delta") is not None
    ]
    negative = [(key, item) for key, item in candidates if item["verdict"] == "negative"]
    if negative:
        key, item = min(negative, key=lambda kv: kv[1]["delta"])
        return {
            "layer": item["name"],
            "summary": f"{item['name']} is the most likely bottleneck: {item['reason']}",
            "next_action": _next_action(key),
            "suspected_modules": item.get("suspected_modules", []),
        }
    if candidates:
        key, item = min(candidates, key=lambda kv: kv[1]["delta"])
        return {
            "layer": item["name"],
            "summary": f"No negative layer detected; weakest measured layer is {item['name']}: {item['reason']}",
            "next_action": "Keep collecting data and inspect the weakest measured layer before changing trading behavior.",
            "suspected_modules": item.get("suspected_modules", []),
        }
    return {
        "layer": "insufficient_data",
        "summary": "No layer has enough measured data to identify a bottleneck.",
        "next_action": "Collect more settled outcomes and resolved shadow trades before changing scanner, ranker, prompt, or learning logic.",
        "suspected_modules": [],
    }


def _next_action(key: str) -> str:
    return {
        "universe": "Do not tune the decision prompt first. Inspect universe scanner selection and its tradable-symbol baseline.",
        "ranker": "Ranker top1 underperforms lower-ranked candidates. Inspect scoring weights, penalties, and eligibility filters.",
        "system_operator": "Do not change scanner. Improve System Operator decision prompt, model, and context selection.",
        "learning": "Learning artifacts are not reducing repeated losses. Inspect memo/guard consumption before adding more memories.",
        "serenity": "Serenity opportunities underperform baseline. Inspect the lens, evidence gating, and equity opportunity criteria.",
    }.get(key, "Collect more data before changing trading behavior.")


def _verdict(
    name: str,
    status: str,
    sample_size: int,
    baseline: float | None,
    observed: float | None,
    delta: float | None,
    confidence_label: str,
    reason: str,
    details: dict | None = None,
    evidence_type: str = "observational",
    causal_grade: str = "weak_observational",
) -> dict:
    contribution_verdict = (
        status
        if causal_grade == "strong_prospective_control" and status != "insufficient_data"
        else "insufficient_data"
    )
    return {
        "name": name,
        "verdict": status,
        "sample_size": int(sample_size or 0),
        "baseline": _round_or_none(baseline),
        "observed": _round_or_none(observed),
        "delta": _round_or_none(delta),
        "confidence_label": confidence_label,
        "evidence_type": evidence_type,
        "causal_grade": causal_grade,
        "contribution_verdict": contribution_verdict,
        "measurement_status": (
            "measured_contribution"
            if contribution_verdict != "insufficient_data"
            else "directional_evidence_only"
            if status != "insufficient_data"
            else "not_measured"
        ),
        "suspected_modules": _MODULES_BY_LAYER.get(name, []),
        "reason": reason,
        "details": details or {},
    }


def _status_from_delta(delta: float) -> str:
    if delta > ALPHA_THRESHOLD_PCT:
        return "positive"
    if delta < -ALPHA_THRESHOLD_PCT:
        return "negative"
    return "neutral"


def _confidence_label(sample_size: int, min_sample_size: int) -> str:
    if sample_size < min_sample_size:
        return "insufficient"
    if sample_size >= 30:
        return "high"
    if sample_size >= 10:
        return "medium"
    return "low"


def _horizon_details(groups: dict[str, dict[str, list[float]]]) -> dict:
    result = {}
    for horizon, values in groups.items():
        selected = values.get("selected", [])
        baseline = values.get("baseline", [])
        result[horizon] = {
            "sample_size": len(selected),
            "baseline_sample_size": len(baseline),
            "observed": _round_or_none(_safe_mean(selected)),
            "baseline": _round_or_none(_safe_mean(baseline)),
            "delta": _round_or_none(
                (_safe_mean(selected) - _safe_mean(baseline))
                if selected and baseline else None
            ),
        }
    return result


def _safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _round_or_none(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _fmt_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}%"
    return str(value)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return {}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_days(days: int) -> int:
    if days <= 0:
        return 30
    if days not in {7, 14, 30}:
        return days
    return days
