import pytest

from aletheia.economics import pricing_sync
from aletheia.economics.pricing_sync import _build_price_index, _match_entries, _normalize, _representative_rates


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
        },
        "no-price-model": {"context_window": 4096},  # no cost fields — excluded
    }
    index = _build_price_index(price_map)
    assert "llama4maverick17b" in index
    assert "gpt4o" in index
    assert "nopricemodelcomponent" not in index  # excluded
    assert len(index["llama4maverick17b"]) == 1


def test_match_entries_exact_stem():
    price_map = {
        "groq/llama-4-maverick": {"input_cost_per_token": 2e-7, "output_cost_per_token": 6e-7},
    }
    index = _build_price_index(price_map)
    hits = _match_entries(index, "llama-4-maverick")
    assert len(hits) == 1
    assert hits[0][0] == "groq/llama-4-maverick"


def test_match_entries_prefix_runtime_shorter():
    """Runtime 'llama4maverick' matches 'llama4maverick17b128e' in the index."""
    price_map = {
        "groq/llama-4-maverick-17b-128e": {"input_cost_per_token": 2e-7, "output_cost_per_token": 6e-7},
    }
    index = _build_price_index(price_map)
    hits = _match_entries(index, "llama-4-maverick")
    assert len(hits) == 1


def test_match_entries_returns_empty_for_unknown_model():
    index = _build_price_index({"openai/gpt-4o": {"input_cost_per_token": 2.5e-6, "output_cost_per_token": 1e-5}})
    assert _match_entries(index, "completely-unknown-xyz") == []


def test_representative_rates_returns_none_for_empty_entries():
    assert _representative_rates([]) is None


def test_refresh_reports_unmatched_models_correctly(monkeypatch):
    """Models with no LiteLLM match appear in unmatched_models."""
    price_map = {"groq/gpt-4o": {"input_cost_per_token": 2.5e-6, "output_cost_per_token": 1e-5}}
    monkeypatch.setattr(pricing_sync, "_save_cache", lambda content: None)
    monkeypatch.setattr(pricing_sync, "_runtime_models", lambda: [("openai_compatible", "totally-unknown-model")])
    monkeypatch.setattr(pricing_sync, "add_pricing_snapshot", lambda **kw: None)

    result = pricing_sync.refresh_pricing(fetcher=lambda: (price_map, b"{}"))

    assert result["matched_model_count"] == 0
    assert "openai_compatible/totally-unknown-model" in result["unmatched_models"]


# ── integration-level refresh test ───────────────────────────────────────────

def test_refresh_uses_model_median_and_preserves_manual_price(monkeypatch):
    price_map = {
        "groq/meta-llama/llama-4-maverick": {
            "input_cost_per_token": 0.0000002,
            "output_cost_per_token": 0.0000006,
        },
        "lambda/llama-4-maverick": {
            "input_cost_per_token": 0.00000005,
            "output_cost_per_token": 0.0000001,
        },
        "together/llama-4-maverick": {
            "input_cost_per_token": 0.0000003,
            "output_cost_per_token": 0.0000009,
        },
    }
    written = []
    monkeypatch.setattr(pricing_sync, "_save_cache", lambda content: None)
    monkeypatch.setattr(
        pricing_sync,
        "_runtime_models",
        lambda: [
            ("openai_compatible", "llama-4-maverick"),
            ("manual_provider", "llama-4-maverick"),
        ],
    )
    monkeypatch.setattr(
        pricing_sync,
        "_may_write_automatic_snapshot",
        lambda provider, model, version: provider != "manual_provider",
    )
    monkeypatch.setattr(
        pricing_sync,
        "add_pricing_snapshot",
        lambda **kwargs: written.append(kwargs),
    )

    result = pricing_sync.refresh_pricing(
        fetcher=lambda: (price_map, b'{"version":"test"}')
    )

    assert result["matched_model_count"] == 2
    assert result["snapshots_created"] == 1
    assert written[0]["provider"] == "openai_compatible"
    assert written[0]["input_usd_per_million"] == pytest.approx(0.2)
    assert written[0]["output_usd_per_million"] == pytest.approx(0.6)
    assert written[0]["source"].startswith("litellm_public_median:")
