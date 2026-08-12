"""Refresh approximate model pricing from LiteLLM's public model-cost map."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

import httpx

from aletheia.config.loader import get_data_dir
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import add_pricing_snapshot


LITELLM_PRICE_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_MIN_MODEL_COUNT = 50
_MIN_RELATIVE_SIZE = 0.5
_SOURCE_PREFIX = "litellm_public_median:"
_MODEL_MARKERS = {
    "qwen35-122b-a10b": "qwen3.5-122b-a10b",
    "qwen3.5-122b-a10b": "qwen3.5-122b-a10b",
    "llama-4-maverick": "llama-4-maverick",
}

_state_lock = threading.Lock()
_worker: threading.Thread | None = None
_state: dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "last_success_at": None,
    "error": None,
    "source_url": LITELLM_PRICE_MAP_URL,
    "version": None,
    "fetched_model_count": None,
    "matched_model_count": 0,
    "snapshots_created": 0,
    "unmatched_models": [],
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path() -> Path:
    return get_data_dir() / "cache" / "litellm" / "model_prices_and_context_window.json"


def _load_cached_count() -> int | None:
    try:
        value = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return len(value) if isinstance(value, dict) else None


def _save_cache(content: bytes) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _fetch_price_map() -> tuple[dict[str, Any], bytes]:
    response = httpx.get(LITELLM_PRICE_MAP_URL, timeout=5, follow_redirects=True)
    response.raise_for_status()
    content = response.content
    parsed = response.json()
    if not isinstance(parsed, dict) or len(parsed) < _MIN_MODEL_COUNT:
        raise ValueError("LiteLLM price map is missing or unexpectedly small")
    cached_count = _load_cached_count()
    if cached_count and len(parsed) < cached_count * _MIN_RELATIVE_SIZE:
        raise ValueError("LiteLLM price map shrank unexpectedly")
    return parsed, content


def _positive_rate(value: object) -> float | None:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _representative_rates(
    price_map: dict[str, Any], marker: str
) -> dict[str, float | int] | None:
    matches = [
        value
        for key, value in price_map.items()
        if marker in key.lower() and isinstance(value, dict)
    ]
    paired = [
        (input_rate, output_rate, value)
        for value in matches
        if (input_rate := _positive_rate(value.get("input_cost_per_token")))
        is not None
        and (output_rate := _positive_rate(value.get("output_cost_per_token")))
        is not None
    ]
    if not paired:
        return None
    result: dict[str, float | int] = {
        "input_usd_per_million": median(row[0] for row in paired) * 1_000_000,
        "output_usd_per_million": median(row[1] for row in paired) * 1_000_000,
        "sample_count": len(paired),
        "input_min_usd_per_million": min(row[0] for row in paired) * 1_000_000,
        "input_max_usd_per_million": max(row[0] for row in paired) * 1_000_000,
        "output_min_usd_per_million": min(row[1] for row in paired) * 1_000_000,
        "output_max_usd_per_million": max(row[1] for row in paired) * 1_000_000,
    }
    for source_key, target_key in (
        ("cache_read_input_token_cost", "cached_input_usd_per_million"),
        ("output_cost_per_reasoning_token", "reasoning_usd_per_million"),
    ):
        values = [
            rate
            for _, _, entry in paired
            if (rate := _positive_rate(entry.get(source_key))) is not None
        ]
        if values:
            result[target_key] = median(values) * 1_000_000
    return result


def _runtime_models() -> list[tuple[str, str]]:
    db = ensure_economics_schema()
    return [
        (str(row[0]), str(row[1]))
        for row in db.execute(
            "SELECT DISTINCT provider,model_or_tool FROM runtime_cost_events "
            "WHERE event_type='llm' AND provider IS NOT NULL AND model_or_tool IS NOT NULL"
        ).fetchall()
    ]


def _may_write_automatic_snapshot(provider: str, model: str, version: str) -> bool:
    db = ensure_economics_schema()
    row = db.execute(
        "SELECT source,version FROM pricing_snapshots WHERE provider=? AND model=? "
        "ORDER BY datetime(effective_from) DESC,rowid DESC LIMIT 1",
        (provider, model),
    ).fetchone()
    if row is None:
        return True
    source, current_version = str(row[0] or ""), str(row[1] or "")
    return source.startswith(_SOURCE_PREFIX) and current_version != version


def refresh_pricing(
    fetcher: Callable[[], tuple[dict[str, Any], bytes]] = _fetch_price_map,
) -> dict[str, Any]:
    """Fetch, aggregate, and append forward-only estimated price snapshots."""
    price_map, content = fetcher()
    _save_cache(content)
    version = "sha256:" + hashlib.sha256(content).hexdigest()
    effective_from = _utcnow()
    source = _SOURCE_PREFIX + LITELLM_PRICE_MAP_URL
    created = matched = 0
    unmatched: list[str] = []
    samples: dict[str, dict[str, float | int]] = {}

    for provider, model in _runtime_models():
        marker = _MODEL_MARKERS.get(model.lower())
        rates = _representative_rates(price_map, marker) if marker else None
        if rates is None:
            unmatched.append(f"{provider}/{model}")
            continue
        matched += 1
        samples[f"{provider}/{model}"] = rates
        if not _may_write_automatic_snapshot(provider, model, version):
            continue
        add_pricing_snapshot(
            provider=provider,
            model=model,
            input_usd_per_million=float(rates["input_usd_per_million"]),
            output_usd_per_million=float(rates["output_usd_per_million"]),
            reasoning_usd_per_million=(
                float(rates["reasoning_usd_per_million"])
                if "reasoning_usd_per_million" in rates
                else None
            ),
            cached_input_usd_per_million=(
                float(rates["cached_input_usd_per_million"])
                if "cached_input_usd_per_million" in rates
                else None
            ),
            effective_from=effective_from,
            source=source,
            version=version,
        )
        created += 1
    return {
        "version": version,
        "fetched_model_count": len(price_map),
        "matched_model_count": matched,
        "snapshots_created": created,
        "unmatched_models": sorted(unmatched),
        "samples": samples,
    }


def pricing_refresh_status() -> dict[str, Any]:
    with _state_lock:
        return dict(_state)


def _refresh_worker(trigger: str) -> None:
    try:
        result = refresh_pricing()
    except Exception as exc:  # network/reference data must not affect runtime readiness
        with _state_lock:
            _state.update(
                status="failed",
                completed_at=_utcnow(),
                error=f"{type(exc).__name__}: {exc}",
            )
        return
    completed_at = _utcnow()
    with _state_lock:
        _state.update(
            status="succeeded",
            completed_at=completed_at,
            last_success_at=completed_at,
            error=None,
            trigger=trigger,
            **{key: value for key, value in result.items() if key != "samples"},
        )
    try:
        from aletheia.files.jsonl import write_audit

        write_audit(
            {
                "actor": "system",
                "source": trigger,
                "event_type": "pricing_refresh_applied",
                "entity_type": "pricing_reference",
                "entity_id": "litellm_public_map",
                "after": {
                    "version": result["version"],
                    "matched_model_count": result["matched_model_count"],
                    "snapshots_created": result["snapshots_created"],
                },
                "message": "Applied LiteLLM public prices as forward-only estimates.",
            }
        )
    except Exception:
        pass


def start_pricing_refresh(*, trigger: str) -> dict[str, Any]:
    """Start one bounded daemon worker; repeated calls share the active refresh."""
    global _worker
    with _state_lock:
        if _worker is not None and _worker.is_alive():
            return dict(_state)
        _state.update(
            status="running",
            started_at=_utcnow(),
            completed_at=None,
            error=None,
            trigger=trigger,
        )
        _worker = threading.Thread(
            target=_refresh_worker,
            args=(trigger,),
            name="aletheia-pricing-refresh",
            daemon=True,
        )
        _worker.start()
        return dict(_state)
