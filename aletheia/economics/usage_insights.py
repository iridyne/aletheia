"""Typed, aggregate-only read model for the Usage v2 page."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from typing import Any, Iterable

from aletheia.economics.tracing import estimate_llm_cost, normalize_usage
from aletheia.model_provider.fallback import get_model_health_snapshot
from aletheia.storage.sqlite import readonly_db

_WINDOW_DAYS = {1, 7, 30, 90}
_GROUPS = {"summary", "operations"}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _instant(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _json(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: object) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _pct(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 4) if denominator else None


def _quantile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * probability
    lower, upper = math.floor(index), math.ceil(index)
    value = ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
    return round(value, 3)


def _rows(db: sqlite3.Connection, sql: str, params: Iterable[object] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]


def _tables(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _window_rows(db: sqlite3.Connection, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return _rows(
        db,
        "SELECT * FROM runtime_cost_events WHERE event_type='llm' "
        "AND replace(started_at,' ','T') >= replace(?,' ','T') "
        "AND replace(started_at,' ','T') < replace(?,' ','T') "
        "ORDER BY replace(started_at,' ','T'),rowid",
        (start.isoformat(), end.isoformat()),
    )


def _logical_window_rows(db: sqlite3.Connection, start: datetime, end: datetime) -> list[dict[str, Any]]:
    return _rows(
        db,
        "SELECT * FROM llm_inference_calls "
        "WHERE replace(started_at,' ','T') >= replace(?,' ','T') "
        "AND replace(started_at,' ','T') < replace(?,' ','T') "
        "ORDER BY replace(started_at,' ','T'),rowid",
        (start.isoformat(), end.isoformat()),
    )


def _physical_projection_rows(db: sqlite3.Connection, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Include every attempt owned by an in-window logical call, even across a bucket edge."""
    return _rows(
        db,
        "SELECT e.* FROM runtime_cost_events e "
        "LEFT JOIN llm_inference_calls l ON l.logical_call_id=e.call_id "
        "WHERE e.event_type='llm' AND ((l.logical_call_id IS NOT NULL "
        "AND replace(l.started_at,' ','T') >= replace(?,' ','T') "
        "AND replace(l.started_at,' ','T') < replace(?,' ','T')) "
        "OR (l.logical_call_id IS NULL "
        "AND replace(e.started_at,' ','T') >= replace(?,' ','T') "
        "AND replace(e.started_at,' ','T') < replace(?,' ','T'))) "
        "ORDER BY replace(COALESCE(l.started_at,e.started_at),' ','T'),e.attempt_index,e.rowid",
        (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()),
    )


def _usage(row: dict[str, Any]) -> dict[str, int | None]:
    envelope = _json(row.get("usage_json"))
    normalized = envelope.get("normalized")
    if not isinstance(normalized, dict):
        return normalize_usage(envelope.get("raw", envelope))
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_input_tokens",
        "provider_cache_read_input_tokens",
        "provider_cache_creation_input_tokens",
        "web_search_requests",
    )
    result: dict[str, int | None] = {}
    for field in fields:
        value = normalized.get(field)
        result[field] = value if isinstance(value, int) and value >= 0 else None
    return result


def _price_index(db: sqlite3.Connection) -> dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]]:
    index: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for row in _rows(db, "SELECT rowid AS _rowid,* FROM pricing_snapshots"):
        effective = _instant(row.get("effective_from"))
        if effective:
            index[(str(row.get("provider") or ""), str(row.get("model") or ""))].append((effective, row))
    for snapshots in index.values():
        snapshots.sort(key=lambda item: (item[0], int(item[1]["_rowid"])), reverse=True)
    return index


def _logical_metadata(ledger: dict[str, Any]) -> dict[str, Any]:
    metadata = _json(ledger.get("metadata_json"))
    environment = metadata.get("environment")
    logical_agent = environment.get("logical_agent") if isinstance(environment, dict) else None
    metadata["logical_agent"] = _text(logical_agent) or ledger.get("caller")
    return metadata


def _resolve(rows: list[dict[str, Any]], prices: dict) -> list[dict[str, Any]]:
    for row in rows:
        if row.get("_integrity_reason"):
            row["_usage"], row["_cost_usd"] = (
                {
                    field: None
                    for field in (
                        "input_tokens",
                        "output_tokens",
                        "total_tokens",
                        "reasoning_tokens",
                        "cached_input_tokens",
                        "provider_cache_read_input_tokens",
                        "provider_cache_creation_input_tokens",
                        "web_search_requests",
                    )
                },
                None,
            )
            continue
        usage = _usage(row)
        stored = _number(row.get("estimated_cost_usd"))
        cost = stored if row.get("cost_status") == "known" and stored is not None else None
        if cost is None:
            started = _instant(row.get("started_at"))
            key = (str(row.get("provider") or ""), str(row.get("model_or_tool") or ""))
            price = next((item for effective, item in prices.get(key, []) if started and effective <= started), None)
            cost, _, _ = estimate_llm_cost(usage, price)
        row["_usage"], row["_cost_usd"] = usage, cost
    return rows


def _project_window(
    db: sqlite3.Connection,
    tables: set[str],
    start: datetime,
    end: datetime,
    prices: dict,
) -> list[dict[str, Any]]:
    """Join completed logical calls to attempts without rewriting either ledger."""
    if "llm_inference_calls" not in tables:
        return _resolve(_window_rows(db, start, end), prices)
    physical = _physical_projection_rows(db, start, end)
    logical = _logical_window_rows(db, start, end)
    by_id = {str(row["logical_call_id"]): row for row in logical}
    by_call: dict[str, list[dict[str, Any]]] = defaultdict(list)
    projected: list[dict[str, Any]] = []
    for row in physical:
        call_id = _text(row.get("call_id")) or str(row.get("id"))
        ledger = by_id.get(call_id)
        if ledger is not None and ledger.get("status") == "running":
            continue
        if ledger is not None:
            row["_logical_status"] = ledger.get("status")
            row["_logical_started_at"] = ledger.get("started_at")
            row["_logical_retry_count"] = int(ledger.get("retry_count") or 0)
            row["_logical_fallback_used"] = ledger.get("fallback_used")
            row["_logical_caller"] = ledger.get("caller")
            row["_logical_error_type"] = ledger.get("error_type")
            by_call[call_id].append(row)
        projected.append(row)
    for call_id, ledger in by_id.items():
        if ledger.get("status") == "running":
            continue
        attempts = by_call.get(call_id, [])
        expected = int(ledger.get("attempt_count") or 0)
        if expected == 0 and not attempts:
            metadata = _logical_metadata(ledger)
            projected.append(
                {
                    "id": "logical_zero_" + call_id,
                    "event_type": "llm",
                    "run_id": ledger.get("run_id"),
                    "call_id": call_id,
                    "provider": ledger.get("final_provider") or ledger.get("requested_provider"),
                    "model_or_tool": ledger.get("final_model") or "Unattributed",
                    "attempt_index": 0,
                    "status": "error",
                    "started_at": ledger.get("started_at"),
                    "finished_at": ledger.get("finished_at"),
                    "latency_ms": None,
                    "usage_json": '{"normalized":{"input_tokens":0,"output_tokens":0}}',
                    "estimated_cost_usd": 0.0,
                    "cost_status": "known",
                    "metadata_json": json.dumps(metadata, separators=(",", ":")),
                    "error_type": ledger.get("error_type"),
                    "_logical_status": "error",
                    "_logical_retry_count": 0,
                    "_logical_fallback_used": ledger.get("fallback_used"),
                    "_synthetic_zero_attempt": True,
                }
            )
        elif expected > 0 and not attempts:
            metadata = _logical_metadata(ledger)
            projected.append(
                {
                    "id": "logical_missing_" + call_id,
                    "event_type": "llm",
                    "run_id": ledger.get("run_id"),
                    "call_id": call_id,
                    "provider": ledger.get("final_provider") or ledger.get("requested_provider"),
                    "model_or_tool": ledger.get("final_model") or "Unattributed",
                    "attempt_index": 0,
                    "status": ledger.get("status"),
                    "started_at": ledger.get("started_at"),
                    "finished_at": ledger.get("finished_at"),
                    "latency_ms": None,
                    "usage_json": "{}",
                    "estimated_cost_usd": None,
                    "cost_status": "unknown",
                    "metadata_json": json.dumps(metadata, separators=(",", ":")),
                    "error_type": ledger.get("error_type"),
                    "_logical_status": ledger.get("status"),
                    "_logical_retry_count": int(ledger.get("retry_count") or 0),
                    "_logical_fallback_used": ledger.get("fallback_used"),
                    "_integrity_reason": "physical_attempt_missing",
                    "_synthetic_missing_attempt": True,
                }
            )
        elif len(attempts) != expected:
            for row in attempts:
                row["_integrity_reason"] = "physical_attempt_missing"
    return _resolve(projected, prices)


def _groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[_text(row.get("call_id")) or str(row.get("id"))].append(row)
    return result


def _group_succeeded(group: list[dict[str, Any]]) -> bool:
    logical = next(
        (row.get("_logical_status") for row in group if row.get("_logical_status")),
        None,
    )
    return logical == "success" if logical is not None else any(row.get("status") == "success" for row in group)


def _grouping_instant(row: dict[str, Any]) -> datetime | None:
    return _instant(row.get("_logical_started_at") or row.get("started_at"))


def _metrics(rows: list[dict[str, Any]], *, observed_zero: bool) -> dict[str, Any]:
    groups = _groups(rows)
    calls = len(groups)
    success = sum(_group_succeeded(group) for group in groups.values())
    token_complete = bool(rows) and all(
        row["_usage"].get("input_tokens") is not None and row["_usage"].get("output_tokens") is not None for row in rows
    )
    cost_complete = bool(rows) and all(row.get("_cost_usd") is not None for row in rows)
    tokens = (
        sum(int(row["_usage"]["input_tokens"]) + int(row["_usage"]["output_tokens"]) for row in rows)
        if token_complete
        else 0
        if not rows and observed_zero
        else None
    )
    cost = (
        round(sum(float(row["_cost_usd"]) for row in rows), 12)
        if cost_complete
        else 0.0
        if not rows and observed_zero
        else None
    )
    llm_calls = calls if rows or observed_zero else None
    return {
        "cost_usd": cost,
        "total_tokens": tokens,
        "llm_calls": llm_calls,
        "success_rate_pct": _pct(success, calls),
        "avg_cost_per_call_usd": round(cost / calls, 12) if cost is not None and calls else None,
        "_complete": cost is not None and tokens is not None and llm_calls is not None,
    }


def _trace_intervals(
    db: sqlite3.Connection, tables: set[str], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    if "runtime_sessions" not in tables or "economics_tracing_enabled" not in {
        str(row[1]) for row in db.execute("PRAGMA table_info(runtime_sessions)")
    }:
        return []
    try:
        from aletheia.agent_runtime.runtime_session import current_session_id

        active = current_session_id()
    except Exception:
        active = None
    result = []
    for row in _rows(
        db,
        "SELECT session_id,started_at,ended_at,economics_tracing_enabled FROM runtime_sessions "
        "WHERE replace(started_at,' ','T') < replace(?,' ','T') AND (ended_at IS NULL OR replace(ended_at,' ','T') > replace(?,' ','T'))",
        (end.isoformat(), start.isoformat()),
    ):
        if row.get("economics_tracing_enabled") != 1:
            continue
        begun, finished = _instant(row.get("started_at")), _instant(row.get("ended_at"))
        if finished is None and row.get("session_id") == active:
            finished = end
        if begun and finished:
            result.append((max(start, begun), min(end, finished)))
    return sorted(result)


def _covered(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    cursor = start
    for begun, finished in intervals:
        if begun > cursor:
            return False
        cursor = max(cursor, finished)
        if cursor >= end:
            return True
    return False


def _comparison(current: dict[str, Any], previous: dict[str, Any], complete: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"complete": complete}
    for key in ("cost_usd", "total_tokens", "llm_calls", "avg_cost_per_call_usd"):
        before, now = previous.get(key), current.get(key)
        result[f"{key}_change_pct"] = (
            round((now - before) * 100 / before, 4)
            if complete and now is not None and before not in (None, 0)
            else None
        )
    result["success_rate_change_pct_points"] = (
        round(current["success_rate_pct"] - previous["success_rate_pct"], 4)
        if complete and current.get("success_rate_pct") is not None and previous.get("success_rate_pct") is not None
        else None
    )
    return result


def _composition(rows: list[dict[str, Any]], total: int | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "input_tokens": sum(int(row["_usage"]["input_tokens"]) for row in rows)
        if total is not None and rows
        else 0
        if total == 0
        else None,
        "output_tokens": sum(int(row["_usage"]["output_tokens"]) for row in rows)
        if total is not None and rows
        else 0
        if total == 0
        else None,
    }
    for source, target in (
        ("provider_cache_read_input_tokens", "cache_read_tokens"),
        ("provider_cache_creation_input_tokens", "cache_write_tokens"),
        ("reasoning_tokens", "reasoning_tokens"),
    ):
        values = [row["_usage"].get(source) for row in rows]
        if any(value is not None for value in values):
            result[target] = sum(int(value or 0) for value in values)
    if "cache_read_tokens" not in result:
        values = [row["_usage"].get("cached_input_tokens") for row in rows]
        if any(value is not None for value in values):
            result["cache_read_tokens"] = sum(int(value or 0) for value in values)
    return result


def _trend(
    rows: list[dict[str, Any]], intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime, days: int
) -> list[dict[str, Any]]:
    cursor = start.replace(minute=0, second=0, microsecond=0)
    step = timedelta(hours=1)
    if days != 1:
        cursor, step = cursor.replace(hour=0), timedelta(days=7 if days == 90 else 1)
        if days == 90:
            cursor -= timedelta(days=cursor.weekday())
    result = []
    while cursor < end:
        begun, finished = max(cursor, start), min(cursor + step, end)
        bucket = [row for row in rows if (at := _grouping_instant(row)) and begun <= at < finished]
        metrics = _metrics(bucket, observed_zero=_covered(intervals, begun, finished))
        complete = metrics["total_tokens"] is not None
        result.append(
            {
                "started_at": begun.isoformat(),
                "cost_usd": metrics["cost_usd"],
                "input_tokens": sum(int(row["_usage"]["input_tokens"]) for row in bucket)
                if complete and bucket
                else 0
                if complete
                else None,
                "output_tokens": sum(int(row["_usage"]["output_tokens"]) for row in bucket)
                if complete and bucket
                else 0
                if complete
                else None,
                "llm_calls": metrics["llm_calls"],
            }
        )
        cursor += step
    return result


def _bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = _groups(rows)
    complete_tokens = all(
        row["_usage"].get("input_tokens") is not None and row["_usage"].get("output_tokens") is not None for row in rows
    )
    complete_cost = all(row.get("_cost_usd") is not None for row in rows)
    latencies = [value for row in rows if (value := _number(row.get("latency_ms"))) is not None]
    calls = len(groups)
    return {
        "calls": calls,
        "success_rate_pct": _pct(sum(_group_succeeded(group) for group in groups.values()), calls),
        "input_tokens": sum(int(row["_usage"]["input_tokens"]) for row in rows) if complete_tokens else None,
        "output_tokens": sum(int(row["_usage"]["output_tokens"]) for row in rows) if complete_tokens else None,
        "tokens": sum(int(row["_usage"]["input_tokens"]) + int(row["_usage"]["output_tokens"]) for row in rows)
        if complete_tokens
        else None,
        "cost_usd": round(sum(float(row["_cost_usd"]) for row in rows), 12) if complete_cost else None,
        "p95_latency_ms": _quantile(latencies, 0.95),
    }


def _sort_cost(rows: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("cost_usd") is None,
            -(float(row["cost_usd"]) if row.get("cost_usd") is not None else 0),
            str(row.get(label) or ""),
        ),
    )


def _breakdowns(
    db: sqlite3.Connection, tables: set[str], rows: list[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, list[dict[str, Any]]]:
    run_names = (
        {str(row["id"]): _text(row.get("agent_name")) for row in _rows(db, "SELECT id,agent_name FROM agent_runs")}
        if "agent_runs" in tables
        else {}
    )
    agents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    models: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    providers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        metadata, run_id = _json(row.get("metadata_json")), _text(row.get("run_id"))
        agent = (
            run_names.get(run_id or "")
            or _text(row.get("_logical_caller"))
            or _text(metadata.get("logical_agent"))
            or "Unattributed"
        )
        provider, model = (
            _text(row.get("provider")) or "Unattributed",
            _text(row.get("model_or_tool")) or "Unattributed",
        )
        agents[agent].append(row)
        models[(model, provider)].append(row)
        providers[provider].append(row)
    agent_rows = [
        {
            "agent": name,
            "runs": len({_text(row.get("run_id")) for row in bucket if _text(row.get("run_id"))}),
            **{
                key: value
                for key, value in _bucket_metrics(bucket).items()
                if key not in {"input_tokens", "output_tokens"}
            },
        }
        for name, bucket in agents.items()
    ]
    model_rows = [
        {
            "model": model,
            "provider": provider,
            **{key: value for key, value in _bucket_metrics(bucket).items() if key != "tokens"},
        }
        for (model, provider), bucket in models.items()
    ]
    provider_rows = [
        {
            "provider": name,
            "models": len({_text(row.get("model_or_tool")) for row in bucket if _text(row.get("model_or_tool"))}),
            **{
                key: value
                for key, value in _bucket_metrics(bucket).items()
                if key not in {"input_tokens", "output_tokens"}
            },
        }
        for name, bucket in providers.items()
    ]
    tools = []
    if "agent_tool_calls" in tables:
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in _rows(
            db,
            "SELECT tool_name,tool_class,permitted,duration_ms FROM agent_tool_calls WHERE replace(created_at,' ','T') >= replace(?,' ','T') AND replace(created_at,' ','T') < replace(?,' ','T')",
            (start.isoformat(), end.isoformat()),
        ):
            buckets[
                (_text(row.get("tool_name")) or "Unattributed", _text(row.get("tool_class")) or "Unattributed")
            ].append(row)
        for (name, klass), bucket in buckets.items():
            durations = [value for row in bucket if (value := _number(row.get("duration_ms"))) is not None]
            tools.append(
                {
                    "tool": name,
                    "class": klass,
                    "permitted": sum(bool(row.get("permitted")) for row in bucket),
                    "denied": sum(not bool(row.get("permitted")) for row in bucket),
                    "calls": len(bucket),
                    "p50_latency_ms": _quantile(durations, 0.5),
                    "p95_latency_ms": _quantile(durations, 0.95),
                }
            )
    tools.sort(key=lambda row: (-int(row["calls"]), str(row["tool"])))
    return {
        "agents": _sort_cost(agent_rows, "agent"),
        "models": _sort_cost(model_rows, "model"),
        "providers": _sort_cost(provider_rows, "provider"),
        "tools": tools,
    }


def _reliability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = _groups(rows)
    physical = [
        row for row in rows if not row.get("_synthetic_zero_attempt") and not row.get("_synthetic_missing_attempt")
    ]
    latencies = [value for row in physical if (value := _number(row.get("latency_ms"))) is not None]
    failed = [group for group in groups.values() if not _group_succeeded(group)]
    errors = len(failed)
    result: dict[str, Any] = {
        "attempts": len(physical),
        "errors": errors,
        "retries": sum(
            int(group[0].get("_logical_retry_count"))
            if group[0].get("_logical_retry_count") is not None
            else max(0, len(group) - 1)
            for group in groups.values()
        ),
        "fallbacks": sum(
            bool(group[0].get("_logical_fallback_used"))
            if group[0].get("_logical_fallback_used") is not None
            else len({(row.get("provider"), row.get("model_or_tool")) for row in group}) > 1
            for group in groups.values()
        ),
        "p50_latency_ms": _quantile(latencies, 0.5),
        "p95_latency_ms": _quantile(latencies, 0.95),
    }
    if errors:
        categories = Counter(
            _text(group[-1].get("_logical_error_type")) or _text(group[-1].get("error_type")) or "Uncategorized"
            for group in failed
        )
        result["error_categories"] = [
            {"category": name, "count": count}
            for name, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))
        ]
    return result


def _cooldowns() -> list[dict[str, Any]]:
    try:
        return [
            {
                "provider": row.get("provider"),
                "model": row.get("model"),
                "remaining_seconds": row.get("cooldown_remaining_sec"),
            }
            for row in get_model_health_snapshot()
            if row.get("cooling_down")
        ]
    except Exception:
        return []


def _period_start(period: str, end: datetime) -> datetime | None:
    if period in {"daily", "weekly"}:
        return end - timedelta(days=1 if period == "daily" else 7)
    try:
        from aletheia.agent_runtime.runtime_session import current_session, current_session_durable

        return (
            _instant((current_session() or {}).get("started_at"))
            if period == "session" and current_session_durable()
            else None
        )
    except Exception:
        return None


def _budgets(db: sqlite3.Connection, tables: set[str], prices: dict, end: datetime) -> list[dict[str, Any]]:
    if "economics_budgets" not in tables:
        return []
    result = []
    for policy in _rows(
        db, "SELECT scope,subject,usd_cap,window FROM economics_budgets WHERE enabled=1 ORDER BY scope,subject,window"
    ):
        start = _period_start(str(policy["window"]), end)
        events = _project_window(db, tables, start, end, prices) if start else []
        scope, subject = str(policy["scope"]), _text(policy.get("subject"))
        if scope == "agent_run":
            events = [row for row in events if _text(row.get("run_id")) == subject]
        elif scope == "provider":
            events = [row for row in events if _text(row.get("provider")) == subject]
        elif scope == "logical_agent":
            events = [row for row in events if _text(_json(row.get("metadata_json")).get("logical_agent")) == subject]
        elif scope != "system":
            start = None
        complete = start is not None and all(row.get("_cost_usd") is not None for row in events)
        spend = round(sum(float(row["_cost_usd"]) for row in events), 12) if complete else None
        limit = float(policy["usd_cap"])
        result.append(
            {
                "limit_usd": limit,
                "spend_usd": spend,
                "remaining_usd": round(limit - spend, 12) if spend is not None else None,
                "utilization_pct": round(spend * 100 / limit, 4) if spend is not None else None,
                "period": policy["window"],
            }
        )
    return result


def _window(days: int, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "days": days,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "updated_at": end.isoformat(),
        "timezone": "UTC",
        "granularity": "hour" if days == 1 else "week" if days == 90 else "day",
    }


def _empty(days: int, start: datetime, end: datetime, group: str) -> dict[str, Any]:
    base = {"contract_version": 2, "window": _window(days, start, end)}
    if group == "summary":
        return {
            **base,
            "overview": {
                key: None
                for key in ("cost_usd", "total_tokens", "llm_calls", "success_rate_pct", "avg_cost_per_call_usd")
            },
            "comparison": {"complete": False},
            "token_composition": {"input_tokens": None, "output_tokens": None},
            "trend": [],
        }
    return {
        **base,
        "breakdowns": {"agents": [], "models": [], "providers": [], "tools": []},
        "reliability": {
            "attempts": 0,
            "errors": 0,
            "retries": 0,
            "fallbacks": 0,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
        },
        "active_cooldowns": [],
        "budgets": [],
    }


def build_usage_insights(*, days: int, as_of: datetime | None = None, group: str = "summary") -> dict[str, Any]:
    """Build Usage contract v2 without writes, caches, or raw content."""
    if days not in _WINDOW_DAYS:
        raise ValueError("days must be one of 1, 7, 30, or 90")
    if group not in _GROUPS:
        raise ValueError("group must be summary or operations")
    end = _utc(as_of or datetime.now(timezone.utc))
    start = end - timedelta(days=days)
    try:
        with readonly_db() as db:
            tables = _tables(db)
            if not {"runtime_cost_events", "pricing_snapshots"}.issubset(tables):
                return _empty(days, start, end, group)
            prices = _price_index(db)
            rows = _project_window(db, tables, start, end, prices)
            base = {"contract_version": 2, "window": _window(days, start, end)}
            if group == "operations":
                return {
                    **base,
                    "breakdowns": _breakdowns(db, tables, rows, start, end),
                    "reliability": _reliability(rows),
                    "active_cooldowns": _cooldowns(),
                    "budgets": _budgets(db, tables, prices, end),
                }
            previous_start = start - timedelta(days=days)
            intervals = _trace_intervals(db, tables, previous_start, end)
            overview = _metrics(rows, observed_zero=_covered(intervals, start, end))
            previous = _metrics(
                _project_window(db, tables, previous_start, start, prices),
                observed_zero=_covered(intervals, previous_start, start),
            )
            complete = bool(previous["_complete"] and _covered(intervals, previous_start, start))
            return {
                **base,
                "overview": {key: value for key, value in overview.items() if not key.startswith("_")},
                "comparison": _comparison(overview, previous, complete),
                "token_composition": _composition(rows, overview["total_tokens"]),
                "trend": _trend(rows, intervals, start, end, days),
            }
    except (FileNotFoundError, sqlite3.Error):
        return _empty(days, start, end, group)
