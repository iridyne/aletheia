"""Provider-agnostic runtime usage normalization."""

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
        "provider_cache_read_input_tokens": None,
        "provider_cache_creation_input_tokens": None,
    }


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
    assert usage["cached_input_tokens"] is None


def test_unpriced_provider_cache_usage_is_unknown_not_undercounted():
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
    assert metadata["reason"] == "provider_cache_pricing_contract_unsupported"
