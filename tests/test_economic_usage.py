"""Provider-agnostic runtime usage normalization."""

from aletheia.economics.tracing import normalize_usage


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
