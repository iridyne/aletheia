"""Refresh provider-independent estimates from LiteLLM's public price catalog."""

import logging

logger = logging.getLogger("aletheia.economics.pricing_sync")

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

from aletheia.config.loader import get_data_dir
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import add_catalog_pricing_snapshot
from aletheia.storage.time_utils import utcnow_iso as _utcnow


LITELLM_PRICE_MAP_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
_MIN_MODEL_COUNT = 50
_MIN_RELATIVE_SIZE = 0.5
_SOURCE_PREFIX = "litellm_public_median:"
_MATCH_VERSION = "catalog-model-family-v2"
_FAMILY_SUFFIXES = ("instruct", "chat")
_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
_catalog_fetcher: Callable[[], tuple[dict[str, Any], bytes]] | None = None


def configure_pricing_fetcher(fetcher: Callable[[], tuple[dict[str, Any], bytes]] | None) -> None:
    """Bind optional public Data acquisition; Core retains pricing evidence authority."""
    global _catalog_fetcher
    _catalog_fetcher = fetcher


@dataclass(frozen=True)
class CatalogObservation:
    """Content-addressed catalog evidence captured before a provider attempt."""

    content: bytes
    catalog_sha256: str
    observed_at: str
    entry_count: int
    source_url: str
    match_version: str

    def price_map(self) -> dict[str, Any]:
        parsed = json.loads(self.content)
        return parsed if isinstance(parsed, dict) else {}


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
        names = _catalog_names(key)
        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str):
                    names.extend(_catalog_names(alias))
        for name in names:
            bucket = index.setdefault(_normalize(name), [])
            candidate = (key, entry)
            if candidate not in bucket:
                bucket.append(candidate)
    return index


def _catalog_names(name: str) -> list[str]:
    tail = name.split("/")[-1]
    names = [tail]
    parts = tail.split(".")
    if len(parts) >= 3:
        names.append(".".join(parts[2:]))
    return names


def _family_match(needle: str, stem: str) -> bool:
    """Allow only conservative deployment/version suffix differences.

    Exact model identity wins. Family fallback accepts numeric deployment
    suffixes (dates, parameter sizes) and explicit ``instruct``/``chat``
    suffixes, but rejects semantic variants such as ``mini`` and ``thinking``.
    """
    if stem.startswith(needle):
        suffix = stem[len(needle) :]
    elif needle.startswith(stem):
        suffix = needle[len(stem) :]
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
    exact = index.get(needle, [])
    if exact:
        for key, entry in exact:
            if key not in seen:
                seen.add(key)
                matches.append((key, entry))
        return matches
    for stem, entries in index.items():
        if not _family_match(needle, stem):
            continue
        for key, entry in entries:
            if key not in seen:
                seen.add(key)
                matches.append((key, entry))
    return matches


_state_lock = threading.Lock()
_worker: threading.Thread | None = None
_active_catalog: CatalogObservation | None = None
_negative_refresh: dict[tuple[str, str, str], float] = {}
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


class PricingRefreshStartError(RuntimeError):
    """Raised when the background pricing refresh worker cannot be admitted."""




def _cache_path() -> Path:
    return get_data_dir() / "cache" / "litellm" / "model_prices_and_context_window.json"


def _cache_metadata_path() -> Path:
    return _cache_path().with_suffix(".metadata.json")


def _load_cached_count() -> int | None:
    try:
        value = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return len(value) if isinstance(value, dict) else None


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _observation(content: bytes, observed_at: str) -> CatalogObservation:
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("LiteLLM cached price map is invalid")
    return CatalogObservation(
        content=content,
        catalog_sha256=hashlib.sha256(content).hexdigest(),
        observed_at=observed_at,
        entry_count=len(parsed),
        source_url=LITELLM_PRICE_MAP_URL,
        match_version=_MATCH_VERSION,
    )


def _metadata(observation: CatalogObservation) -> bytes:
    return json.dumps(
        {
            "catalog_sha256": observation.catalog_sha256,
            "observed_at": observation.observed_at,
            "entry_count": observation.entry_count,
            "source_url": observation.source_url,
            "match_version": observation.match_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _activate(observation: CatalogObservation) -> CatalogObservation:
    global _active_catalog
    with _state_lock:
        _active_catalog = observation
    return observation


def _save_cache(content: bytes, observed_at: str | None = None) -> CatalogObservation:
    observation = _observation(content, observed_at or _utcnow())
    _atomic_write(_cache_path(), content)
    # Metadata is written last: a crash can leave an untrusted raw file, never a
    # trusted receipt for bytes that were not durably installed.
    _atomic_write(_cache_metadata_path(), _metadata(observation))
    return _activate(observation)


def _legacy_observed_at(catalog_sha256: str) -> str | None:
    """Recover old cache trust only from an already-persisted matching snapshot."""
    try:
        row = (
            ensure_economics_schema()
            .execute(
                "SELECT effective_from FROM pricing_snapshots "
                "WHERE source LIKE ? AND version LIKE ? "
                "ORDER BY datetime(effective_from),rowid LIMIT 1",
                (_SOURCE_PREFIX + "%", f"%{catalog_sha256}%"),
            )
            .fetchone()
        )
    except Exception:
        return None
    return str(row[0]) if row else None


def activate_cached_catalog() -> CatalogObservation | None:
    """Synchronously activate only a hash-, size-, source-verified local cache."""
    try:
        content = _cache_path().read_bytes()
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or len(parsed) < _MIN_MODEL_COUNT:
            return None
        digest = hashlib.sha256(content).hexdigest()
        try:
            metadata = json.loads(_cache_metadata_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            metadata = {}
        if metadata:
            if (
                metadata.get("catalog_sha256") != digest
                or metadata.get("entry_count") != len(parsed)
                or metadata.get("source_url") != LITELLM_PRICE_MAP_URL
                or metadata.get("match_version") != _MATCH_VERSION
            ):
                return None
            observed_at = str(metadata.get("observed_at") or "")
        else:
            observed_at = _legacy_observed_at(digest) or ""
            if observed_at:
                recovered = _observation(content, observed_at)
                _atomic_write(_cache_metadata_path(), _metadata(recovered))
        if not observed_at:
            return None
        # Validate the timestamp before it becomes event-time evidence.
        datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        return _activate(_observation(content, observed_at))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def capture_catalog_observation() -> CatalogObservation | None:
    with _state_lock:
        return _active_catalog


def _validate_price_map(parsed: dict[str, Any], content: bytes) -> None:
    if json.loads(content) != parsed:
        raise ValueError("LiteLLM parsed price map does not match cached bytes")
    if not isinstance(parsed, dict) or len(parsed) < _MIN_MODEL_COUNT:
        raise ValueError("LiteLLM price map is missing or unexpectedly small")
    cached_count = _load_cached_count()
    if cached_count and len(parsed) < cached_count * _MIN_RELATIVE_SIZE:
        raise ValueError("LiteLLM price map shrank unexpectedly")


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
        (
            "cache_creation_input_token_cost",
            "cache_creation_input_usd_per_million",
        ),
        ("output_cost_per_reasoning_token", "reasoning_usd_per_million"),
    ):
        values = [rate for _, _, entry in paired if (rate := _rate(entry.get(source_key))) is not None]
        if values:
            result[target_key] = median(values) * 1_000_000
    search_rates: list[float] = []
    for _, _, entry in paired:
        tiers = entry.get("search_context_cost_per_query")
        if not isinstance(tiers, dict):
            continue
        rate = _rate(tiers.get("search_context_size_medium"))
        if rate is None:
            available = [_rate(value) for value in tiers.values()]
            usable = [value for value in available if value is not None]
            rate = median(usable) if usable else None
        if rate is not None:
            search_rates.append(rate)
    if search_rates:
        result["web_search_usd_per_request"] = median(search_rates)
    return result


def _runtime_models() -> list[tuple[str, str]]:
    db = ensure_economics_schema()
    models = {
        (str(row[0]), str(row[1]))
        for row in db.execute(
            "SELECT DISTINCT provider,model_or_tool FROM runtime_cost_events "
            "WHERE event_type='llm' AND provider IS NOT NULL AND model_or_tool IS NOT NULL"
        ).fetchall()
    }
    try:
        from aletheia.config.loader import load_config
        from aletheia.model_provider.fallback import _parse_fallback_entry

        for provider, config in load_config().models.providers.items():
            model = str(getattr(config, "model", "") or "").strip()
            if model and model != "default":
                models.add((provider, model))
            for fallback in getattr(config, "fallback_models", ()) or ():
                fallback_provider, fallback_model = _parse_fallback_entry(str(fallback), provider)
                if fallback_model:
                    models.add((fallback_provider, fallback_model))
    except Exception:
        logger.warning("pricing_sync: model catalog scan failed (partial)", exc_info=True)
    return sorted(models)


def _rate_vector_version(catalog_sha256: str, rates: dict[str, float | int]) -> str:
    vector = {
        key: rates.get(key)
        for key in (
            "input_usd_per_million",
            "output_usd_per_million",
            "reasoning_usd_per_million",
            "cached_input_usd_per_million",
            "cache_creation_input_usd_per_million",
            "web_search_usd_per_request",
        )
    }
    vector_hash = hashlib.sha256(json.dumps(vector, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"{_MATCH_VERSION}:catalog-sha256:{catalog_sha256}:rates-sha256:{vector_hash}"


def resolve_pricing_from_observation(
    provider: str,
    model: str,
    started_at: str,
    observation: CatalogObservation | None,
    *,
    use_existing: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve event-time pricing, binding an actual model to pre-attempt evidence."""
    from aletheia.economics.tracing import resolve_pricing_snapshot

    if use_existing:
        existing = resolve_pricing_snapshot(provider, model, started_at)
        if existing is not None:
            return existing, "formula_priced"
    if observation is None:
        return None, "pricing_snapshot_missing"
    try:
        observed = datetime.fromisoformat(observation.observed_at.replace("Z", "+00:00"))
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except ValueError:
        return None, "pricing_snapshot_missing"
    if observed > started:
        return None, "catalog_observed_after_event"
    entries = _match_entries(_build_price_index(observation.price_map()), model)
    rates = _representative_rates(entries) if entries else None
    if rates is None:
        request_model_pricing_refresh(provider, model, observation.catalog_sha256)
        return None, "model_unmatched"
    version = _rate_vector_version(observation.catalog_sha256, rates)
    snapshot = add_catalog_pricing_snapshot(
        provider=provider,
        model=model,
        input_usd_per_million=float(rates["input_usd_per_million"]),
        output_usd_per_million=float(rates["output_usd_per_million"]),
        reasoning_usd_per_million=(
            float(rates["reasoning_usd_per_million"]) if "reasoning_usd_per_million" in rates else None
        ),
        cached_input_usd_per_million=(
            float(rates["cached_input_usd_per_million"]) if "cached_input_usd_per_million" in rates else None
        ),
        cache_creation_input_usd_per_million=(
            float(rates["cache_creation_input_usd_per_million"])
            if "cache_creation_input_usd_per_million" in rates
            else None
        ),
        web_search_usd_per_request=(
            float(rates["web_search_usd_per_request"]) if "web_search_usd_per_request" in rates else None
        ),
        effective_from=observation.observed_at,
        source=_SOURCE_PREFIX + observation.source_url,
        version=version,
    )
    return snapshot, "formula_priced"


def request_model_pricing_refresh(provider: str, model: str, catalog_sha256: str) -> bool:
    """Single-flight unmatched refresh with a six-hour per-catalog negative cache."""
    key = (provider, model, catalog_sha256)
    now = time.monotonic()
    with _state_lock:
        if _negative_refresh.get(key, 0) > now:
            return False
        _negative_refresh[key] = now + _REFRESH_INTERVAL_SECONDS
    try:
        start_pricing_refresh(trigger="model_unmatched")
        return True
    except Exception:
        with _state_lock:
            _negative_refresh.pop(key, None)
        return False


def _may_write_catalog_snapshot(provider: str, model: str, version: str) -> bool:
    db = ensure_economics_schema()
    row = db.execute(
        "SELECT version FROM pricing_snapshots WHERE provider=? AND model=? "
        "ORDER BY datetime(effective_from) DESC,rowid DESC LIMIT 1",
        (provider, model),
    ).fetchone()
    return row is None or str(row[0] or "") != version


def refresh_pricing(
    fetcher: Callable[[], tuple[dict[str, Any], bytes]] | None = None,
) -> dict[str, Any]:
    """Fetch public prices and append forward-only estimated snapshots.

    The catalog is independent of the configured runtime provider or API base.
    Exact model/alias matches are preferred; conservative family matching
    handles deployment suffixes. Multiple provider listings are aggregated as
    a public-market median and never labelled provider-billed or known cost.
    """
    fetcher = fetcher if fetcher is not None else _catalog_fetcher
    if fetcher is None:
        raise RuntimeError("pricing_catalog_unavailable")
    price_map, content = fetcher()
    _validate_price_map(price_map, content)
    effective_from = _utcnow()
    observation = _save_cache(content) or _observation(content, effective_from)
    effective_from = observation.observed_at
    catalog_version = _MATCH_VERSION + ":catalog-sha256:" + observation.catalog_sha256
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
        version = _rate_vector_version(observation.catalog_sha256, rates)
        if not _may_write_catalog_snapshot(provider, model, version):
            continue
        add_catalog_pricing_snapshot(
            provider=provider,
            model=model,
            input_usd_per_million=float(rates["input_usd_per_million"]),
            output_usd_per_million=float(rates["output_usd_per_million"]),
            reasoning_usd_per_million=(
                float(rates["reasoning_usd_per_million"]) if "reasoning_usd_per_million" in rates else None
            ),
            cached_input_usd_per_million=(
                float(rates["cached_input_usd_per_million"]) if "cached_input_usd_per_million" in rates else None
            ),
            cache_creation_input_usd_per_million=(
                float(rates["cache_creation_input_usd_per_million"])
                if "cache_creation_input_usd_per_million" in rates
                else None
            ),
            web_search_usd_per_request=(
                float(rates["web_search_usd_per_request"]) if "web_search_usd_per_request" in rates else None
            ),
            effective_from=effective_from,
            source=source,
            version=version,
        )
        created += 1
    return {
        "version": catalog_version,
        "fetched_model_count": len(price_map),
        "matched_model_count": matched,
        "snapshots_created": created,
        "unmatched_models": sorted(unmatched),
        "samples": samples,
    }


def pricing_refresh_status() -> dict[str, Any]:
    with _state_lock:
        _reconcile_worker_state_locked()
        return dict(_state)


def _reconcile_worker_state_locked() -> None:
    if _state.get("status") != "running":
        return
    if _worker is not None and _worker.is_alive():
        return
    _state.update(
        status="failed",
        completed_at=_state.get("completed_at") or _utcnow(),
        error=_state.get("error") or "worker_not_running",
    )


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
        _reconcile_worker_state_locked()
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
        try:
            _worker.start()
        except Exception as exc:
            _worker = None
            _state.update(
                status="failed",
                completed_at=_utcnow(),
                error="worker_start_failed",
            )
            raise PricingRefreshStartError("worker_start_failed") from exc
        return dict(_state)
