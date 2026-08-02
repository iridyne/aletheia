from aletheia.economics.tracing import observe_token_integrity


def test_complete_consistent_usage_has_no_observations():
    assert observe_token_integrity({
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "reasoning_tokens": 10,
        "provider_cache_read_input_tokens": 50,
        "provider_cache_creation_input_tokens": 5,
    }) == []


def test_missing_usage_is_observed_but_never_estimated():
    assert observe_token_integrity({"input_tokens": None, "output_tokens": 20}) == [
        {"rule_id": "input_or_output_usage_unavailable"}
    ]


def test_inconsistent_provider_counters_are_observed():
    findings = observe_token_integrity({
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 110,
        "reasoning_tokens": 21,
        "provider_cache_read_input_tokens": 101,
        "provider_cache_creation_input_tokens": 102,
    })
    assert {item["rule_id"] for item in findings} == {
        "total_tokens_below_input_plus_output",
        "reasoning_tokens_exceed_output",
        "cache_read_tokens_exceed_input",
        "cache_creation_tokens_exceed_input",
    }
