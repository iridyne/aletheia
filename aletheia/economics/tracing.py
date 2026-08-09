"""Append-only runtime cost facts for LLM, tool, data, and infrastructure activity."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from aletheia.economics.agent_roles import role_for_logical_agent
from aletheia.economics.schema import ensure_economics_schema

# ── Server-side TTL cache for the Usage dashboard ───────────────────────────
# Historical daily aggregates never change (the past is written); the current
# UTC day accrues new rows as agents run.  We cache finished-day results for
# 60s and the still-open day for 5s so the 8-polling-component dashboard does
# not re-run the same SQL on every refetch.  The lock protects the shared dict
# across the gateway worker threads; writes are infrequent (TTL expiry).
_CACHE_LOCK = threading.Lock()
_COST_CACHE: dict[str, tuple[float, Any]] = {}  # key → (expires_at, value)
_CACHE_TTL_FINISHED_DAY = 60.0
_CACHE_TTL_CURRENT_DAY = 5.0


def _cache_get(key: str) -> Any | None:
    """Return a cached value if it exists and has not expired."""
    with _CACHE_LOCK:
        entry = _COST_CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() < expires_at:
            return value
        # Expired — drop it
        del _COST_CACHE[key]
        return None


def _cache_set(key: str, value: Any, *, ttl: float) -> None:
    """Store a value with an absolute TTL (seconds)."""
    with _CACHE_LOCK:
        _COST_CACHE[key] = (time.monotonic() + ttl, value)


def _cache_invalidate() -> None:
    """Flush all cached cost data — call after a write if ever needed."""
    with _CACHE_LOCK:
        _COST_CACHE.clear()


def _window_end_is_today_utc(window_end: str) -> bool:
    """True if the window's end falls on the current UTC day.

    The Usage dashboard always passes ``window_end=now``; if ``window_end``
    is already on a past day we can use the longer TTL safely.
    """
    try:
        end_dt = datetime.fromisoformat(window_end)
    except (ValueError, TypeError):
        return True  # defensive: short TTL
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    return end_dt.date() == now_utc.date()


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


def _bounded_limit(value: Any, *, maximum: int = 5_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    return max(1, min(parsed, maximum))


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
    parsed_rates = {name: _float(value) for name, value in rates.items()}
    for name, value in parsed_rates.items():
        if rates[name] is not None and (value is None or value < 0):
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
        "input_usd_per_million": parsed_rates["input_usd_per_million"],
        "output_usd_per_million": parsed_rates["output_usd_per_million"],
        "reasoning_usd_per_million": parsed_rates["reasoning_usd_per_million"],
        "cached_input_usd_per_million": parsed_rates["cached_input_usd_per_million"],
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
        f"ORDER BY provider, model, effective_from DESC, created_at DESC "
        f"LIMIT ?",
        (*params, _bounded_limit(limit)),
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
        f"ORDER BY tool_name, effective_from DESC, created_at DESC "
        f"LIMIT ?",
        (*params, _bounded_limit(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_tool_cost_profile(tool_name: str, at: str) -> dict[str, Any] | None:
    db = ensure_economics_schema()
    row = db.execute(
        """SELECT * FROM tool_cost_profiles
           WHERE tool_name=?
             AND effective_from <= ?
           ORDER BY effective_from DESC,rowid DESC LIMIT 1""",
        (tool_name, at),
    ).fetchone()
    return dict(row) if row else None


def observe_token_integrity(usage: dict[str, int | None]) -> list[dict[str, str]]:
    """Return non-blocking integrity observations without estimating tokens."""
    findings: list[dict[str, str]] = []
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    reasoning_tokens = usage.get("reasoning_tokens")
    cache_read = usage.get("provider_cache_read_input_tokens")
    cache_creation = usage.get("provider_cache_creation_input_tokens")
    if input_tokens is None or output_tokens is None:
        findings.append({"rule_id": "input_or_output_usage_unavailable"})
    if (
        isinstance(total_tokens, int)
        and isinstance(input_tokens, int)
        and isinstance(output_tokens, int)
        and total_tokens < input_tokens + output_tokens
    ):
        findings.append({"rule_id": "total_tokens_below_input_plus_output"})
    if isinstance(reasoning_tokens, int) and isinstance(output_tokens, int) and reasoning_tokens > output_tokens:
        findings.append({"rule_id": "reasoning_tokens_exceed_output"})
    for name, value in (("cache_read", cache_read), ("cache_creation", cache_creation)):
        if isinstance(value, int) and isinstance(input_tokens, int) and value > input_tokens:
            findings.append({"rule_id": f"{name}_tokens_exceed_input"})
    return findings


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
    cached_from_details = detail(
        prompt_details, "cached_tokens", "cached_input_tokens"
    )
    return {
        "input_tokens": token("prompt_tokens", "input_tokens"),
        "output_tokens": token("completion_tokens", "output_tokens"),
        "total_tokens": token("total_tokens"),
        "reasoning_tokens": (
            top_level_reasoning
            if top_level_reasoning is not None
            else detail(completion_details, "reasoning_tokens")
        ),
        # Providers disagree on where cache hits live: OpenAI/Anthropic report
        # them under prompt_tokens_details.cached_tokens, while DeepSeek-style
        # providers (incl. sensenova) put them at the top level as
        # cache_read_input_tokens. Fall back so both shapes normalize to
        # cached_input_tokens instead of silently zeroing the counter.
        "cached_input_tokens": (
            cached_from_details
            if cached_from_details is not None
            else token("cache_read_input_tokens")
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


def _causal_identity(metadata: dict[str, Any] | None, key: str) -> str | None:
    """Return one explicitly bound, content-free causal id for ledger columns."""
    value = (metadata or {}).get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text[:160] if text else None


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
            _causal_identity(metadata, "opportunity_id"),
            _causal_identity(metadata, "decision_id"),
            _causal_identity(metadata, "proposal_id"),
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
            _json({
                **(metadata or {}),
                "cost_calculation": cost_meta,
                "token_integrity_observations": observe_token_integrity(usage),
            }),
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
        (_float(row["estimated_cost_usd"]) or 0.0)
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


def list_token_summary(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate provider-reported token usage without requiring a price snapshot.

    Input/output totals are summed only when the provider reported the field.
    Cache counters remain separate optional fields rather than inferred values.
    """
    db = ensure_economics_schema()
    if run_id:
        result = db.execute(
            "SELECT usage_json, metadata_json, provider, model_or_tool "
            "FROM runtime_cost_events WHERE event_type='llm' "
            "AND started_at >= ? AND started_at <= ? AND run_id=?",
            (window_start, window_end, run_id),
        )
    else:
        result = db.execute(
            "SELECT usage_json, metadata_json, provider, model_or_tool "
            "FROM runtime_cost_events WHERE event_type='llm' "
            "AND started_at >= ? AND started_at <= ?",
            (window_start, window_end),
        )
    rows = [dict(row) for row in result.fetchall()]
    token_fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_input_tokens",
        "provider_cache_read_input_tokens",
        "provider_cache_creation_input_tokens",
    )
    totals = {field: 0 for field in token_fields}
    availability = {field: 0 for field in token_fields}
    by_logical_agent: dict[str, dict[str, int]] = {}
    by_role: dict[str, dict[str, int]] = {}
    token_integrity_observation_counts: dict[str, int] = {}
    input_output_available_attempt_count = 0
    for row in rows:
        try:
            usage_envelope = json.loads(row["usage_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            usage_envelope = {}
        usage = usage_envelope.get("normalized") if isinstance(usage_envelope, dict) else None
        normalized = usage if isinstance(usage, dict) else normalize_usage(usage_envelope)
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        logical_agent = metadata.get("logical_agent") if isinstance(metadata, dict) else None
        observations = metadata.get("token_integrity_observations", []) if isinstance(metadata, dict) else []
        if isinstance(observations, list):
            for observation in observations:
                rule_id = observation.get("rule_id") if isinstance(observation, dict) else None
                if isinstance(rule_id, str):
                    token_integrity_observation_counts[rule_id] = (
                        token_integrity_observation_counts.get(rule_id, 0) + 1
                    )
        agent_bucket = by_logical_agent.setdefault(
            str(logical_agent or "unattributed"),
            {"llm_attempt_count": 0, **{field: 0 for field in token_fields}},
        )
        agent_bucket["llm_attempt_count"] += 1
        role_bucket = by_role.setdefault(
            role_for_logical_agent(logical_agent).value,
            {"llm_attempt_count": 0, **{field: 0 for field in token_fields}},
        )
        role_bucket["llm_attempt_count"] += 1
        if all(isinstance(normalized.get(field), int) for field in ("input_tokens", "output_tokens")):
            input_output_available_attempt_count += 1
        for field in token_fields:
            value = normalized.get(field)
            if isinstance(value, int) and value >= 0:
                totals[field] += value
                availability[field] += 1
                agent_bucket[field] += value
                role_bucket[field] += value
    return {
        "window_start": window_start,
        "window_end": window_end,
        "llm_attempt_count": len(rows),
        "input_output_available_attempt_count": input_output_available_attempt_count,
        "input_output_unavailable_attempt_count": len(rows) - input_output_available_attempt_count,
        "tokens": totals,
        "field_availability": availability,
        "by_logical_agent": by_logical_agent,
        "by_role": by_role,
        "token_integrity_observation_counts": token_integrity_observation_counts,
    }


def list_cost_summary(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    db = ensure_economics_schema()
    if run_id:
        result = db.execute(
            "SELECT * FROM runtime_cost_events "
            "WHERE started_at >= ? AND started_at <= ? "
            "AND run_id=? ORDER BY started_at,id",
            (window_start, window_end, run_id),
        )
    else:
        result = db.execute(
            "SELECT * FROM runtime_cost_events "
            "WHERE started_at >= ? AND started_at <= ? "
            "ORDER BY started_at,id",
            (window_start, window_end),
        )
    rows = [dict(row) for row in result.fetchall()]
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
            bucket["known_cost_usd"] += _float(row["estimated_cost_usd"]) or 0.0
    for bucket in by_type.values():
        bucket["known_cost_usd"] = round(bucket["known_cost_usd"], 12)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "event_count": len(rows),
        "known_cost_subtotal_usd": round(
            sum(_float(row["estimated_cost_usd"]) or 0.0 for row in rows), 12
        ),
        "unknown_event_count": sum(
            1
            for row in rows
            if row["estimated_cost_usd"] is None or row["cost_status"] == "unknown"
        ),
        "by_type": by_type,
        "events": rows,
    }


_PROVIDER_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
)


def _extract_usage(usage_json: str) -> dict[str, Any] | None:
    """Parse the persisted ``usage_json`` blob and return the normalized block.

    The ledger stores ``{"raw": <provider usage>, "normalized": {...}}``. The
    heatmap/scatter/stacked-chart aggregations only need the normalized block;
    if the blob is missing, malformed, or has no normalized key the caller skips
    the row and counts it as a parse error rather than raising.
    """
    if not usage_json:
        return None
    try:
        parsed = json.loads(usage_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    normalized = parsed.get("normalized")
    if not isinstance(normalized, dict):
        return None
    return normalized


def _date_only(iso_ts: str) -> str:
    """Return the UTC date (YYYY-MM-DD) for an ISO timestamp.

    The schema stores ``started_at`` as an ISO8601 string in UTC, but rejects
    are normalized defensively so a stray space or a Z suffix does not shift
    the bucketed day.
    """
    return _normalize_iso(iso_ts)[:10]


def _bucket_from_sql_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a SQL GROUP BY output row into the dashboard bucket schema.

    Shared by provider/model/daily aggregation so the output shape stays
    consistent across the three nesting levels. ``latency_count`` /
    ``known_cost_usd`` are finalized from the raw SQL aggregates.
    """
    latency_count = int(row["latency_count"])
    known_cost = float(row["known_cost_usd"])
    return {
        "event_count": int(row["event_count"]),
        "known_cost_usd": round(known_cost, 12),
        "unknown_count": int(row["unknown_count"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "reasoning_tokens": int(row["reasoning_tokens"]),
        "cached_input_tokens": int(row["cached_input_tokens"]),
        "success_count": int(row["success_count"]),
        "error_count": int(row["error_count"]),
        "avg_latency_ms": round(float(row["latency_sum"]) / latency_count, 1)
        if latency_count > 0
        else None,
    }


def list_cost_breakdown_by_provider(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
    event_limit: int = 10_000,
) -> dict[str, Any]:
    """Per-provider/model token breakdown + daily time series for the Usage dashboard.

    Reads LLM-type rows and aggregates token fields, cost, latency, and
    success/error counts in SQL (``GROUP BY`` + ``json_extract``) so the query
    uses the ``idx_runtime_cost_events_window`` index for an efficient range
    scan and returns only compact buckets — never the raw event rows. The
    persisted ``usage_json`` blob stores ``{"raw":..., "normalized": {...}}``;
    token fields are extracted via ``json_extract`` on the normalized block.

    Historical rows may predate the top-level ``cached_input_tokens`` mapping —
    their normalized blob only carries ``provider_cache_read_input_tokens``.  The
    SQL uses ``COALESCE(json_extract('$.normalized.cached_input_tokens'),
    json_extract('$.normalized.provider_cache_read_input_tokens'))`` so cached

    Results are cached server-side: finished UTC days for 60s, the still-open
    day for 5s.  The cache key incorporates all query parameters.
    tokens are not silently dropped.

    Rows whose ``usage_json`` has no ``normalized`` block still count toward
    ``event_count`` / ``known_cost_usd``; the ``parse_errors`` counter reflects
    rows where the blob exists but cannot be parsed (detected by comparing rows
    that have ``usage_json != '{}'`` against those where token extraction
    succeeded).
    """
    db = ensure_economics_schema()
    if not tracing_enabled():
        return {
            "window_start": window_start,
            "window_end": window_end,
            "tracing_enabled": False,
            "providers": {},
            "models": {},
            "daily_series": [],
            "parse_errors": 0,
            "event_count": 0,
        }

    # Cache lookup — key includes all parameters that affect the result
    cache_key = f"cbp:{window_start}:{window_end}:{run_id}:{event_limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    where = ["event_type='llm'", "started_at >= ?", "started_at <= ?"]
    params: list[Any] = [window_start, window_end]
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    where_sql = " AND ".join(where)

    # Token extraction: pull from the normalized block inside usage_json.
    # Cache fallback: prefer cached_input_tokens, fall back to
    # provider_cache_read_input_tokens for historical rows.
    token_expr = (
        "COALESCE(json_extract(usage_json,'$.normalized.%s'),0)"
    )
    cached_expr = (
        "COALESCE("
        "json_extract(usage_json,'$.normalized.cached_input_tokens'),"
        "json_extract(usage_json,'$.normalized.provider_cache_read_input_tokens'),"
        "0)"
    )

    group_sql = (
        "SELECT {dim_cols},"
        "COUNT(*) AS event_count,"
        "SUM(CASE WHEN estimated_cost_usd IS NOT NULL AND cost_status != 'unknown'"
        "    THEN estimated_cost_usd ELSE 0 END) AS known_cost_usd,"
        "SUM(CASE WHEN estimated_cost_usd IS NULL OR cost_status = 'unknown'"
        "    THEN 1 ELSE 0 END) AS unknown_count,"
        f"SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,"
        f"SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) AS error_count,"
        "SUM(CASE WHEN latency_ms IS NOT NULL THEN latency_ms ELSE 0 END) AS latency_sum,"
        "SUM(CASE WHEN latency_ms IS NOT NULL THEN 1 ELSE 0 END) AS latency_count,"
        f"SUM(CASE WHEN {token_expr % 'input_tokens'} IS NOT NULL AND {token_expr % 'input_tokens'} > 0"
        f"    THEN CAST({token_expr % 'input_tokens'} AS INTEGER) ELSE 0 END) AS input_tokens,"
        f"SUM(CASE WHEN {token_expr % 'output_tokens'} IS NOT NULL AND {token_expr % 'output_tokens'} > 0"
        f"    THEN CAST({token_expr % 'output_tokens'} AS INTEGER) ELSE 0 END) AS output_tokens,"
        f"SUM(CASE WHEN {token_expr % 'reasoning_tokens'} IS NOT NULL AND {token_expr % 'reasoning_tokens'} > 0"
        f"    THEN CAST({token_expr % 'reasoning_tokens'} AS INTEGER) ELSE 0 END) AS reasoning_tokens,"
        f"SUM(CASE WHEN {cached_expr} IS NOT NULL AND {cached_expr} > 0"
        f"    THEN CAST({cached_expr} AS INTEGER) ELSE 0 END) AS cached_input_tokens "
        "FROM runtime_cost_events "
        f"WHERE {where_sql} "
        "GROUP BY {group_cols}"
    )

    providers: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, dict[str, Any]]] = {}
    daily_map: dict[tuple[str, str], dict[str, Any]] = {}
    total_events = 0

    # Provider-level aggregation
    for row in db.execute(
        group_sql.format(
            dim_cols="provider",
            group_cols="provider",
        ),
        params,
    ):
        d = dict(row)
        total_events += int(d["event_count"])
        provider = d["provider"] or "unknown"
        providers[provider] = _bucket_from_sql_row(d)

    # (provider, model)-level aggregation
    for row in db.execute(
        group_sql.format(
            dim_cols="provider, model_or_tool AS model",
            group_cols="provider, model_or_tool",
        ),
        params,
    ):
        d = dict(row)
        provider = d["provider"] or "unknown"
        model = d["model"] or "unknown"
        models.setdefault(provider, {})[model] = _bucket_from_sql_row(d)

    # Daily series: (date, provider)-level aggregation
    daily_sql = (
        "SELECT substr(started_at,1,10) AS date, provider,"
        "COUNT(*) AS event_count,"
        "SUM(CASE WHEN estimated_cost_usd IS NOT NULL AND cost_status != 'unknown'"
        "    THEN estimated_cost_usd ELSE 0 END) AS known_cost_usd,"
        "SUM(CASE WHEN estimated_cost_usd IS NULL OR cost_status = 'unknown'"
        "    THEN 1 ELSE 0 END) AS unknown_count,"
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,"
        "SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) AS error_count,"
        "SUM(CASE WHEN latency_ms IS NOT NULL THEN latency_ms ELSE 0 END) AS latency_sum,"
        "SUM(CASE WHEN latency_ms IS NOT NULL THEN 1 ELSE 0 END) AS latency_count,"
        f"SUM(CASE WHEN {token_expr % 'input_tokens'} IS NOT NULL AND {token_expr % 'input_tokens'} > 0"
        f"    THEN CAST({token_expr % 'input_tokens'} AS INTEGER) ELSE 0 END) AS input_tokens,"
        f"SUM(CASE WHEN {token_expr % 'output_tokens'} IS NOT NULL AND {token_expr % 'output_tokens'} > 0"
        f"    THEN CAST({token_expr % 'output_tokens'} AS INTEGER) ELSE 0 END) AS output_tokens,"
        f"SUM(CASE WHEN {token_expr % 'reasoning_tokens'} IS NOT NULL AND {token_expr % 'reasoning_tokens'} > 0"
        f"    THEN CAST({token_expr % 'reasoning_tokens'} AS INTEGER) ELSE 0 END) AS reasoning_tokens,"
        f"SUM(CASE WHEN {cached_expr} IS NOT NULL AND {cached_expr} > 0"
        f"    THEN CAST({cached_expr} AS INTEGER) ELSE 0 END) AS cached_input_tokens "
        f"FROM runtime_cost_events "
        f"WHERE {where_sql} "
        f"GROUP BY substr(started_at,1,10), provider "
        f"ORDER BY substr(started_at,1,10), provider"
    )
    for row in db.execute(daily_sql, params):
        d = dict(row)
        day = d["date"]
        provider = d["provider"] or "unknown"
        daily_map[(day, provider)] = _bucket_from_sql_row(d)

    # parse_errors: count rows where usage_json has content but no normalized block
    # We detect this by comparing total LLM events vs events with parseable usage
    parse_errors = db.execute(
        f"SELECT COUNT(*) FROM runtime_cost_events "
        f"WHERE {where_sql} "
        f"  AND usage_json IS NOT NULL AND usage_json != '{{}}' "
        f"  AND json_extract(usage_json,'$.normalized') IS NULL",
        params,
    ).fetchone()[0]

    daily_series = [
        {
            "date": day,
            "provider": provider,
            **bucket,
        }
        for (day, provider), bucket in sorted(
            daily_map.items(),
            key=lambda item: (item[0][0], item[0][1]),
        )
    ]

    result = {
        "window_start": window_start,
        "window_end": window_end,
        "tracing_enabled": True,
        "event_count": total_events,
        "parse_errors": parse_errors or 0,
        "providers": providers,
        "models": models,
        "daily_series": daily_series,
    }

    ttl = (
        _CACHE_TTL_CURRENT_DAY
        if _window_end_is_today_utc(window_end)
        else _CACHE_TTL_FINISHED_DAY
    )
    _cache_set(cache_key, result, ttl=ttl)
    return result


def list_cost_breakdown_by_tool(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
    event_limit: int = 10_000,
) -> dict[str, Any]:
    """Per-tool call rollup for the Usage dashboard.

    Tool rows are mirrored into ``runtime_cost_events`` from ``agent_tool_calls``
    by the ``trg_agent_tool_call_cost_event`` trigger: ``provider`` is always
    ``"tool"`` and the tool name lives in ``model_or_tool``. ``status`` is
    ``recorded`` (permitted) or ``denied``. The trigger never writes a latency
    or usage blob, so this rollup carries no latency/token fields — those would
    be invented zeros.

    Costs come from ``tool_cost_profiles`` via ``cost_profile_id``; rows without
    a profile stay ``unknown`` and are counted, never dropped.

    Aggregation runs in SQL (``GROUP BY model_or_tool``) so the
    ``idx_runtime_cost_events_window`` index covers the range scan and only
    compact buckets cross the wire — never the raw rows.
    """
    db = ensure_economics_schema()
    if not tracing_enabled():
        return {
            "window_start": window_start,
            "window_end": window_end,
            "tracing_enabled": False,
            "tools": {},
            "event_count": 0,
        }

    # Cache lookup
    cache_key = f"cbt:{window_start}:{window_end}:{run_id}:{event_limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    where = ["event_type='tool'", "started_at >= ?", "started_at <= ?"]
    params: list[Any] = [window_start, window_end]
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    where_sql = " AND ".join(where)

    rows = [
        dict(row)
        for row in db.execute(
            "SELECT model_or_tool AS tool_name, "
            "COUNT(*) AS event_count, "
            "SUM(CASE WHEN estimated_cost_usd IS NOT NULL AND cost_status != 'unknown' "
            "    THEN estimated_cost_usd ELSE 0 END) AS known_cost_usd, "
            "SUM(CASE WHEN estimated_cost_usd IS NULL OR cost_status = 'unknown' "
            "    THEN 1 ELSE 0 END) AS unknown_count, "
            "SUM(CASE WHEN status != 'denied' THEN 1 ELSE 0 END) AS recorded_count, "
            "SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied_count "
            "FROM runtime_cost_events "
            f"WHERE {where_sql} "
            "GROUP BY model_or_tool "
            "LIMIT ?",
            (*params, int(event_limit)),
        ).fetchall()
    ]

    tools: dict[str, dict[str, Any]] = {}
    total_events = 0
    for row in rows:
        name = row["tool_name"] or "unknown"
        total_events += row["event_count"]
        tools[name] = {
            "event_count": int(row["event_count"]),
            "known_cost_usd": round(float(row["known_cost_usd"]), 12),
            "unknown_count": int(row["unknown_count"]),
            "recorded_count": int(row["recorded_count"]),
            "denied_count": int(row["denied_count"]),
        }

    result = {
        "window_start": window_start,
        "window_end": window_end,
        "tracing_enabled": True,
        "event_count": total_events,
        "tools": tools,
    }

    ttl = (
        _CACHE_TTL_CURRENT_DAY
        if _window_end_is_today_utc(window_end)
        else _CACHE_TTL_FINISHED_DAY
    )
    _cache_set(cache_key, result, ttl=ttl)
    return result
