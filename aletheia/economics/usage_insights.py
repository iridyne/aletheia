"""Read-only, aggregate-only projection backing the Usage insights page."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from typing import Any, Iterable

from aletheia.economics.agent_roles import role_for_logical_agent
from aletheia.economics.tracing import estimate_llm_cost, normalize_usage
from aletheia.model_provider.fallback import get_model_health_snapshot
from aletheia.storage.sqlite import readonly_db


_WINDOW_DAYS = {1, 7, 30, 90}
_GROUPS = {"summary", "operations", "analysis"}
_ERROR_CATEGORY_LIMIT = 8
_BASELINE_MIN_FULL_DAYS = 7
_ANOMALY_Z_THRESHOLD = 3.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _as_utc(parsed)


def _count(value: object) -> int:
    return int(value or 0)


def _number(value: object) -> float:
    return round(float(value or 0), 12)


def _pct(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 4) if denominator else None


def _status(denominator: int, numerator: int) -> str:
    if denominator == 0 or numerator == 0:
        return "not_available"
    return "available" if numerator == denominator else "partial"


def _section(status: str, reason: str | None, **fields: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **fields}


def _table_names(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _window_sql(column: str) -> str:
    return f"replace({column},' ','T') >= replace(?,' ','T') AND replace({column},' ','T') <= replace(?,' ','T')"


def _query_rows(db: sqlite3.Connection, sql: str, params: Iterable[object]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]


def _metadata(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _cost_known(row: dict[str, Any]) -> bool:
    return row.get("estimated_cost_usd") is not None and row.get("cost_status") == "known"


def _cost_estimated(row: dict[str, Any]) -> bool:
    return row.get("estimated_cost_usd") is not None and row.get("cost_status") == "estimated"


def _cost_priced(row: dict[str, Any]) -> bool:
    return _cost_known(row) or _cost_estimated(row)


def _cost_value(row: dict[str, Any]) -> float:
    return _number(row.get("estimated_cost_usd")) if _cost_known(row) else 0.0


def _estimated_cost_value(row: dict[str, Any]) -> float:
    return _number(row.get("estimated_cost_usd")) if _cost_estimated(row) else 0.0


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    value = ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
    return round(value, 3)


def _error_categories(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    counts = Counter(
        _text(row.get("error_type")) or "not_available"
        for row in rows
        if row.get("status") != "success"
    )
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    visible = ordered[:_ERROR_CATEGORY_LIMIT]
    return ([{"error_type": name, "event_count": count} for name, count in visible], sum(count for _, count in ordered[_ERROR_CATEGORY_LIMIT:]))


def _empty(days: int, end: datetime, reason: str) -> dict[str, Any]:
    start = end - timedelta(days=days)
    unavailable = lambda **fields: _section("not_available", reason, **fields)
    axes = [
        {"id": key, "label": label, "status": "not_available", "coverage_pct": None, "reason": reason}
        for key, label in (
            ("consumption", "Consumption"), ("pricing", "Pricing"),
            ("attribution", "Attribution"), ("tools", "Tools"),
            ("reliability", "Reliability"), ("budgets", "Budgets"),
            ("trends", "Trends"), ("research_yield", "Research yield"),
        )
    ]
    return {
        "window": {"days": days, "started_at": start.isoformat(), "ended_at": end.isoformat(), "as_of": end.isoformat(), "timezone": "UTC"},
        "coverage": unavailable(axes=axes),
        "token_mix": unavailable(input_tokens=None, output_tokens=None, total_tokens=None, reasoning_tokens=None, cached_tokens=None, event_count=None),
        "pricing": unavailable(known_cost_usd=None, estimated_cost_usd=None, unknown_event_count=None, pricing_coverage_pct=None, gaps=[]),
        "attribution": unavailable(rows=[]),
        "tools": unavailable(rows=[], timing_coverage_pct=None, pricing_coverage_pct=None),
        "reliability": unavailable(
            logical={},
            physical={},
            current_model_cooldown=_current_model_cooldown(end.isoformat()),
        ),
        "budgets": unavailable(rows=[]),
        "trends": unavailable(
            daily=[],
            baseline=None,
            trace_coverage=unavailable(
                session_count=0,
                traced_session_count=0,
                legacy_or_untraced_session_count=0,
                full_day_count=0,
                trace_covered_full_day_count=0,
                observed_zero_day_count=0,
                unavailable_zero_day_count=0,
            ),
        ),
        "research_yield": unavailable(artifacts=[], lineage=[]),
    }


def _runtime_rows(db: sqlite3.Connection, window: tuple[str, str]) -> list[dict[str, Any]]:
    return _query_rows(
        db,
        "SELECT id,event_type,run_id,opportunity_id,decision_id,proposal_id,call_id,provider,model_or_tool,requested_model,attempt_index,status,started_at,finished_at,latency_ms,usage_json,estimated_cost_usd,cost_status,error_type,metadata_json "
        "FROM runtime_cost_events WHERE " + _window_sql("started_at"),
        window,
    )


def _project_latest_litellm_estimates(
    db: sqlite3.Connection, runtime_rows: list[dict[str, Any]]
) -> None:
    """Estimate otherwise-unknown LLM rows in memory without rewriting history."""
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in _query_rows(
        db,
        "SELECT * FROM pricing_snapshots WHERE source LIKE 'litellm_public_median:%' "
        "ORDER BY datetime(effective_from) DESC,rowid DESC",
        (),
    ):
        snapshots.setdefault((str(snapshot["provider"]), str(snapshot["model"])), snapshot)

    for row in runtime_rows:
        if row.get("event_type") != "llm" or _cost_priced(row):
            continue
        pricing = snapshots.get((str(row.get("provider") or ""), str(row.get("model_or_tool") or "")))
        if pricing is None:
            continue
        try:
            envelope = json.loads(str(row.get("usage_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        normalized = envelope.get("normalized") if isinstance(envelope, dict) else None
        usage = normalized if isinstance(normalized, dict) else normalize_usage(envelope)
        cost, status, _ = estimate_llm_cost(usage, pricing)
        if cost is not None and status == "estimated":
            row["estimated_cost_usd"] = cost
            row["cost_status"] = status


def _token_mix(llm_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    fields = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
    totals: dict[str, float] = {field: 0.0 for field in fields}
    available: dict[str, int] = {field: 0 for field in fields}
    usable_rows = 0
    for row in llm_rows:
        try:
            envelope = json.loads(str(row.get("usage_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            envelope = {}
        normalized = envelope.get("normalized") if isinstance(envelope, dict) else None
        if not isinstance(normalized, dict):
            normalized = normalize_usage(envelope) if isinstance(envelope, dict) else {}
        if any(isinstance(normalized.get(key), (int, float)) and normalized.get(key) >= 0 for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens", "provider_cache_read_input_tokens")):
            usable_rows += 1
        values = {
            "input_tokens": normalized.get("input_tokens"),
            "output_tokens": normalized.get("output_tokens"),
            "reasoning_tokens": normalized.get("reasoning_tokens"),
            "cached_tokens": normalized.get("cached_input_tokens") if normalized.get("cached_input_tokens") is not None else normalized.get("provider_cache_read_input_tokens"),
        }
        for field, value in values.items():
            if isinstance(value, (int, float)) and value >= 0:
                totals[field] += value
                available[field] += 1
    total = len(llm_rows)
    field_coverage = {field: _pct(available[field], total) for field in fields}
    status = _status(total, min(available.values(), default=0))
    reason = None if status == "available" else "No normalized token usage is recorded in this window." if total == 0 or usable_rows == 0 else "Some displayed token fields are absent from one or more LLM events."
    return (
        _section(
            status,
            reason,
            input_tokens=_number(totals["input_tokens"]) if available["input_tokens"] else None,
            output_tokens=_number(totals["output_tokens"]) if available["output_tokens"] else None,
            total_tokens=(
                _number(totals["input_tokens"] + totals["output_tokens"])
                if available["input_tokens"] and available["output_tokens"]
                else None
            ),
            reasoning_tokens=_number(totals["reasoning_tokens"]) if available["reasoning_tokens"] else None,
            cached_tokens=_number(totals["cached_tokens"]) if available["cached_tokens"] else None,
            event_count=total or None,
            field_coverage_pct=field_coverage,
        ),
        usable_rows,
    )


def _pricing(runtime_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    known = estimated = 0.0
    priced = 0
    gaps: Counter[tuple[str, str, str]] = Counter()
    for row in runtime_rows:
        value = _number(row.get("estimated_cost_usd")) if _cost_priced(row) else 0.0
        if _cost_priced(row):
            priced += 1
            if row.get("cost_status") == "known":
                known += value
            else:
                estimated += value
        else:
            gaps[(str(row.get("event_type") or "not_available"), _text(row.get("provider")) or "not_available", _text(row.get("model_or_tool")) or "not_available")] += 1
    total = len(runtime_rows)
    status = _status(total, priced)
    reason = None if status == "available" else "No cost events are recorded in this window." if total == 0 else "Some cost events have no effective price record."
    return (
        _section(
            status,
            reason,
            known_cost_usd=_number(known) if priced else None,
            estimated_cost_usd=_number(estimated) if priced else None,
            unknown_event_count=total - priced if total else None,
            pricing_coverage_pct=_pct(priced, total),
            gaps=[{"event_type": event_type, "provider": provider, "model_or_tool": model, "event_count": count} for (event_type, provider, model), count in sorted(gaps.items())],
        ),
        priced,
    )


def _attribution(db: sqlite3.Connection, llm_rows: list[dict[str, Any]], tables: set[str]) -> tuple[dict[str, Any], int]:
    run_names = {
        str(row["id"]): row.get("agent_name")
        for row in _query_rows(db, "SELECT id,agent_name FROM agent_runs", ())
    } if "agent_runs" in tables else {}
    buckets: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

    def add(axis: str, values: dict[str, str], row: dict[str, Any]) -> None:
        key = (axis, tuple(sorted(values.items())))
        bucket = buckets.setdefault(key, {"axis": axis, **values, "event_count": 0, "known_cost_usd": 0.0, "unknown_cost_event_count": 0})
        bucket["event_count"] += 1
        if _cost_known(row):
            bucket["known_cost_usd"] += _cost_value(row)
        elif _cost_estimated(row):
            bucket.setdefault("estimated_cost_usd", 0.0)
            bucket["estimated_cost_usd"] += _estimated_cost_value(row)
        else:
            bucket["unknown_cost_event_count"] += 1

    attributable = 0
    for row in llm_rows:
        metadata = _metadata(row.get("metadata_json"))
        run_id = _text(row.get("run_id"))
        logical_agent = _text(metadata.get("logical_agent"))
        direct_values = {
            key: value for key in ("evaluation_id", "trigger")
            if (value := _text(metadata.get(key))) is not None
        }
        for key in ("opportunity_id", "decision_id", "proposal_id"):
            value = _text(row.get(key)) or _text(metadata.get(key))
            if value is not None:
                direct_values[key] = value
        has_direct = bool(direct_values)
        if run_id or logical_agent or has_direct:
            attributable += 1
        if run_id:
            add("agent_run", {"agent_run_id": run_id, "agent_name": _text(run_names.get(run_id)) or "not_available"}, row)
        if logical_agent:
            add("logical_agent", {"logical_agent": logical_agent}, row)
            add("role", {"role": role_for_logical_agent(logical_agent).value}, row)
        if has_direct:
            add("direct_tags", direct_values, row)
    rows = []
    for bucket in buckets.values():
        bucket["known_cost_usd"] = _number(bucket["known_cost_usd"])
        if "estimated_cost_usd" in bucket:
            bucket["estimated_cost_usd"] = _number(bucket["estimated_cost_usd"])
        rows.append(bucket)
    rows.sort(key=lambda row: (str(row["axis"]), json.dumps(row, sort_keys=True, default=str)))
    total = len(llm_rows)
    status = _status(total, attributable)
    reason = None if status == "available" else "No LLM usage is recorded in this window." if total == 0 else "Some LLM events have no durable run, logical-agent, or direct attribution tag."
    return _section(status, reason, rows=rows), attributable


def _tool_profiles(db: sqlite3.Connection, tables: set[str], as_of: str) -> dict[str, list[dict[str, Any]]]:
    if "tool_cost_profiles" not in tables:
        return {}
    profiles = _query_rows(
        db,
        "SELECT tool_name,effective_from,usd_per_call FROM tool_cost_profiles WHERE replace(effective_from,' ','T') <= replace(?,' ','T') ORDER BY tool_name,replace(effective_from,' ','T')",
        (as_of,),
    )
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        result[str(profile["tool_name"])].append(profile)
    return result


def _effective_tool_price(profiles: dict[str, list[dict[str, Any]]], tool_name: str, at: str) -> float | None:
    candidate = None
    for profile in profiles.get(tool_name, []):
        if str(profile.get("effective_from") or "") <= at:
            candidate = profile
    return _number(candidate.get("usd_per_call")) if candidate is not None else None


def _tools(db: sqlite3.Connection, tables: set[str], runtime_rows: list[dict[str, Any]], window: tuple[str, str], as_of: str) -> tuple[dict[str, Any], int, int]:
    if "agent_tool_calls" not in tables:
        return _section("not_available", "The agent tool-call ledger is not available.", rows=[], timing_coverage_pct=None, pricing_coverage_pct=None), 0, 0
    columns = _columns(db, "agent_tool_calls")
    duration = "duration_ms" if "duration_ms" in columns else "NULL AS duration_ms"
    calls = _query_rows(
        db,
        "SELECT id,tool_name,tool_class,permitted,created_at," + duration + " FROM agent_tool_calls WHERE " + _window_sql("created_at"),
        window,
    )
    runtime_by_call = {str(row.get("call_id")): row for row in runtime_rows if row.get("event_type") == "tool" and row.get("call_id")}
    profiles = _tool_profiles(db, tables, as_of)
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    priced_calls = timed_calls = 0
    for call in calls:
        name = _text(call.get("tool_name")) or "not_available"
        tool_class = _text(call.get("tool_class")) or "not_available"
        bucket = buckets.setdefault((name, tool_class), {"tool_name": name, "tool_class": tool_class, "event_count": 0, "permitted_count": 0, "denied_count": 0, "priced_event_count": 0, "known_cost_usd": 0.0, "timed_event_count": 0, "durations": []})
        bucket["event_count"] += 1
        bucket["permitted_count" if call.get("permitted") else "denied_count"] += 1
        event = runtime_by_call.get("tool_" + str(call["id"]))
        if event is not None and _cost_known(event):
            price = _cost_value(event)
        elif call.get("permitted"):
            price = _effective_tool_price(profiles, name, str(call.get("created_at") or ""))
        else:
            price = 0.0
        if price is not None:
            priced_calls += 1
            bucket["priced_event_count"] += 1
            bucket["known_cost_usd"] += price
        duration_ms = call.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
            timed_calls += 1
            bucket["timed_event_count"] += 1
            bucket["durations"].append(float(duration_ms))
    rows = []
    for bucket in buckets.values():
        total = bucket.pop("event_count")
        durations = bucket.pop("durations")
        rows.append({
            **bucket,
            "event_count": total,
            "known_cost_usd": _number(bucket["known_cost_usd"]),
            "pricing_coverage_pct": _pct(bucket["priced_event_count"], total),
            "timing_coverage_pct": _pct(bucket["timed_event_count"], total),
            "p50_duration_ms": _quantile(durations, 0.5),
            "p95_duration_ms": _quantile(durations, 0.95),
        })
    rows.sort(key=lambda row: (str(row["tool_name"]), str(row["tool_class"])))
    total = len(calls)
    if not total:
        return _section("not_available", "No tool calls are recorded in this window.", rows=[], timing_coverage_pct=None, pricing_coverage_pct=None), 0, 0
    status = "available" if priced_calls == total and timed_calls == total else "partial"
    reason = None if status == "available" else "The nullable duration field is absent or incomplete." if "duration_ms" not in columns or timed_calls < total else "Some tool calls have no effective price record."
    return _section(status, reason, rows=rows, timing_coverage_pct=_pct(timed_calls, total), pricing_coverage_pct=_pct(priced_calls, total)), priced_calls, timed_calls


def _logical_reliability(db: sqlite3.Connection, tables: set[str], window: tuple[str, str]) -> dict[str, Any]:
    if "llm_inference_calls" not in tables:
        return {"status": "not_available", "reason": "The logical-inference ledger is not available."}
    rows = _query_rows(db, "SELECT logical_call_id,status,parse_valid,schema_valid,attempt_count,retry_count,fallback_used,started_at,finished_at,error_type FROM llm_inference_calls WHERE " + _window_sql("started_at"), window)
    durations = []
    for row in rows:
        started, finished = _iso(row.get("started_at")), _iso(row.get("finished_at"))
        if started and finished and finished >= started:
            durations.append((finished - started).total_seconds() * 1000)
    total = len(rows)
    errors, other = _error_categories(rows)
    return {
        "status": _status(total, len(durations)),
        "reason": None if total and len(durations) == total else "No logical inferences are recorded in this window." if not total else "Some logical inferences lack a completed duration.",
        "call_count": total,
        "success_count": sum(row.get("status") == "success" for row in rows),
        "error_count": sum(row.get("status") == "error" for row in rows),
        "retry_count": sum(_count(row.get("retry_count")) for row in rows),
        "fallback_count": sum(row.get("fallback_used") == 1 for row in rows),
        "parse_valid_count": sum(row.get("parse_valid") == 1 for row in rows),
        "parse_invalid_count": sum(row.get("parse_valid") == 0 for row in rows),
        "parse_not_available_count": sum(row.get("parse_valid") is None for row in rows),
        "schema_valid_count": sum(row.get("schema_valid") == 1 for row in rows),
        "schema_invalid_count": sum(row.get("schema_valid") == 0 for row in rows),
        "schema_not_available_count": sum(row.get("schema_valid") is None for row in rows),
        "timing_coverage_pct": _pct(len(durations), total),
        "p50_duration_ms": _quantile(durations, 0.5),
        "p95_duration_ms": _quantile(durations, 0.95),
        "error_categories": errors,
        "other_error_count": other,
    }


def _physical_reliability(llm_rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(row["latency_ms"]) for row in llm_rows if isinstance(row.get("latency_ms"), (int, float)) and row["latency_ms"] >= 0]
    providers: dict[str, set[str]] = defaultdict(set)
    for row in llm_rows:
        call_id, provider = _text(row.get("call_id")), _text(row.get("provider"))
        if call_id and provider:
            providers[call_id].add(provider)
    total = len(llm_rows)
    errors, other = _error_categories(llm_rows)
    return {
        "status": _status(total, len(durations)),
        "reason": None if total and len(durations) == total else "No physical LLM attempts are recorded in this window." if not total else "Some physical attempts lack measured latency.",
        "attempt_count": total,
        "logical_call_count": len({_text(row.get("call_id")) for row in llm_rows if _text(row.get("call_id"))}),
        "success_count": sum(row.get("status") == "success" for row in llm_rows),
        "failure_count": sum(row.get("status") != "success" for row in llm_rows),
        "retry_attempt_count": sum(_count(row.get("attempt_index")) > 0 for row in llm_rows),
        "fallback_call_count": sum(len(values) > 1 for values in providers.values()),
        "timing_coverage_pct": _pct(len(durations), total),
        "p50_duration_ms": _quantile(durations, 0.5),
        "p95_duration_ms": _quantile(durations, 0.95),
        "error_categories": errors,
        "other_error_count": other,
    }


def _current_model_cooldown(as_of: str) -> dict[str, Any]:
    """Expose only sanitized, in-process health facts; they are not history."""
    rows = [
        {
            "provider": item["provider"],
            "model": item["model"],
            "cooling_down": item["cooling_down"],
            "cooldown_remaining_sec": item["cooldown_remaining_sec"],
            "consecutive_failures": item["consecutive_failures"],
            "last_failure_at": item["last_failure_at"],
            "last_success_at": item["last_success_at"],
        }
        for item in get_model_health_snapshot()
    ]
    status = "available" if rows else "not_available"
    reason = "Current in-process model health has no observed models." if not rows else "Current in-process state; it is not a historical cooldown ledger."
    return _section(
        status,
        reason,
        current=True,
        fact_scope="current_in_process",
        as_of=as_of,
        rows=rows,
        cooling_down_count=sum(row["cooling_down"] for row in rows),
    )


def _reliability(db: sqlite3.Connection, tables: set[str], llm_rows: list[dict[str, Any]], window: tuple[str, str], as_of: str) -> tuple[dict[str, Any], int]:
    logical = _logical_reliability(db, tables, window)
    physical = _physical_reliability(llm_rows)
    statuses = {logical["status"], physical["status"]}
    status = "available" if statuses == {"available"} else "not_available" if statuses == {"not_available"} else "partial"
    reason = None if status == "available" else "No logical or physical inference facts are recorded in this window." if status == "not_available" else "Logical and physical reliability coverage differ."
    return _section(
        status,
        reason,
        logical=logical,
        physical=physical,
        current_model_cooldown=_current_model_cooldown(as_of),
    ), sum(isinstance(row.get("latency_ms"), (int, float)) for row in llm_rows)


def _policy_events(policy: dict[str, Any], events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    scope, subject = str(policy.get("scope") or ""), _text(policy.get("subject"))
    if str(policy.get("window")) == "session":
        return None, "Session budgets cannot be reconstructed from historical rows."
    if scope == "system":
        return events, None
    if scope == "agent_run" and subject:
        return [row for row in events if _text(row.get("run_id")) == subject], None
    if scope == "provider" and subject:
        return [row for row in events if _text(row.get("provider")) == subject], None
    if scope == "logical_agent" and subject:
        return [row for row in events if _text(_metadata(row.get("metadata_json")).get("logical_agent")) == subject], None
    if scope == "provider_tier":
        return None, "Provider tiers are not persisted on historical cost events."
    return None, "The budget subject is not recorded on historical cost events."


def _budgets(db: sqlite3.Connection, tables: set[str], end: datetime) -> tuple[dict[str, Any], int, int]:
    if "economics_budgets" not in tables:
        return _section("not_available", "The economics budget ledger is not available.", rows=[]), 0, 0
    policies = _query_rows(db, "SELECT id,scope,subject,usd_cap,window,enabled FROM economics_budgets ORDER BY id", ())
    if not policies:
        return _section("not_available", "No budget policies are declared.", rows=[]), 0, 0
    weekly_start = end - timedelta(days=7)
    events = _runtime_rows(db, (weekly_start.isoformat(), end.isoformat()))
    rows, calculable = [], 0
    for policy in policies:
        duration = timedelta(days=1 if policy.get("window") == "daily" else 7)
        start = end - duration
        window_events, unavailable_reason = _policy_events(policy, [row for row in events if _iso(row.get("started_at")) and _iso(row["started_at"]) >= start])
        base = {"policy_id": policy["id"], "scope": policy["scope"], "subject": policy.get("subject"), "window": policy["window"], "usd_cap": _number(policy["usd_cap"]), "enabled": bool(policy["enabled"]), "window_started_at": start.isoformat()}
        if window_events is None:
            rows.append({**base, "status": "not_available", "reason": unavailable_reason, "known_spent_usd": None, "unknown_event_count": None, "headroom_usd": None, "forecast_status": "not_available", "forecast_reason": unavailable_reason, "forecast_usd": None, "forecast_headroom_usd": None})
            continue
        known = sum(_cost_value(row) for row in window_events)
        estimated = sum(_estimated_cost_value(row) for row in window_events)
        non_known = sum(not _cost_known(row) for row in window_events)
        elapsed = max((end - start).total_seconds(), 1)
        forecast_reason = None
        forecast = None
        if non_known:
            forecast_reason = "Estimated or unknown-price events prevent an honest forecast."
        elif not window_events:
            forecast_reason = "No priced events are recorded for this rolling window."
        else:
            forecast = known * duration.total_seconds() / elapsed
        status = "available" if not non_known else "partial"
        reason = None if not non_known else "Known spend excludes estimated and unknown-price events."
        calculable += 1
        rows.append({**base, "status": status, "reason": reason, "known_spent_usd": _number(known), "estimated_spent_usd": _number(estimated), "unknown_event_count": sum(not _cost_priced(row) for row in window_events), "headroom_usd": _number(float(policy["usd_cap"]) - known) if not non_known else None, "forecast_status": "available" if forecast is not None else "not_available", "forecast_reason": forecast_reason, "forecast_usd": _number(forecast) if forecast is not None else None, "forecast_headroom_usd": _number(float(policy["usd_cap"]) - forecast) if forecast is not None else None})
    status = "available" if calculable == len(policies) else "partial" if calculable else "not_available"
    reason = None if status == "available" else "Some budget scopes cannot be reconstructed from durable history." if calculable else "No budget scope can be reconstructed from durable history."
    return _section(status, reason, rows=rows), calculable, len(policies)


def _traced_intervals(db: sqlite3.Connection, tables: set[str], start: datetime, end: datetime) -> tuple[list[tuple[datetime, datetime]], dict[str, Any]]:
    if "runtime_sessions" not in tables:
        return [], {"status": "not_available", "reason": "The runtime-session ledger is not available.", "session_count": 0, "traced_session_count": 0, "legacy_or_untraced_session_count": 0}
    if "economics_tracing_enabled" not in _columns(db, "runtime_sessions"):
        return [], {"status": "not_available", "reason": "Runtime sessions predate the tracing-state snapshot.", "session_count": 0, "traced_session_count": 0, "legacy_or_untraced_session_count": 0}
    intervals: list[tuple[datetime, datetime]] = []
    session_count = traced = legacy_or_untraced = 0
    try:
        from aletheia.agent_runtime.runtime_session import current_session_id
        active_session_id = current_session_id()
    except Exception:
        active_session_id = None
    for row in _query_rows(db, "SELECT session_id,started_at,ended_at,economics_tracing_enabled FROM runtime_sessions", ()):
        started_at = _iso(row.get("started_at"))
        ended_at = _iso(row.get("ended_at"))
        if ended_at is None:
            if row.get("session_id") != active_session_id:
                legacy_or_untraced += 1
                continue
            ended_at = end
        if started_at is None or ended_at <= start or started_at >= end:
            continue
        session_count += 1
        if row.get("economics_tracing_enabled") == 1:
            intervals.append((max(started_at, start), min(ended_at, end)))
            traced += 1
        else:
            legacy_or_untraced += 1
    status = "available" if traced else "not_available"
    reason = None if traced else "No trace-enabled runtime session overlaps this window." if session_count == 0 else "Overlapping runtime sessions are legacy or economics tracing was disabled."
    return intervals, {"status": status, "reason": reason, "session_count": session_count, "traced_session_count": traced, "legacy_or_untraced_session_count": legacy_or_untraced}


def _covered_seconds(intervals: Iterable[tuple[datetime, datetime]], start: datetime, end: datetime) -> float:
    merged: list[list[datetime]] = []
    for interval_start, interval_end in sorted(intervals):
        interval_start, interval_end = max(interval_start, start), min(interval_end, end)
        if interval_end <= interval_start:
            continue
        if merged and interval_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], interval_end)
        else:
            merged.append([interval_start, interval_end])
    return sum((interval_end - interval_start).total_seconds() for interval_start, interval_end in merged)


def _trends(db: sqlite3.Connection, tables: set[str], runtime_rows: list[dict[str, Any]], start: datetime, end: datetime) -> tuple[dict[str, Any], int, int]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runtime_rows:
        occurred = _iso(row.get("started_at"))
        if occurred:
            by_date[occurred.date().isoformat()].append(row)
    intervals, trace = _traced_intervals(db, tables, start, end)
    days = []
    cursor = start.date()
    while cursor <= end.date():
        day_start = datetime.combine(cursor, datetime.min.time(), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        entries = by_date.get(cursor.isoformat(), [])
        llm_entries = [row for row in entries if row.get("event_type") == "llm"]
        input_tokens = output_tokens = 0
        token_rows = 0
        for entry in llm_entries:
            try:
                envelope = json.loads(str(entry.get("usage_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            normalized = envelope.get("normalized") if isinstance(envelope, dict) else None
            usage = normalized if isinstance(normalized, dict) else normalize_usage(envelope)
            input_value, output_value = usage.get("input_tokens"), usage.get("output_tokens")
            if isinstance(input_value, (int, float)) and isinstance(output_value, (int, float)):
                input_tokens += int(input_value)
                output_tokens += int(output_value)
                token_rows += 1
        priced = sum(_cost_known(row) for row in entries)
        total = len(entries)
        full = start <= day_start and end >= day_end
        traced_seconds = _covered_seconds(intervals, day_start, day_end)
        trace_coverage_pct = round(traced_seconds * 100 / 86_400, 4)
        if total:
            zero_status, zero_reason = "not_applicable", "Recorded runtime cost events exist for this day."
        elif full and traced_seconds >= 86_400:
            zero_status, zero_reason = "observed_zero", "Trace-enabled runtime sessions cover the full UTC day."
        elif traced_seconds:
            zero_status, zero_reason = "partial", "Trace-enabled runtime sessions cover only part of this UTC day."
        elif not full:
            zero_status, zero_reason = "not_available", "This UTC day is only partially inside the requested window."
        else:
            zero_status, zero_reason = "not_available", trace["reason"]
        days.append({"date": cursor.isoformat(), "event_count": total, "llm_event_count": len(llm_entries), "token_covered_event_count": token_rows, "input_tokens": input_tokens if token_rows else None, "output_tokens": output_tokens if token_rows else None, "total_tokens": input_tokens + output_tokens if token_rows else None, "known_cost_usd": _number(sum(_cost_value(row) for row in entries)), "estimated_cost_usd": _number(sum(_estimated_cost_value(row) for row in entries)), "unknown_event_count": total - sum(_cost_priced(row) for row in entries), "pricing_coverage_pct": _pct(sum(_cost_priced(row) for row in entries), total), "known_pricing_coverage_pct": _pct(sum(_cost_known(row) for row in entries), total), "full_window_day": full, "trace_coverage_pct": trace_coverage_pct, "zero_observation_status": zero_status, "zero_observation_reason": zero_reason, "anomaly_eligible": False, "cost_anomaly_z_score": None, "cost_anomaly": None})
        cursor += timedelta(days=1)
    full_days = [row for row in days if row["full_window_day"]]
    baseline_days = [row for row in full_days if row["trace_coverage_pct"] == 100 and row["known_pricing_coverage_pct"] == 100]
    baseline = None
    if len(baseline_days) >= _BASELINE_MIN_FULL_DAYS:
        values = [float(row["known_cost_usd"]) for row in baseline_days]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        deviation = math.sqrt(variance)
        baseline = {"full_day_count": len(values), "mean_known_cost_usd": _number(mean), "stddev_known_cost_usd": _number(deviation), "anomaly_threshold_z": _ANOMALY_Z_THRESHOLD}
        if deviation > 0:
            for row in days:
                if row["full_window_day"] and row["trace_coverage_pct"] == 100 and row["known_pricing_coverage_pct"] == 100:
                    score = (float(row["known_cost_usd"]) - mean) / deviation
                    row["anomaly_eligible"] = True
                    row["cost_anomaly_z_score"] = round(score, 4)
                    row["cost_anomaly"] = abs(score) >= _ANOMALY_Z_THRESHOLD
    event_count = len(runtime_rows)
    priced = sum(_cost_known(row) for row in runtime_rows)
    full_coverage = len(baseline_days) == len(full_days) and bool(full_days)
    if full_coverage and (not event_count or priced == event_count):
        status = "available"
        reason = None if baseline is not None else "A baseline needs at least seven trace-covered full UTC days."
    elif event_count or baseline_days:
        status = "partial"
        reason = "Some full UTC days lack trace-enabled runtime coverage." if not full_coverage else "Some cost events lack prices."
    else:
        status = "not_available"
        reason = trace["reason"]
    observed_zero_days = sum(row["zero_observation_status"] == "observed_zero" for row in full_days)
    trace_status = _status(len(full_days), len(baseline_days))
    trace_reason = None if trace_status == "available" else trace["reason"] if not baseline_days else "Some full UTC days lack trace-enabled runtime coverage."
    trace_coverage = {
        **trace,
        "status": trace_status,
        "reason": trace_reason,
        "full_day_count": len(full_days),
        "trace_covered_full_day_count": len(baseline_days),
        "observed_zero_day_count": observed_zero_days,
        "unavailable_zero_day_count": sum(row["zero_observation_status"] == "not_available" for row in full_days),
    }
    return _section(status, reason, daily=days, baseline=baseline, trace_coverage=trace_coverage), len(baseline_days), len(full_days)


def _lineage_row(db: sqlite3.Connection, artifact_type: str, table: str, stage: str, tables: set[str], window: tuple[str, str], target_key: str = "id") -> dict[str, Any]:
    if table not in tables:
        return {"stage": stage, "artifact_type": artifact_type, "status": "not_available", "reason": f"The {table} table is not available.", "referenced_count": 0, "factual_record_count": None, "missing_record_count": None}
    row = db.execute(
        f"SELECT COUNT(*) AS referenced_count, COUNT(target.{target_key}) AS factual_record_count "
        "FROM agent_run_artifacts artifact JOIN agent_runs run ON run.id=artifact.run_id "
        f"LEFT JOIN {table} target ON target.{target_key}=artifact.artifact_id "
        "WHERE artifact.artifact_type=? AND " + _window_sql("run.started_at"),
        (artifact_type, *window),
    ).fetchone()
    referenced, factual = _count(row["referenced_count"]), _count(row["factual_record_count"])
    status = _status(referenced, factual)
    reason = None if status == "available" else "No typed references of this stage are recorded in this window." if not referenced else "Some typed artifact references no longer resolve to a factual record."
    return {"stage": stage, "artifact_type": artifact_type, "status": status, "reason": reason, "referenced_count": referenced, "factual_record_count": factual, "missing_record_count": referenced - factual}


def _decision_proposal_lineage(db: sqlite3.Connection, tables: set[str], window: tuple[str, str]) -> list[dict[str, Any]]:
    stages = ("proposal", "order", "fill", "position")
    sources, parameters = [], []
    for artifact_type, table in (("filter_decision", "filter_decisions"), ("pi_decision", "pi_decisions")):
        if table in tables:
            sources.append(
                "SELECT decision.proposal_id FROM agent_run_artifacts artifact "
                "JOIN agent_runs run ON run.id=artifact.run_id "
                f"JOIN {table} decision ON decision.id=artifact.artifact_id "
                "WHERE artifact.artifact_type=? AND " + _window_sql("run.started_at")
            )
            parameters.extend((artifact_type, *window))
    if not sources:
        reason = "No typed decision table is available."
        return [{"stage": stage, "artifact_type": "decision_proposal", "status": "not_available", "reason": reason, "referenced_count": 0, "factual_record_count": None, "missing_record_count": None} for stage in stages]
    references = " UNION ALL ".join(sources)
    reference_count = _count(db.execute(
        f"SELECT COUNT(DISTINCT proposal_id) FROM ({references}) WHERE proposal_id IS NOT NULL",
        parameters,
    ).fetchone()[0])
    if "trade_proposals" not in tables:
        reason = "The trade-proposals table is not available."
        return [{"stage": stage, "artifact_type": "decision_proposal", "status": "not_available", "reason": reason, "referenced_count": reference_count, "factual_record_count": None, "missing_record_count": None} for stage in stages]

    def factual_row(stage: str, sql: str, missing_reason: str) -> dict[str, Any]:
        row = db.execute(sql, parameters).fetchone()
        referenced, record_count, covered = (_count(row[key]) for key in ("referenced_count", "factual_record_count", "covered_reference_count"))
        status = _status(referenced, covered)
        reason = None if status == "available" else "No typed decision artifacts refer to a proposal in this window." if not referenced else missing_reason
        return {"stage": stage, "artifact_type": "decision_proposal", "status": status, "reason": reason, "referenced_count": referenced, "factual_record_count": record_count, "covered_reference_count": covered, "missing_record_count": referenced - covered}

    proposal_row = factual_row(
        "proposal",
        "SELECT COUNT(DISTINCT reference.proposal_id) AS referenced_count, COUNT(DISTINCT proposal.id) AS factual_record_count, COUNT(DISTINCT CASE WHEN proposal.id IS NOT NULL THEN reference.proposal_id END) AS covered_reference_count "
        f"FROM ({references}) reference LEFT JOIN trade_proposals proposal ON proposal.id=reference.proposal_id WHERE reference.proposal_id IS NOT NULL",
        "Some decision-linked proposals no longer resolve to factual records.",
    )

    def unavailable(stage: str, table: str) -> dict[str, Any]:
        return {"stage": stage, "artifact_type": "decision_proposal", "status": "not_available", "reason": f"The {table} table is not available.", "referenced_count": reference_count, "factual_record_count": None, "missing_record_count": None}

    if "orders" not in tables:
        return [proposal_row, unavailable("order", "orders"), unavailable("fill", "fills"), unavailable("position", "positions")]
    order_row = factual_row(
        "order",
        "SELECT COUNT(DISTINCT reference.proposal_id) AS referenced_count, COUNT(DISTINCT orders.id) AS factual_record_count, COUNT(DISTINCT CASE WHEN orders.id IS NOT NULL THEN reference.proposal_id END) AS covered_reference_count "
        f"FROM ({references}) reference LEFT JOIN orders ON orders.proposal_id=reference.proposal_id WHERE reference.proposal_id IS NOT NULL",
        "Some decision-linked proposals have no direct order record.",
    )
    if "fills" not in tables:
        fill_row = unavailable("fill", "fills")
    else:
        fill_row = factual_row(
            "fill",
            "SELECT COUNT(DISTINCT reference.proposal_id) AS referenced_count, COUNT(DISTINCT fills.id) AS factual_record_count, COUNT(DISTINCT CASE WHEN fills.id IS NOT NULL THEN reference.proposal_id END) AS covered_reference_count "
            f"FROM ({references}) reference LEFT JOIN orders ON orders.proposal_id=reference.proposal_id LEFT JOIN fills ON fills.order_id=orders.id WHERE reference.proposal_id IS NOT NULL",
            "Some decision-linked proposals have no direct fill record.",
        )
    if "positions" not in tables:
        position_row = unavailable("position", "positions")
    else:
        position_row = factual_row(
            "position",
            "SELECT COUNT(DISTINCT reference.proposal_id) AS referenced_count, COUNT(DISTINCT positions.id) AS factual_record_count, COUNT(DISTINCT CASE WHEN positions.id IS NOT NULL THEN reference.proposal_id END) AS covered_reference_count "
            f"FROM ({references}) reference LEFT JOIN positions ON positions.proposal_id=reference.proposal_id WHERE reference.proposal_id IS NOT NULL",
            "Some decision-linked proposals have no factual position.",
        )
    return [proposal_row, order_row, fill_row, position_row]


def _research_yield(db: sqlite3.Connection, tables: set[str], window: tuple[str, str]) -> tuple[dict[str, Any], int, int]:
    if not {"agent_run_artifacts", "agent_runs"}.issubset(tables):
        return _section("not_available", "Typed agent-run artifacts are not available in this database.", artifacts=[], lineage=[]), 0, 0
    artifacts = _query_rows(
        db,
        "SELECT artifact.run_id,run.agent_name,artifact.artifact_type,COUNT(*) AS artifact_count "
        "FROM agent_run_artifacts artifact JOIN agent_runs run ON run.id=artifact.run_id WHERE "
        + _window_sql("run.started_at") + " GROUP BY artifact.run_id,run.agent_name,artifact.artifact_type ORDER BY artifact.run_id,artifact.artifact_type",
        window,
    )
    lineage = [
        _lineage_row(db, artifact_type, table, stage, tables, window, target_key)
        for artifact_type, table, stage, target_key in (
            ("opportunity", "opportunities", "opportunity", "id"),
            ("filter_decision", "filter_decisions", "decision", "id"),
            ("pi_decision", "pi_decisions", "decision", "id"),
            ("serenity_evidence", "serenity_evidence", "serenity_evidence", "id"),
            ("research_note", "knowledge_items", "research_note", "id"),
            ("portfolio_snapshot", "portfolio_snapshots", "portfolio_snapshot", "id"),
            ("post_trade_review", "knowledge_items", "post_trade_review", "id"),
            ("lesson", "ps_lessons", "lesson", "lesson_id"),
            ("dreaming_log", "knowledge_items", "dreaming_log", "id"),
            ("daily_review", "knowledge_items", "daily_review", "id"),
            ("policy_version", "ps_policy_versions", "policy_version", "policy_version_id"),
            ("draft_skill", "ps_skills", "draft_skill", "skill_id"),
        )
    ]
    lineage.extend(_decision_proposal_lineage(db, tables, window))
    referenced = sum(_count(row.get("referenced_count")) for row in lineage)
    factual = sum(_count(row.get("covered_reference_count", row.get("factual_record_count"))) for row in lineage)
    status = _status(referenced, factual)
    reason = None if status == "available" else "No typed artifacts are recorded in this window." if not artifacts else "Typed artifacts exist, but some factual stage links are unavailable or unresolved."
    return _section(status, reason, artifacts=artifacts, lineage=lineage), factual, referenced


def _coverage(axes: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {axis["status"] for axis in axes}
    status = "available" if statuses == {"available"} else "not_available" if statuses == {"not_available"} else "partial"
    reason = None if status == "available" else "No coverage axis is measurable in this window." if status == "not_available" else "Some coverage axes are incomplete or unavailable."
    return _section(status, reason, axes=axes)


def build_usage_insights(
    *, days: int, as_of: datetime | None = None, group: str | None = None
) -> dict[str, Any]:
    """Build the exact Usage-page contract without writes, caches, or raw content."""
    if days not in _WINDOW_DAYS:
        raise ValueError("days must be one of 1, 7, 30, or 90")
    if group is not None and group not in _GROUPS:
        raise ValueError("group must be summary, operations, or analysis")
    end = _as_utc(as_of or _utcnow())
    start = end - timedelta(days=days)
    window = (start.isoformat(), end.isoformat())
    try:
        with readonly_db() as db:
            tables = _table_names(db)
            if "runtime_cost_events" not in tables:
                return _empty(days, end, "The runtime cost ledger is not available.")
            runtime_rows = _runtime_rows(db, window)
            _project_latest_litellm_estimates(db, runtime_rows)
            llm_rows = [row for row in runtime_rows if row.get("event_type") == "llm"]
            result: dict[str, Any] = {
                "window": {"days": days, "started_at": window[0], "ended_at": window[1], "as_of": end.isoformat(), "timezone": "UTC"},
            }
            axes: list[dict[str, Any]] = []
            if group in (None, "summary"):
                token_mix, token_usable = _token_mix(llm_rows)
                pricing, priced_events = _pricing(runtime_rows)
                reliability, measured_latency = _reliability(
                    db, tables, llm_rows, window, end.isoformat()
                )
                result.update(
                    token_mix=token_mix, pricing=pricing, reliability=reliability
                )
                axes.extend([
                    {"id": "consumption", "label": "Consumption", "status": _status(len(llm_rows), token_usable), "coverage_pct": _pct(token_usable, len(llm_rows)), "reason": token_mix["reason"]},
                    {"id": "pricing", "label": "Pricing", "status": _status(len(runtime_rows), priced_events), "coverage_pct": _pct(priced_events, len(runtime_rows)), "reason": pricing["reason"]},
                    {"id": "reliability", "label": "Reliability", "status": _status(len(llm_rows), measured_latency), "coverage_pct": _pct(measured_latency, len(llm_rows)), "reason": reliability["reason"]},
                ])
            if group in (None, "operations"):
                attribution, attributable_events = _attribution(db, llm_rows, tables)
                tools, priced_tools, timed_tools = _tools(db, tables, runtime_rows, window, end.isoformat())
                budgets, calculable_budgets, total_budgets = _budgets(db, tables, end)
                trends, trace_covered_trend_days, full_trend_days = _trends(db, tables, runtime_rows, start, end)
                result.update(attribution=attribution, tools=tools, budgets=budgets, trends=trends)
                tool_total = sum(_count(row.get("event_count")) for row in tools["rows"])
                axes.extend([
                    {"id": "attribution", "label": "Attribution", "status": _status(len(llm_rows), attributable_events), "coverage_pct": _pct(attributable_events, len(llm_rows)), "reason": attribution["reason"]},
                    {"id": "tools", "label": "Tools", "status": _status(tool_total, min(priced_tools, timed_tools)), "coverage_pct": _pct(min(priced_tools, timed_tools), tool_total), "reason": tools["reason"]},
                    {"id": "budgets", "label": "Budgets", "status": _status(total_budgets, calculable_budgets), "coverage_pct": _pct(calculable_budgets, total_budgets), "reason": budgets["reason"]},
                    {"id": "trends", "label": "Trends", "status": trends["status"], "coverage_pct": _pct(trace_covered_trend_days, full_trend_days), "reason": trends["reason"]},
                ])
            if group in (None, "analysis"):
                research_yield, factual_stages, referenced_stages = _research_yield(db, tables, window)
                result.update(research_yield=research_yield)
                axes.extend([
                    {"id": "research_yield", "label": "Research yield", "status": _status(referenced_stages, factual_stages), "coverage_pct": _pct(factual_stages, referenced_stages), "reason": research_yield["reason"]},
                ])
            if group is None:
                order = {
                    name: index
                    for index, name in enumerate(
                        ("consumption", "pricing", "attribution", "tools", "reliability", "budgets", "trends", "research_yield")
                    )
                }
                axes.sort(key=lambda axis: order[str(axis["id"])])
            result["coverage"] = _coverage(axes)
            return result
    except (FileNotFoundError, sqlite3.Error):
        return _empty(days, end, "The usage-insights read model is not available.")
