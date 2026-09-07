"""Append-only runtime cost facts for LLM, tool, data, and infrastructure activity."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from aletheia.economics.agent_roles import role_for_logical_agent
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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


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


def add_catalog_pricing_snapshot(
    *,
    provider: str,
    model: str,
    input_usd_per_million: float,
    output_usd_per_million: float,
    reasoning_usd_per_million: float | None = None,
    cached_input_usd_per_million: float | None = None,
    cache_creation_input_usd_per_million: float | None = None,
    web_search_usd_per_request: float | None = None,
    effective_from: str | None = None,
    source: str,
    version: str,
) -> dict[str, Any]:
    """Persist one immutable LiteLLM public-catalog price estimate.

    Model prices have one source of truth: LiteLLM's public GitHub catalog.
    There is deliberately no manual/default source and no user-facing writer.
    """
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise ValueError("provider and model are required")
    if not source.startswith("litellm_public_median:"):
        raise ValueError("model pricing source must be the LiteLLM public catalog")
    if not version.startswith("catalog-model-family-"):
        raise ValueError("model pricing version must identify the catalog matching contract")
    rates = {
        "input_usd_per_million": input_usd_per_million,
        "output_usd_per_million": output_usd_per_million,
        "reasoning_usd_per_million": reasoning_usd_per_million,
        "cached_input_usd_per_million": cached_input_usd_per_million,
        "cache_creation_input_usd_per_million": cache_creation_input_usd_per_million,
        "web_search_usd_per_request": web_search_usd_per_request,
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
        "cache_creation_input_usd_per_million": parsed_rates["cache_creation_input_usd_per_million"],
        "web_search_usd_per_request": parsed_rates["web_search_usd_per_request"],
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
            cached_input_usd_per_million, cache_creation_input_usd_per_million,
            web_search_usd_per_request, currency, source, version, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        tuple(row.values()),
    )
    stored = db.execute("SELECT * FROM pricing_snapshots WHERE id=?", (snapshot_id,)).fetchone()
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
        (*params, _bounded_limit(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_pricing_snapshot(provider: str, model: str, at: str) -> dict[str, Any] | None:
    db = ensure_economics_schema()
    row = db.execute(
        """SELECT * FROM pricing_snapshots
           WHERE provider=? AND model=? AND datetime(effective_from) <= datetime(?)
           ORDER BY datetime(effective_from) DESC, rowid DESC LIMIT 1""",
        (provider, model, at),
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
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    server_tool_use = usage.get("server_tool_use") or {}
    server_side_tools = usage.get("server_side_tool_usage_details") or {}

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
    top_level_cache_read = token("cache_read_input_tokens")
    cache_read = (
        top_level_cache_read
        if top_level_cache_read is not None
        else detail(prompt_details, "cached_tokens", "cached_input_tokens", "cache_read_input_tokens")
    )
    top_level_cache_creation = token("cache_creation_input_tokens", "cache_write_input_tokens")
    cache_creation = (
        top_level_cache_creation
        if top_level_cache_creation is not None
        else detail(
            prompt_details,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_write_input_tokens",
            "cache_write_tokens",
        )
    )
    web_search_requests = detail(prompt_details, "web_search_requests")
    if web_search_requests is None:
        web_search_requests = detail(server_tool_use, "web_search_requests")
    if web_search_requests is None:
        web_search_requests = detail(server_side_tools, "web_search_calls")
    return {
        "input_tokens": token("prompt_tokens", "input_tokens"),
        "output_tokens": token("completion_tokens", "output_tokens"),
        "total_tokens": token("total_tokens"),
        "reasoning_tokens": (
            top_level_reasoning if top_level_reasoning is not None else detail(completion_details, "reasoning_tokens")
        ),
        # Providers disagree on where cache hits live: OpenAI/Anthropic report
        # them under prompt_tokens_details.cached_tokens, while DeepSeek-style
        # providers (incl. sensenova) put them at the top level as
        # cache_read_input_tokens. Fall back so both shapes normalize to
        # cached_input_tokens instead of silently zeroing the counter.
        "cached_input_tokens": (cache_read),
        "provider_cache_read_input_tokens": cache_read,
        "provider_cache_creation_input_tokens": cache_creation,
        "web_search_requests": web_search_requests,
    }


def provider_reported_cost_usd(raw_usage: Any) -> float | None:
    """Return only an explicitly USD-denominated provider amount."""
    if not isinstance(raw_usage, dict):
        return None
    for key in ("provider_cost_usd", "total_cost_usd", "cost_usd"):
        value = _float(raw_usage.get(key))
        if value is not None and value >= 0:
            return value
    return None


def estimate_llm_cost(
    usage: dict[str, int | None], pricing: dict[str, Any] | None
) -> tuple[float | None, str, dict[str, Any]]:
    if pricing is None:
        return None, "unknown", {"reason": "pricing_snapshot_missing"}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    reasoning_tokens = usage.get("reasoning_tokens") or 0
    provider_cache_read = usage.get("provider_cache_read_input_tokens")
    cache_read = provider_cache_read if provider_cache_read is not None else usage.get("cached_input_tokens")
    cache_creation = usage.get("provider_cache_creation_input_tokens")
    web_search_requests = usage.get("web_search_requests") or 0
    if input_tokens is None or output_tokens is None:
        return None, "unknown", {"reason": "usage_incomplete"}
    cache_read_tokens = cache_read or 0
    cache_creation_tokens = cache_creation or 0
    if cache_read_tokens + cache_creation_tokens > input_tokens:
        return None, "unknown", {"reason": "cache_tokens_exceed_input_tokens"}

    input_rate = _float(pricing.get("input_usd_per_million"))
    output_rate = _float(pricing.get("output_usd_per_million"))
    reasoning_rate = _float(pricing.get("reasoning_usd_per_million"))
    cached_rate = _float(pricing.get("cached_input_usd_per_million"))
    cache_creation_rate = _float(pricing.get("cache_creation_input_usd_per_million"))
    web_search_rate = _float(pricing.get("web_search_usd_per_request"))
    if input_rate is None or output_rate is None:
        return None, "unknown", {"reason": "positive_special_rate_missing", "field": "base_tokens"}
    if cache_read_tokens > 0 and cached_rate is None:
        return None, "unknown", {"reason": "positive_special_rate_missing", "field": "cache_read"}
    if cache_creation_tokens > 0 and cache_creation_rate is None:
        return None, "unknown", {"reason": "positive_special_rate_missing", "field": "cache_creation"}
    if reasoning_tokens > output_tokens:
        return None, "unknown", {"reason": "reasoning_tokens_exceed_output_tokens"}
    if reasoning_tokens > 0 and reasoning_rate is None:
        return None, "unknown", {"reason": "positive_special_rate_missing", "field": "reasoning"}
    if web_search_requests and web_search_rate is None:
        return None, "unknown", {"reason": "positive_special_rate_missing", "field": "web_search"}

    ordinary_input = input_tokens - cache_read_tokens - cache_creation_tokens
    ordinary_output = output_tokens - reasoning_tokens if reasoning_rate is not None else output_tokens
    cost = ordinary_input * input_rate / 1_000_000
    cost += cache_read_tokens * (cached_rate or 0.0) / 1_000_000
    cost += cache_creation_tokens * (cache_creation_rate or 0.0) / 1_000_000
    cost += ordinary_output * output_rate / 1_000_000
    if reasoning_rate is not None:
        cost += reasoning_tokens * reasoning_rate / 1_000_000
    cost += web_search_requests * (web_search_rate or 0.0)
    return (
        round(cost, 12),
        "estimated",
        {
            "reason": "formula_priced",
            "ordinary_input_tokens": ordinary_input,
            "ordinary_output_tokens": ordinary_output,
            "cache_read_input_tokens": cache_read_tokens,
            "cache_creation_input_tokens": cache_creation_tokens,
            "reasoning_priced_separately": reasoning_rate is not None,
            "web_search_requests": web_search_requests,
            "web_search_cost_usd": web_search_requests * (web_search_rate or 0.0),
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
    pricing_observation: Any = None,
) -> str | None:
    """Persist one physical provider attempt. Measurement failure never blocks trading."""
    if not tracing_enabled():
        return None
    try:
        usage = normalize_usage(raw_usage)
        provider_cost = provider_reported_cost_usd(raw_usage)
        if provider_cost is not None:
            estimated_cost, cost_status = provider_cost, "known"
            cost_meta = {"reason": "provider_reported"}
            pricing = resolve_pricing_snapshot(provider, model, started_at)
        else:
            from aletheia.economics.pricing_sync import resolve_pricing_from_observation

            pricing, pricing_reason = resolve_pricing_from_observation(provider, model, started_at, pricing_observation)
            estimated_cost, cost_status, cost_meta = estimate_llm_cost(usage, pricing)
            if (
                estimated_cost is None
                and cost_meta.get("reason") == "positive_special_rate_missing"
                and pricing_observation is not None
            ):
                catalog_pricing, _ = resolve_pricing_from_observation(
                    provider,
                    model,
                    started_at,
                    pricing_observation,
                    use_existing=False,
                )
                if catalog_pricing is not None:
                    pricing = catalog_pricing
                    estimated_cost, cost_status, cost_meta = estimate_llm_cost(usage, pricing)
            if pricing is None:
                cost_meta = {"reason": pricing_reason}
        if status != "success" and not raw_usage:
            estimated_cost, cost_status = None, "unknown"
            cost_meta = {"reason": "usage_incomplete"}
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
            _json(
                {
                    **(metadata or {}),
                    "cost_calculation": cost_meta,
                    "token_integrity_observations": observe_token_integrity(usage),
                }
            ),
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


def aggregate_agent_run_cost(run_id: str) -> dict[str, Any]:
    db = ensure_economics_schema()
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM runtime_cost_events WHERE run_id=? ORDER BY started_at,attempt_index,id",
            (run_id,),
        ).fetchall()
    ]
    known = sum((_float(row["estimated_cost_usd"]) or 0.0) for row in rows if row["estimated_cost_usd"] is not None)
    unknown = sum(1 for row in rows if row["cost_status"] == "unknown" or row["estimated_cost_usd"] is None)
    llm_rows = [row for row in rows if row["event_type"] == "llm"]
    final_model = next(
        (row["model_or_tool"] for row in reversed(llm_rows) if row["status"] == "success"),
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
            "AND replace(started_at,' ','T') >= replace(?,' ','T') "
            "AND replace(started_at,' ','T') <= replace(?,' ','T') AND run_id=?",
            (window_start, window_end, run_id),
        )
    else:
        result = db.execute(
            "SELECT usage_json, metadata_json, provider, model_or_tool "
            "FROM runtime_cost_events WHERE event_type='llm' "
            "AND replace(started_at,' ','T') >= replace(?,' ','T') "
            "AND replace(started_at,' ','T') <= replace(?,' ','T')",
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
                    token_integrity_observation_counts[rule_id] = token_integrity_observation_counts.get(rule_id, 0) + 1
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
            "WHERE replace(started_at,' ','T') >= replace(?,' ','T') "
            "AND replace(started_at,' ','T') < replace(?,' ','T') "
            "AND run_id=? ORDER BY started_at,id",
            (window_start, window_end, run_id),
        )
    else:
        result = db.execute(
            "SELECT * FROM runtime_cost_events "
            "WHERE replace(started_at,' ','T') >= replace(?,' ','T') "
            "AND replace(started_at,' ','T') < replace(?,' ','T') "
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
        "known_cost_subtotal_usd": round(sum(_float(row["estimated_cost_usd"]) or 0.0 for row in rows), 12),
        "unknown_event_count": sum(
            1 for row in rows if row["estimated_cost_usd"] is None or row["cost_status"] == "unknown"
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


def list_cost_breakdown_by_provider(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Per-provider/model token breakdown + daily time series for the Usage dashboard.

    Unlike :func:`list_cost_summary` (which groups by ``event_type`` only), this
    reads LLM-type rows, parses the persisted ``usage_json`` blob, and rolls the
    normalized token fields up by ``provider`` and ``(provider, model)`` as well
    as by UTC date. Each bucket also carries success/error counts and average
    latency so the dashboard can show per-model call health without scanning raw
    rows. The output is intentionally compact (no raw events returned) so the
    dashboard can poll it at the same cadence as ``/api/economics/costs`` without
    paying for a 5 KB row back per call.

    Rows whose ``usage_json`` cannot be parsed are still credited to the
    provider's ``event_count`` and ``known_cost_usd`` totals (cost is always on
    the row itself), but their token fields are skipped and surfaced via
    ``parse_errors`` so an operator can see drift without an exception masking
    the rest of the response.
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
    where = [
        "event_type='llm'",
        "replace(started_at,' ','T') >= replace(?,' ','T')",
        "replace(started_at,' ','T') <= replace(?,' ','T')",
    ]
    params: list[Any] = [window_start, window_end]
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    rows = db.execute(
        "SELECT provider, model_or_tool, started_at, latency_ms, status, "
        "estimated_cost_usd, cost_status, usage_json "
        "FROM runtime_cost_events WHERE " + " AND ".join(where),
        params,
    )

    providers: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, dict[str, Any]]] = {}
    daily_map: dict[tuple[str, str], dict[str, Any]] = {}
    event_count = 0
    parse_errors = 0

    def _new_bucket() -> dict[str, Any]:
        return {
            "event_count": 0,
            "known_cost_usd": 0.0,
            "unknown_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_input_tokens": 0,
            "success_count": 0,
            "error_count": 0,
            "avg_latency_ms": None,
            "latency_ms_sum": 0.0,
            "latency_count": 0,
        }

    def _apply(
        bucket: dict[str, Any],
        *,
        is_unknown: bool,
        expense: Any,
        usage: dict[str, Any] | None,
        status: str,
        latency_ms: Any,
    ) -> None:
        bucket["event_count"] += 1
        if is_unknown:
            bucket["unknown_count"] += 1
        else:
            bucket["known_cost_usd"] += float(expense)
        if status == "success":
            bucket["success_count"] += 1
        else:
            bucket["error_count"] += 1
        if latency_ms is not None:
            try:
                bucket["latency_ms_sum"] += float(latency_ms)
                bucket["latency_count"] += 1
            except (TypeError, ValueError):
                pass
        if usage is not None:
            for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
                value = usage.get(field)
                if isinstance(value, (int, float)) and value > 0:
                    bucket[field] += int(value)
            cached = usage.get("cached_input_tokens")
            if cached is None:
                # Historical rows may predate the top-level cache_read mapping:
                # their normalized blob only carries
                # provider_cache_read_input_tokens. Fall back so cached tokens
                # are not silently dropped from the dashboard.
                cached = usage.get("provider_cache_read_input_tokens")
            if isinstance(cached, (int, float)) and cached > 0:
                bucket["cached_input_tokens"] += int(cached)

    def _finalize_bucket(bucket: dict[str, Any]) -> None:
        bucket["known_cost_usd"] = round(bucket["known_cost_usd"], 12)
        if bucket["latency_count"]:
            bucket["avg_latency_ms"] = round(bucket["latency_ms_sum"] / bucket["latency_count"], 1)
        else:
            bucket["avg_latency_ms"] = None
        bucket.pop("latency_ms_sum", None)
        bucket.pop("latency_count", None)

    for db_row in rows:
        event_count += 1
        row = dict(db_row)
        provider = row.get("provider") or "unknown"
        model = row.get("model_or_tool") or "unknown"
        started_at = row.get("started_at") or _utcnow()
        usage = _extract_usage(row.get("usage_json") or "")
        expense = row.get("estimated_cost_usd")
        status = row.get("status") or "error"
        latency_ms = row.get("latency_ms")
        is_unknown = expense is None or row.get("cost_status") == "unknown"

        if usage is None and row.get("usage_json"):
            parse_errors += 1

        prov_bucket = providers.setdefault(provider, _new_bucket())
        _apply(
            prov_bucket,
            is_unknown=is_unknown,
            expense=expense,
            usage=usage,
            status=status,
            latency_ms=latency_ms,
        )

        model_bucket = models.setdefault(provider, {}).setdefault(model, _new_bucket())
        _apply(
            model_bucket,
            is_unknown=is_unknown,
            expense=expense,
            usage=usage,
            status=status,
            latency_ms=latency_ms,
        )

        day = _date_only(started_at)
        day_key = (day, provider)
        day_bucket = daily_map.setdefault(day_key, _new_bucket())
        _apply(
            day_bucket,
            is_unknown=is_unknown,
            expense=expense,
            usage=usage,
            status=status,
            latency_ms=latency_ms,
        )

    for prov_bucket in providers.values():
        _finalize_bucket(prov_bucket)
    for provider_models in models.values():
        for model_bucket in provider_models.values():
            _finalize_bucket(model_bucket)
    for day_bucket in daily_map.values():
        _finalize_bucket(day_bucket)

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

    return {
        "window_start": window_start,
        "window_end": window_end,
        "tracing_enabled": True,
        "event_count": event_count,
        "parse_errors": parse_errors,
        "providers": providers,
        "models": models,
        "daily_series": daily_series,
    }


def list_cost_breakdown_by_tool(
    *,
    window_start: str,
    window_end: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Per-tool call rollup for the Usage dashboard.

    Tool rows are mirrored into ``runtime_cost_events`` from ``agent_tool_calls``
    by the ``trg_agent_tool_call_cost_event`` trigger: ``provider`` is always
    ``"tool"`` and the tool name lives in ``model_or_tool``. ``status`` is
    ``recorded`` (permitted) or ``denied``. Duration is copied when measured;
    token usage is never invented.

    Aletheia-local tools carry zero external marginal cost. Provider-native
    built-in tool charges are priced on the corresponding LLM event from
    LiteLLM catalog rates and measured usage counters, preventing double count.
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
    where = [
        "event_type='tool'",
        "replace(started_at,' ','T') >= replace(?,' ','T')",
        "replace(started_at,' ','T') <= replace(?,' ','T')",
    ]
    params: list[Any] = [window_start, window_end]
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    rows = db.execute(
        "SELECT model_or_tool, status, estimated_cost_usd, cost_status "
        "FROM runtime_cost_events WHERE " + " AND ".join(where),
        params,
    )

    def _new_tool_bucket() -> dict[str, Any]:
        return {
            "event_count": 0,
            "known_cost_usd": 0.0,
            "unknown_count": 0,
            "recorded_count": 0,
            "denied_count": 0,
        }

    tools: dict[str, dict[str, Any]] = {}
    event_count = 0
    for db_row in rows:
        event_count += 1
        row = dict(db_row)
        name = row.get("model_or_tool") or "unknown"
        bucket = tools.setdefault(name, _new_tool_bucket())
        bucket["event_count"] += 1
        if (row.get("status") or "recorded") == "denied":
            bucket["denied_count"] += 1
        else:
            bucket["recorded_count"] += 1
        # Historical local-tool rows may carry retired manual profiles. The
        # read model ignores them: local wrappers are zero external cost and
        # provider-native tool charges live on catalog-priced LLM events.
        bucket["known_cost_usd"] += 0.0

    for bucket in tools.values():
        bucket["known_cost_usd"] = round(bucket["known_cost_usd"], 12)

    return {
        "window_start": window_start,
        "window_end": window_end,
        "tracing_enabled": True,
        "event_count": event_count,
        "tools": tools,
    }
