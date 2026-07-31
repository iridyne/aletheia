"""Append-only runtime cost facts for LLM, tool, data, and infrastructure activity."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from aletheia.economics.schema import ensure_economics_schema


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_iso(value: str | None) -> str:
    if value is None:
        return _utcnow()
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def tracing_enabled() -> bool:
    return os.getenv("ALETHEIA_COST_TRACING", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def initialize_runtime_tracing() -> bool:
    """Install economics schema/tool trigger and synchronize the runtime kill switch."""
    enabled = tracing_enabled()
    db = ensure_economics_schema()
    db.execute(
        "INSERT OR REPLACE INTO economics_settings(key,value,updated_at) VALUES (?,?,?)",
        ("cost_tracing_enabled", "1" if enabled else "0", _utcnow()),
    )
    return enabled


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def add_pricing_snapshot(
    *,
    provider: str,
    model: str,
    input_usd_per_million: float,
    output_usd_per_million: float,
    reasoning_usd_per_million: float | None = None,
    cached_input_usd_per_million: float | None = None,
    effective_from: str | None = None,
    source: str = "manual",
    version: str = "v1",
) -> dict[str, Any]:
    """Persist an explicit immutable price contract; no live prices are guessed."""
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise ValueError("provider and model are required")
    rates = {
        "input_usd_per_million": input_usd_per_million,
        "output_usd_per_million": output_usd_per_million,
        "reasoning_usd_per_million": reasoning_usd_per_million,
        "cached_input_usd_per_million": cached_input_usd_per_million,
    }
    for name, value in rates.items():
        if value is not None and (_float(value) is None or float(value) < 0):
            raise ValueError(f"{name} must be a finite non-negative number")
    created_at = _utcnow()
    effective = _normalize_iso(effective_from)
    identity = _json(
        {
            "provider": provider,
            "model": model,
            "effective_from": effective,
            "version": version,
        }
    )
    snapshot_id = "price_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    row = {
        "id": snapshot_id,
        "provider": provider,
        "model": model,
        "effective_from": effective,
        "input_usd_per_million": float(input_usd_per_million),
        "output_usd_per_million": float(output_usd_per_million),
        "reasoning_usd_per_million": None
        if reasoning_usd_per_million is None
        else float(reasoning_usd_per_million),
        "cached_input_usd_per_million": None
        if cached_input_usd_per_million is None
        else float(cached_input_usd_per_million),
        "currency": "USD",
        "source": source,
        "version": version,
        "created_at": created_at,
    }
    db = ensure_economics_schema()
    db.execute(
        """INSERT OR IGNORE INTO pricing_snapshots (
            id, provider, model, effective_from, input_usd_per_million,
            output_usd_per_million, reasoning_usd_per_million,
            cached_input_usd_per_million, currency, source, version, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        tuple(row.values()),
    )
    stored = db.execute(
        "SELECT * FROM pricing_snapshots WHERE id=?", (snapshot_id,)
    ).fetchone()
    return dict(stored) if stored else row


def list_pricing_snapshots(
    *,
    provider: str | None = None,
    model: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List currently persisted pricing snapshots for CLI/UI inspection.

    Read-only. Immutable table by contract — this helper is for viewing,
    not for reconciliation.
    """
    db = ensure_economics_schema()
    where: list[str] = []
    params: list[Any] = []
    if provider:
        where.append("provider=?")
        params.append(provider)
    if model:
        where.append("model=?")
        params.append(model)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT * FROM pricing_snapshots {where_sql} "
        f"ORDER BY provider, model, replace(effective_from,' ','T') DESC, created_at DESC "
        f"LIMIT ?",
        (*params, max(1, min(int(limit), 5_000))),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_pricing_snapshot(
    provider: str, model: str, at: str
) -> dict[str, Any] | None:
    db = ensure_economics_schema()
    row = db.execute(
        """SELECT * FROM pricing_snapshots
           WHERE provider=? AND model=? AND datetime(effective_from) <= datetime(?)
           ORDER BY datetime(effective_from) DESC, rowid DESC LIMIT 1""",
        (provider, model, at),
    ).fetchone()
    return dict(row) if row else None


def add_tool_cost_profile(
    *,
    tool_name: str,
    usd_per_call: float,
    effective_from: str | None = None,
    source: str = "manual",
    version: str = "v1",
) -> dict[str, Any]:
    """Persist an explicit immutable per-call Tool/API cost contract."""
    tool_name = tool_name.strip()
    parsed_cost = _float(usd_per_call)
    if not tool_name:
        raise ValueError("tool_name is required")
    if parsed_cost is None or parsed_cost < 0:
        raise ValueError("usd_per_call must be a finite non-negative number")
    created_at = _utcnow()
    effective = _normalize_iso(effective_from)
    identity = _json(
        {
            "tool_name": tool_name,
            "effective_from": effective,
            "version": version,
        }
    )
    profile_id = "toolprice_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    row = {
        "id": profile_id,
        "tool_name": tool_name,
        "effective_from": effective,
        "usd_per_call": parsed_cost,
        "currency": "USD",
        "source": source,
        "version": version,
        "created_at": created_at,
    }
    db = ensure_economics_schema()
    db.execute(
        """INSERT OR IGNORE INTO tool_cost_profiles (
            id,tool_name,effective_from,usd_per_call,currency,source,version,created_at
        ) VALUES (?,?,?,?,?,?,?,?)""",
        tuple(row.values()),
    )
    stored = db.execute(
        "SELECT * FROM tool_cost_profiles WHERE id=?", (profile_id,)
    ).fetchone()
    return dict(stored) if stored else row


def list_tool_cost_profiles(
    *,
    tool_name: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List currently persisted tool cost profiles for CLI/UI inspection.

    Read-only. Immutable table by contract.
    """
    db = ensure_economics_schema()
    where: list[str] = []
    params: list[Any] = []
    if tool_name:
        where.append("tool_name=?")
        params.append(tool_name)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT * FROM tool_cost_profiles {where_sql} "
        f"ORDER BY tool_name, replace(effective_from,' ','T') DESC, created_at DESC "
        f"LIMIT ?",
        (*params, max(1, min(int(limit), 5_000))),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_tool_cost_profile(tool_name: str, at: str) -> dict[str, Any] | None:
    db = ensure_economics_schema()
    row = db.execute(
        """SELECT * FROM tool_cost_profiles
           WHERE tool_name=?
             AND replace(effective_from,' ','T') <= replace(?,' ','T')
           ORDER BY replace(effective_from,' ','T') DESC,rowid DESC LIMIT 1""",
        (tool_name, at),
    ).fetchone()
    return dict(row) if row else None


def normalize_usage(raw_usage: Any) -> dict[str, int | None]:
    """Normalize common provider usage shapes without inventing absent counters."""
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_details = (
        usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    )
    completion_details = (
        usage.get("completion_tokens_details")
        or usage.get("output_tokens_details")
        or {}
    )

    def token(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if value is not None:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                return parsed if parsed >= 0 else None
        return None

    def detail(container: Any, *keys: str) -> int | None:
        if not isinstance(container, dict):
            return None
        for key in keys:
            value = container.get(key)
            if value is not None:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                return parsed if parsed >= 0 else None
        return None

    top_level_reasoning = token("reasoning_tokens")
    return {
        "input_tokens": token("prompt_tokens", "input_tokens"),
        "output_tokens": token("completion_tokens", "output_tokens"),
        "total_tokens": token("total_tokens"),
        "reasoning_tokens": (
            top_level_reasoning
            if top_level_reasoning is not None
            else detail(completion_details, "reasoning_tokens")
        ),
        "cached_input_tokens": detail(
            prompt_details, "cached_tokens", "cached_input_tokens"
        ),
        "provider_cache_read_input_tokens": token("cache_read_input_tokens"),
        "provider_cache_creation_input_tokens": token("cache_creation_input_tokens"),
    }


def estimate_llm_cost(
    usage: dict[str, int | None], pricing: dict[str, Any] | None
) -> tuple[float | None, str, dict[str, Any]]:
    if pricing is None:
        return None, "unknown", {"reason": "pricing_snapshot_missing"}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    reasoning_tokens = usage.get("reasoning_tokens") or 0
    cached_tokens = usage.get("cached_input_tokens") or 0
    provider_cache_read = usage.get("provider_cache_read_input_tokens") or 0
    provider_cache_creation = usage.get("provider_cache_creation_input_tokens") or 0
    if provider_cache_read or provider_cache_creation:
        return (
            None,
            "unknown",
            {
                "reason": "provider_cache_pricing_contract_unsupported",
                "cache_read_input_tokens": provider_cache_read,
                "cache_creation_input_tokens": provider_cache_creation,
            },
        )
    if input_tokens is None or output_tokens is None:
        return None, "unknown", {"reason": "billable_token_usage_incomplete"}
    if cached_tokens > input_tokens:
        return None, "unknown", {"reason": "cached_tokens_exceed_input_tokens"}

    input_rate = _float(pricing.get("input_usd_per_million"))
    output_rate = _float(pricing.get("output_usd_per_million"))
    reasoning_rate = _float(pricing.get("reasoning_usd_per_million"))
    cached_rate = _float(pricing.get("cached_input_usd_per_million"))
    if input_rate is None or output_rate is None:
        return None, "unknown", {"reason": "required_pricing_rate_missing"}
    if cached_tokens and cached_rate is None:
        return None, "unknown", {"reason": "cached_input_pricing_missing"}
    if reasoning_tokens > output_tokens:
        return None, "unknown", {"reason": "reasoning_tokens_exceed_output_tokens"}

    ordinary_input = input_tokens - cached_tokens
    ordinary_output = (
        output_tokens - reasoning_tokens
        if reasoning_rate is not None
        else output_tokens
    )
    cost = ordinary_input * input_rate / 1_000_000
    cost += cached_tokens * (cached_rate or 0.0) / 1_000_000
    cost += ordinary_output * output_rate / 1_000_000
    if reasoning_rate is not None:
        cost += reasoning_tokens * reasoning_rate / 1_000_000
    return (
        round(cost, 12),
        "estimated",
        {
            "ordinary_input_tokens": ordinary_input,
            "ordinary_output_tokens": ordinary_output,
            "reasoning_priced_separately": reasoning_rate is not None,
        },
    )


def _current_provenance() -> dict[str, Any]:
    try:
        from aletheia.agent_runtime.runtime_session import current_provenance

        return current_provenance()
    except Exception:
        return {}


def _current_run_id() -> str | None:
    try:
        from aletheia.agent_runtime.run_context import current_agent_run_id

        return current_agent_run_id()
    except Exception:
        return None


def record_llm_attempt(
    *,
    call_id: str,
    provider: str,
    model: str,
    requested_model: str | None,
    attempt_index: int,
    status: str,
    started_at: str,
    finished_at: str | None,
    latency_ms: float | None,
    raw_usage: Any = None,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Persist one physical provider attempt. Measurement failure never blocks trading."""
    if not tracing_enabled():
        return None
    try:
        usage = normalize_usage(raw_usage)
        pricing = resolve_pricing_snapshot(provider, model, started_at)
        estimated_cost, cost_status, cost_meta = estimate_llm_cost(usage, pricing)
        if status != "success" and not raw_usage:
            estimated_cost, cost_status = None, "unknown"
            cost_meta = {"reason": "failed_attempt_usage_unavailable"}
        event_id = "cost_" + uuid4().hex[:16]
        provenance = _current_provenance()
        row = (
            event_id,
            "llm",
            _current_run_id(),
            provenance.get("runtime_session_id"),
            None,
            None,
            None,
            call_id,
            provider,
            model,
            requested_model,
            int(attempt_index),
            status,
            started_at,
            finished_at,
            latency_ms,
            _json({"raw": raw_usage or {}, "normalized": usage}),
            pricing.get("id") if pricing else None,
            estimated_cost,
            "USD",
            cost_status,
            error_type,
            _json({**(metadata or {}), "cost_calculation": cost_meta}),
            _utcnow(),
        )
        db = ensure_economics_schema()
        db.execute(
            """INSERT OR IGNORE INTO runtime_cost_events (
                id,event_type,run_id,runtime_session_id,opportunity_id,decision_id,
                proposal_id,call_id,provider,model_or_tool,requested_model,attempt_index,
                status,started_at,finished_at,latency_ms,usage_json,pricing_snapshot_id,
                estimated_cost_usd,currency,cost_status,error_type,metadata_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            row,
        )
        return event_id
    except Exception:
        return None


def record_tool_event(
    *,
    run_id: str | None,
    tool_name: str,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    latency_ms: float | None = None,
    external_cost_usd: float | None = None,
    cost_status: str | None = None,
    attempt_index: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    if not tracing_enabled() or not run_id:
        return None
    try:
        start = started_at or finished_at or _utcnow()
        end = finished_at or start
        profile = None
        if external_cost_usd is None:
            profile = resolve_tool_cost_profile(tool_name, start)
            cost = _float(profile.get("usd_per_call")) if profile else None
            resolved_status = "estimated" if profile and cost is not None else "unknown"
        else:
            cost = _float(external_cost_usd)
            if cost is None or cost < 0:
                raise ValueError("external_cost_usd must be finite and non-negative")
            resolved_status = (
                cost_status if cost_status in {"known", "estimated"} else "known"
            )
        call_id = (
            "tool_"
            + hashlib.sha256(
                f"{run_id}:{tool_name}:{attempt_index}:{start}".encode()
            ).hexdigest()[:16]
        )
        provenance = _current_provenance()
        event_id = "cost_" + uuid4().hex[:16]
        db = ensure_economics_schema()
        db.execute(
            """INSERT OR IGNORE INTO runtime_cost_events (
                id,event_type,run_id,runtime_session_id,call_id,provider,model_or_tool,
                attempt_index,status,started_at,finished_at,latency_ms,usage_json,
                cost_profile_id,estimated_cost_usd,currency,cost_status,metadata_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                "tool",
                run_id,
                provenance.get("runtime_session_id"),
                call_id,
                "tool",
                tool_name,
                int(attempt_index),
                status,
                start,
                end,
                latency_ms,
                "{}",
                profile.get("id") if profile else None,
                cost,
                "USD",
                resolved_status,
                _json(metadata or {}),
                _utcnow(),
            ),
        )
        return event_id
    except Exception:
        return None


def record_fixed_cost_event(
    *,
    event_type: str,
    name: str,
    cost_usd: float | None,
    started_at: str,
    finished_at: str | None = None,
    cost_status: str = "known",
    metadata: dict[str, Any] | None = None,
) -> str:
    if event_type not in {"data", "infrastructure", "other"}:
        raise ValueError("fixed cost event_type must be data, infrastructure, or other")
    parsed = _float(cost_usd)
    if parsed is not None and parsed < 0:
        raise ValueError("cost_usd must be non-negative")
    resolved_status = (
        cost_status
        if parsed is not None and cost_status in {"known", "estimated"}
        else "unknown"
    )
    event_id = "cost_" + uuid4().hex[:16]
    call_id = (
        "fixed_"
        + hashlib.sha256(
            f"{event_type}:{name}:{started_at}:{finished_at or ''}".encode()
        ).hexdigest()[:16]
    )
    db = ensure_economics_schema()
    db.execute(
        """INSERT OR IGNORE INTO runtime_cost_events (
            id,event_type,call_id,provider,model_or_tool,attempt_index,status,started_at,
            finished_at,usage_json,estimated_cost_usd,currency,cost_status,metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            event_type,
            call_id,
            event_type,
            name,
            0,
            "allocated",
            started_at,
            finished_at or started_at,
            "{}",
            parsed,
            "USD",
            resolved_status,
            _json(metadata or {}),
            _utcnow(),
        ),
    )
    return event_id


def aggregate_agent_run_cost(run_id: str) -> dict[str, Any]:
    db = ensure_economics_schema()
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM runtime_cost_events WHERE run_id=? ORDER BY started_at,attempt_index,id",
            (run_id,),
        ).fetchall()
    ]
    known = sum(
        float(row["estimated_cost_usd"] or 0)
        for row in rows
        if row["estimated_cost_usd"] is not None
    )
    unknown = sum(
        1
        for row in rows
        if row["cost_status"] == "unknown" or row["estimated_cost_usd"] is None
    )
    llm_rows = [row for row in rows if row["event_type"] == "llm"]
    final_model = next(
        (
            row["model_or_tool"]
            for row in reversed(llm_rows)
            if row["status"] == "success"
        ),
        None,
    )
    return {
        "run_id": run_id,
        "event_count": len(rows),
        "llm_attempt_count": len(llm_rows),
        "tool_event_count": sum(1 for row in rows if row["event_type"] == "tool"),
        "known_cost_subtotal_usd": round(known, 12),
        "unknown_event_count": unknown,
        "cost_estimate_usd": round(known, 12) if rows and unknown == 0 else None,
        "cost_complete": bool(rows) and unknown == 0,
        "final_model": final_model,
    }


def list_cost_summary(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    db = ensure_economics_schema()
    where = [
        "replace(started_at,' ','T') >= replace(?,' ','T')",
        "replace(started_at,' ','T') <= replace(?,' ','T')",
    ]
    params: list[Any] = [window_start, window_end]
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM runtime_cost_events WHERE "
            + " AND ".join(where)
            + " ORDER BY started_at,id",
            params,
        ).fetchall()
    ]
    by_type: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = by_type.setdefault(
            row["event_type"],
            {"event_count": 0, "known_cost_usd": 0.0, "unknown_count": 0},
        )
        bucket["event_count"] += 1
        if row["estimated_cost_usd"] is None or row["cost_status"] == "unknown":
            bucket["unknown_count"] += 1
        else:
            bucket["known_cost_usd"] += float(row["estimated_cost_usd"])
    for bucket in by_type.values():
        bucket["known_cost_usd"] = round(bucket["known_cost_usd"], 12)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "event_count": len(rows),
        "known_cost_subtotal_usd": round(
            sum(float(row["estimated_cost_usd"] or 0) for row in rows), 12
        ),
        "unknown_event_count": sum(
            1
            for row in rows
            if row["estimated_cost_usd"] is None or row["cost_status"] == "unknown"
        ),
        "by_type": by_type,
        "events": rows,
    }
