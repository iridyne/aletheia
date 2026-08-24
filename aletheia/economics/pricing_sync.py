"""Refresh provider-independent estimates from LiteLLM's public price catalog."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

import httpx

from aletheia.config.loader import get_data_dir
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import add_catalog_pricing_snapshot


LITELLM_PRICE_MAP_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_MIN_MODEL_COUNT = 50
_MIN_RELATIVE_SIZE = 0.5
_SOURCE_PREFIX = "litellm_public_median:"
_MATCH_VERSION = "catalog-model-family-v2"
_FAMILY_SUFFIXES = ("instruct", "chat")


def _normalize(s: str) -> str:
    """Strip all non-alphanumeric chars and lowercase for catalog identity."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _build_price_index(
    price_map: dict[str, Any],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Index priced catalog entries by normalized model name and aliases.

    LiteLLM keys are either bare model names or ``provider/.../model``. The
    catalog is used as an independent public reference: the runtime API base is
    intentionally irrelevant. Zero-cost entries remain valid prices.
    """
    index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, entry in price_map.items():
        if not isinstance(entry, dict):
            continue
        if _rate(entry.get("input_cost_per_token")) is None or _rate(entry.get("output_cost_per_token")) is None:
            continue
        names = [key.split("/")[-1]]
        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            names.extend(alias for alias in aliases if isinstance(alias, str))
        for name in names:
            bucket = index.setdefault(_normalize(name), [])
            candidate = (key, entry)
            if candidate not in bucket:
                bucket.append(candidate)
    return index


def _family_match(needle: str, stem: str) -> bool:
    """Allow only conservative deployment/version suffix differences.

    Exact model identity wins. Family fallback accepts numeric deployment
    suffixes (dates, parameter sizes) and explicit ``instruct``/``chat``
    suffixes, but rejects semantic variants such as ``mini`` and ``thinking``.
    """
    if stem.startswith(needle):
        suffix = stem[len(needle):]
    elif needle.startswith(stem):
        suffix = needle[len(stem):]
    else:
        return False
    return bool(suffix) and (suffix[0].isdigit() or suffix.startswith(_FAMILY_SUFFIXES))


def _match_entries(
    index: dict[str, list[tuple[str, dict[str, Any]]]],
    model: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Match a runtime model against the provider-independent public catalog."""
    needle = _normalize(model)
    matches: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for stem, entries in index.items():
        if stem != needle and not _family_match(needle, stem):
            continue
        for key, entry in entries:
            if key not in seen:
                seen.add(key)
                matches.append((key, entry))
    return matches

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


def _rate(value: object) -> float | None:
    try:
        rate = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate >= 0 else None


def _representative_rates(
    entries: list[tuple[str, dict[str, Any]]],
) -> dict[str, float | int] | None:
    """Aggregate matched LiteLLM entries into median rates.

    Accepts the list of ``(litellm_key, entry_dict)`` tuples produced by
    ``_match_entries``.  Returns ``None`` when no entry has both input and
    output rates.
    """
    paired = [
        (input_rate, output_rate, entry)
        for _, entry in entries
        if (input_rate := _rate(entry.get("input_cost_per_token"))) is not None
        and (output_rate := _rate(entry.get("output_cost_per_token"))) is not None
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
            if (rate := _rate(entry.get(source_key))) is not None
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


def _may_write_catalog_snapshot(provider: str, model: str, version: str) -> bool:
    db = ensure_economics_schema()
    row = db.execute(
        "SELECT version FROM pricing_snapshots WHERE provider=? AND model=? "
        "ORDER BY datetime(effective_from) DESC,rowid DESC LIMIT 1",
        (provider, model),
    ).fetchone()
    return row is None or str(row[0] or "") != version


def refresh_pricing(
    fetcher: Callable[[], tuple[dict[str, Any], bytes]] = _fetch_price_map,
) -> dict[str, Any]:
    """Fetch public prices and append forward-only estimated snapshots.

    The catalog is independent of the configured runtime provider or API base.
    Exact model/alias matches are preferred; conservative family matching
    handles deployment suffixes. Multiple provider listings are aggregated as
    a public-market median and never labelled provider-billed or known cost.
    """
    price_map, content = fetcher()
    _save_cache(content)
    version = _MATCH_VERSION + ":sha256:" + hashlib.sha256(content).hexdigest()
    effective_from = _utcnow()
    source = _SOURCE_PREFIX + LITELLM_PRICE_MAP_URL
    created = matched = 0
    unmatched: list[str] = []
    samples: dict[str, dict[str, float | int]] = {}

    index = _build_price_index(price_map)

    for provider, model in _runtime_models():
        entries = _match_entries(index, model)
        rates = _representative_rates(entries) if entries else None
        if rates is None:
            unmatched.append(f"{provider}/{model}")
            continue
        matched += 1
        samples[f"{provider}/{model}"] = rates
        if not _may_write_catalog_snapshot(provider, model, version):
            continue
        add_catalog_pricing_snapshot(
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
