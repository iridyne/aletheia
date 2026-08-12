import pytest

from aletheia.economics import pricing_sync


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
