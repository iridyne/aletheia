"""Provider-agnostic runtime usage normalization."""

import pytest

from aletheia.economics.tracing import estimate_llm_cost, normalize_usage


def test_top_level_reasoning_tokens_are_preserved():
    usage = normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 40,
            "reasoning_tokens": 25,
        }
    )

    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 40
    assert usage["reasoning_tokens"] == 25


def test_nested_reasoning_tokens_remain_supported():
    usage = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
    )

    assert usage["reasoning_tokens"] == 20


def test_openai_usage_details_preserve_cached_tokens():
    usage = normalize_usage(
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 80},
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    )

    assert usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "reasoning_tokens": 12,
        "cached_input_tokens": 80,
        "provider_cache_read_input_tokens": 80,
        "provider_cache_creation_input_tokens": None,
        "web_search_requests": None,
    }


def test_web_search_usage_is_normalized_and_priced_from_catalog():
    usage = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"web_search_requests": 3},
        }
    )

    cost, status, metadata = estimate_llm_cost(
        usage,
        {
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 2.0,
            "web_search_usd_per_request": 0.01,
        },
    )

    assert usage["web_search_requests"] == 3
    assert cost == 0.03014
    assert status == "estimated"
    assert metadata["web_search_cost_usd"] == 0.03


def test_web_search_usage_without_catalog_rate_is_unknown():
    usage = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "server_tool_use": {"web_search_requests": 1},
        }
    )

    cost, status, metadata = estimate_llm_cost(
        usage,
        {
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 2.0,
        },
    )

    assert cost is None
    assert status == "unknown"
    assert metadata == {"reason": "positive_special_rate_missing", "field": "web_search"}


def test_anthropic_usage_preserves_provider_cache_fields():
    usage = normalize_usage(
        {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_input_tokens": 70,
            "cache_creation_input_tokens": 10,
        }
    )

    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == 30
    assert usage["provider_cache_read_input_tokens"] == 70
    assert usage["provider_cache_creation_input_tokens"] == 10
    # PR #611: cache_read_input_tokens now falls back to cached_input_tokens
    # for DeepSeek-style providers that put cache hits at the top level.
    assert usage["cached_input_tokens"] == 70


def test_zero_special_usage_does_not_require_special_rates():
    usage = normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "reasoning_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    )

    cost, status, metadata = estimate_llm_cost(
        usage,
        {"input_usd_per_million": 1.0, "output_usd_per_million": 2.0},
    )

    assert cost == pytest.approx(0.00014)
    assert status == "estimated"
    assert metadata["reason"] == "formula_priced"


def test_positive_reasoning_usage_requires_its_special_rate():
    usage = normalize_usage({"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 5})

    cost, status, metadata = estimate_llm_cost(
        usage,
        {"input_usd_per_million": 1.0, "output_usd_per_million": 2.0},
    )

    assert cost is None and status == "unknown"
    assert metadata == {"reason": "positive_special_rate_missing", "field": "reasoning"}


def test_invalid_provider_cache_usage_is_unknown_not_undercounted():
    usage = normalize_usage(
        {
            "input_tokens": 100,
            "output_tokens": 40,
            "cache_read_input_tokens": 500,
        }
    )

    cost, status, metadata = estimate_llm_cost(
        usage,
        {
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 2.0,
        },
    )

    assert cost is None
    assert status == "unknown"
    assert metadata["reason"] == "cache_tokens_exceed_input_tokens"
