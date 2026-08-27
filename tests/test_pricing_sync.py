import time
import hashlib
import json

import pytest

from aletheia.economics import pricing_sync
from aletheia.economics.pricing_sync import (
    CatalogObservation,
    _build_price_index,
    _match_entries,
    _normalize,
    _representative_rates,
    _rate_vector_version,
    activate_cached_catalog,
    request_model_pricing_refresh,
    resolve_pricing_from_observation,
)
from aletheia.economics.tracing import add_catalog_pricing_snapshot


def _reset_pricing_refresh_state():
    with pricing_sync._state_lock:
        pricing_sync._worker = None
        pricing_sync._active_catalog = None
        pricing_sync._negative_refresh.clear()
        pricing_sync._state.clear()
        pricing_sync._state.update(
            {
                "status": "idle",
                "started_at": None,
                "completed_at": None,
                "last_success_at": None,
                "error": None,
                "source_url": pricing_sync.LITELLM_PRICE_MAP_URL,
                "version": None,
                "fetched_model_count": None,
                "matched_model_count": 0,
                "snapshots_created": 0,
                "unmatched_models": [],
            }
        )


@pytest.fixture(autouse=True)
def _isolate_pricing_refresh_globals():
    _reset_pricing_refresh_state()
    yield
    _reset_pricing_refresh_state()


# ── unit tests for the auto-discovery helpers ────────────────────────────────


def test_normalize_strips_punctuation_and_lowercases():
    assert _normalize("Llama-4-Maverick") == "llama4maverick"
    assert _normalize("qwen3.5-122b-a10b") == "qwen35122ba10b"
    assert _normalize("gpt-4o") == "gpt4o"


def test_build_price_index_keys_by_last_path_component():
    price_map = {
        "groq/meta-llama/llama-4-maverick-17b": {
            "input_cost_per_token": 2e-7,
            "output_cost_per_token": 6e-7,
        },
        "openai/gpt-4o": {
            "input_cost_per_token": 2.5e-6,
            "output_cost_per_token": 1e-5,
            "aliases": ["gpt-4o-current"],
        },
        "free/model": {
            "input_cost_per_token": 0,
            "output_cost_per_token": 0,
        },
        "no-price-model": {"context_window": 4096},  # no cost fields — excluded
    }
    index = _build_price_index(price_map)
    assert "llama4maverick17b" in index
    assert "gpt4o" in index
    assert "gpt4ocurrent" in index
    assert "model" in index  # zero-cost entries are valid catalog prices
    assert "nopricemodel" not in index
    assert len(index["llama4maverick17b"]) == 1


def test_build_price_index_strips_litellm_dotted_catalog_prefix():
    price_map = {
        "global.anthropic.claude-sonnet-4-6": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 1.5e-5,
        },
        "openrouter/anthropic/claude-sonnet-4.6": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 1.5e-5,
        },
    }

    index = _build_price_index(price_map)
    hits = _match_entries(index, "claude-sonnet-4-6")

    assert {key for key, _ in hits} == {
        "global.anthropic.claude-sonnet-4-6",
        "openrouter/anthropic/claude-sonnet-4.6",
    }


def test_match_entries_exact_stem():
    price_map = {
        "groq/llama-4-maverick": {"input_cost_per_token": 2e-7, "output_cost_per_token": 6e-7},
    }
    index = _build_price_index(price_map)
    hits = _match_entries(index, "llama-4-maverick")
    assert len(hits) == 1
    assert hits[0][0] == "groq/llama-4-maverick"


def test_match_entries_prefers_exact_over_numeric_family_suffix():
    price_map = {
        "vendor-a/gpt-5": {
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 10e-6,
        },
        "vendor-b/gpt-5.1": {
            "input_cost_per_token": 5e-6,
            "output_cost_per_token": 50e-6,
        },
    }
    index = _build_price_index(price_map)
    hits = _match_entries(index, "gpt-5")
    rates = _representative_rates(hits)

    assert [key for key, _ in hits] == ["vendor-a/gpt-5"]
    assert rates is not None
    assert rates["input_usd_per_million"] == pytest.approx(1.0)
    assert rates["output_usd_per_million"] == pytest.approx(10.0)
    assert rates["sample_count"] == 1


def test_match_entries_exact_still_medians_across_provider_listings():
    price_map = {
        "vendor-a/gpt-5": {
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 10e-6,
        },
        "vendor-b/gpt-5": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 30e-6,
        },
        "vendor-c/gpt-5.1": {
            "input_cost_per_token": 100e-6,
            "output_cost_per_token": 300e-6,
        },
    }
    rates = _representative_rates(_match_entries(_build_price_index(price_map), "gpt-5"))

    assert rates is not None
    assert rates["input_usd_per_million"] == pytest.approx(2.0)
    assert rates["output_usd_per_million"] == pytest.approx(20.0)
    assert rates["sample_count"] == 2


def test_match_entries_prefix_runtime_shorter():
    """Runtime 'llama4maverick' matches 'llama4maverick17b128e' in the index."""
    price_map = {
        "groq/llama-4-maverick-17b-128e": {"input_cost_per_token": 2e-7, "output_cost_per_token": 6e-7},
    }
    index = _build_price_index(price_map)
    hits = _match_entries(index, "llama-4-maverick")
    assert len(hits) == 1


def test_match_entries_rejects_semantic_variant_suffixes():
    index = _build_price_index(
        {
            "openai/gpt-4o-mini": {
                "input_cost_per_token": 1e-7,
                "output_cost_per_token": 4e-7,
            },
            "libertai/qwen3.5-122b-a10b-thinking": {
                "input_cost_per_token": 2.5e-7,
                "output_cost_per_token": 1.75e-6,
            },
        }
    )
    assert _match_entries(index, "gpt-4o") == []
    assert _match_entries(index, "qwen35-122b-a10b") == []


def test_match_entries_returns_empty_for_unknown_model():
    index = _build_price_index(
        {
            "openai/gpt-4o": {
                "input_cost_per_token": 2.5e-6,
                "output_cost_per_token": 1e-5,
            }
        }
    )
    assert _match_entries(index, "completely-unknown-xyz") == []


def test_representative_rates_returns_none_for_empty_entries():
    assert _representative_rates([]) is None


def test_snapshot_version_changes_when_rate_vector_changes():
    first = _rate_vector_version("same-catalog", {"input_usd_per_million": 1, "output_usd_per_million": 2})
    second = _rate_vector_version(
        "same-catalog",
        {
            "input_usd_per_million": 1,
            "output_usd_per_million": 2,
            "cache_creation_input_usd_per_million": 1.25,
        },
    )

    assert first != second


def _catalog_observation(model: str, observed_at: str) -> CatalogObservation:
    content = json.dumps(
        {
            f"vendor/{model}": {
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        sort_keys=True,
    ).encode()
    return CatalogObservation(
        content=content,
        catalog_sha256=hashlib.sha256(content).hexdigest(),
        observed_at=observed_at,
        entry_count=1,
        source_url=pricing_sync.LITELLM_PRICE_MAP_URL,
        match_version=pricing_sync._MATCH_VERSION,
    )


def test_hidden_actual_model_binds_to_pre_attempt_catalog():
    observation = _catalog_observation("hidden-actual-model", "2026-01-01T00:00:00+00:00")

    snapshot, reason = resolve_pricing_from_observation(
        "provider-a",
        "hidden-actual-model",
        "2026-01-01T00:00:01+00:00",
        observation,
    )

    assert reason == "formula_priced"
    assert snapshot is not None
    assert snapshot["model"] == "hidden-actual-model"
    assert snapshot["effective_from"] == observation.observed_at


def test_catalog_observed_after_event_cannot_bind_history():
    observation = _catalog_observation("late-model", "2026-01-01T00:00:02+00:00")

    snapshot, reason = resolve_pricing_from_observation(
        "provider-a", "late-model", "2026-01-01T00:00:01+00:00", observation
    )

    assert snapshot is None
    assert reason == "catalog_observed_after_event"


def test_catalog_can_add_new_special_rates_to_an_existing_base_snapshot():
    model = "cache-capable-model"
    add_catalog_pricing_snapshot(
        provider="provider-a",
        model=model,
        input_usd_per_million=1,
        output_usd_per_million=2,
        effective_from="2025-12-31T00:00:00+00:00",
        source="litellm_public_median:test",
        version="catalog-model-family-v1:base-only",
    )
    content = json.dumps(
        {
            f"vendor/{model}": {
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
                "cache_read_input_token_cost": 1e-7,
            }
        },
        sort_keys=True,
    ).encode()
    observation = CatalogObservation(
        content=content,
        catalog_sha256=hashlib.sha256(content).hexdigest(),
        observed_at="2026-01-01T00:00:00+00:00",
        entry_count=1,
        source_url=pricing_sync.LITELLM_PRICE_MAP_URL,
        match_version=pricing_sync._MATCH_VERSION,
    )

    existing, _ = resolve_pricing_from_observation("provider-a", model, "2026-01-01T00:00:01+00:00", observation)
    enriched, _ = resolve_pricing_from_observation(
        "provider-a",
        model,
        "2026-01-01T00:00:01+00:00",
        observation,
        use_existing=False,
    )

    assert existing["cached_input_usd_per_million"] is None
    assert enriched["cached_input_usd_per_million"] == pytest.approx(0.1)
    assert enriched["id"] != existing["id"]


def _full_catalog_bytes() -> bytes:
    return json.dumps(
        {
            f"vendor/model-{index}": {
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            }
            for index in range(pricing_sync._MIN_MODEL_COUNT)
        },
        sort_keys=True,
    ).encode()


def test_cached_catalog_activation_verifies_metadata_hash_size_and_source(monkeypatch, tmp_path):
    cache = tmp_path / "model_prices_and_context_window.json"
    monkeypatch.setattr(pricing_sync, "_cache_path", lambda: cache)
    content = _full_catalog_bytes()
    saved = pricing_sync._save_cache(content, "2026-01-01T00:00:00+00:00")
    pricing_sync._active_catalog = None

    activated = activate_cached_catalog()

    assert activated == saved
    cache.write_bytes(content + b" ")
    pricing_sync._active_catalog = None
    assert activate_cached_catalog() is None


def test_legacy_cache_requires_matching_snapshot_hash(monkeypatch, tmp_path):
    cache = tmp_path / "model_prices_and_context_window.json"
    monkeypatch.setattr(pricing_sync, "_cache_path", lambda: cache)
    content = _full_catalog_bytes()
    cache.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    add_catalog_pricing_snapshot(
        provider="provider-a",
        model="model-1",
        input_usd_per_million=1,
        output_usd_per_million=2,
        effective_from="2025-12-31T00:00:00+00:00",
        source="litellm_public_median:test",
        version=f"catalog-model-family-v1:sha256:{digest}",
    )
    pricing_sync._active_catalog = None

    activated = activate_cached_catalog()

    assert activated is not None
    assert activated.observed_at == "2025-12-31T00:00:00+00:00"
    assert pricing_sync._cache_metadata_path().exists()


def test_unmatched_model_refresh_is_negative_cached_per_catalog(monkeypatch):
    _reset_pricing_refresh_state()
    calls = []
    monkeypatch.setattr(
        pricing_sync,
        "start_pricing_refresh",
        lambda *, trigger: calls.append(trigger) or {},
    )

    assert request_model_pricing_refresh("provider-a", "unknown", "catalog-a")
    assert not request_model_pricing_refresh("provider-a", "unknown", "catalog-a")
    assert calls == ["model_unmatched"]


def test_refresh_reports_unmatched_models_correctly(monkeypatch):
    """Models with no LiteLLM match appear in unmatched_models."""
    price_map = {"groq/gpt-4o": {"input_cost_per_token": 2.5e-6, "output_cost_per_token": 1e-5}}
    monkeypatch.setattr(pricing_sync, "_save_cache", lambda content: None)
    monkeypatch.setattr(pricing_sync, "_runtime_models", lambda: [("openai_compatible", "totally-unknown-model")])
    monkeypatch.setattr(pricing_sync, "add_catalog_pricing_snapshot", lambda **kw: None)

    result = pricing_sync.refresh_pricing(fetcher=lambda: (price_map, json.dumps(price_map).encode()))

    assert result["matched_model_count"] == 0
    assert "openai_compatible/totally-unknown-model" in result["unmatched_models"]


# ── integration-level refresh test ───────────────────────────────────────────


def test_refresh_uses_model_median_and_skips_current_catalog_version(monkeypatch):
    price_map = {
        "groq/meta-llama/llama-4-maverick": {
            "input_cost_per_token": 0.0000002,
            "output_cost_per_token": 0.0000006,
            "search_context_cost_per_query": {
                "search_context_size_medium": 0.01,
            },
        },
        "lambda/llama-4-maverick": {
            "input_cost_per_token": 0.00000005,
            "output_cost_per_token": 0.0000001,
        },
        "together/llama-4-maverick": {
            "input_cost_per_token": 0.0000003,
            "output_cost_per_token": 0.0000009,
            "search_context_cost_per_query": {
                "search_context_size_low": 0.02,
                "search_context_size_high": 0.04,
            },
        },
        "global.anthropic.claude-sonnet-4-6": {
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
        },
    }
    written = []
    monkeypatch.setattr(pricing_sync, "_save_cache", lambda content: None)
    monkeypatch.setattr(
        pricing_sync,
        "_runtime_models",
        lambda: [
            ("openai_compatible", "llama-4-maverick"),
            ("existing_catalog_provider", "llama-4-maverick"),
            ("openai_compatible", "claude-sonnet-4-6"),
        ],
    )
    monkeypatch.setattr(
        pricing_sync,
        "_may_write_catalog_snapshot",
        lambda provider, model, version: provider != "existing_catalog_provider",
    )
    monkeypatch.setattr(
        pricing_sync,
        "add_catalog_pricing_snapshot",
        lambda **kwargs: written.append(kwargs),
    )

    result = pricing_sync.refresh_pricing(fetcher=lambda: (price_map, json.dumps(price_map).encode()))

    assert result["matched_model_count"] == 3
    assert result["snapshots_created"] == 2
    assert written[0]["provider"] == "openai_compatible"
    assert written[0]["input_usd_per_million"] == pytest.approx(0.2)
    assert written[0]["output_usd_per_million"] == pytest.approx(0.6)
    assert written[0]["web_search_usd_per_request"] == pytest.approx(0.02)
    assert written[0]["source"].startswith("litellm_public_median:")
    assert ":catalog-sha256:" in written[0]["version"]
    assert ":rates-sha256:" in written[0]["version"]
    claude = next(row for row in written if row["model"] == "claude-sonnet-4-6")
    assert claude["input_usd_per_million"] == pytest.approx(3.0)
    assert claude["output_usd_per_million"] == pytest.approx(15.0)


def test_pricing_refresh_start_failure_publishes_failed_status_and_allows_retry(
    monkeypatch,
):
    _reset_pricing_refresh_state()
    original_thread = pricing_sync.threading.Thread
    refresh_calls = []

    class FailingThread:
        def __init__(self, *args, **kwargs):
            self._target = kwargs.get("target")

        def is_alive(self):
            return False

        def start(self):
            raise RuntimeError("raw thread detail")

    def _refresh_pricing():
        refresh_calls.append(True)
        return {
            "version": "test-version",
            "fetched_model_count": 1,
            "matched_model_count": 0,
            "snapshots_created": 0,
            "unmatched_models": [],
            "samples": {},
        }

    monkeypatch.setattr(pricing_sync.threading, "Thread", FailingThread)
    monkeypatch.setattr(pricing_sync, "refresh_pricing", _refresh_pricing)

    with pytest.raises(pricing_sync.PricingRefreshStartError):
        pricing_sync.start_pricing_refresh(trigger="dashboard")

    status = pricing_sync.pricing_refresh_status()
    assert status["status"] == "failed"
    assert status["error"] == "worker_start_failed"
    assert status["completed_at"] is not None
    assert "raw thread detail" not in status["error"]
    assert refresh_calls == []

    monkeypatch.setattr(pricing_sync.threading, "Thread", original_thread)
    retry = pricing_sync.start_pricing_refresh(trigger="dashboard")
    assert retry["status"] == "running"

    for _ in range(100):
        status = pricing_sync.pricing_refresh_status()
        if status["status"] != "running":
            break
        time.sleep(0.01)
    assert status["status"] == "succeeded"
    assert refresh_calls == [True]


def test_pricing_refresh_status_reconciles_dead_running_worker():
    _reset_pricing_refresh_state()

    class DeadWorker:
        def is_alive(self):
            return False

    with pricing_sync._state_lock:
        pricing_sync._worker = DeadWorker()
        pricing_sync._state.update(
            status="running",
            started_at="2026-08-27T00:00:00+00:00",
            completed_at=None,
            error=None,
        )

    status = pricing_sync.pricing_refresh_status()

    assert status["status"] == "failed"
    assert status["error"] == "worker_not_running"
    assert status["completed_at"] is not None
