"""Executable claims for the persisted configuration loading contract."""

from unittest.mock import patch

import pytest
import yaml

from pydantic import ValidationError

from aletheia.config.loader import load_config
from aletheia.config.schema import AletheiaConfig
from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "CFG-S01",
        "configuration_contract",
        "strict persisted config",
        "L2b",
        "Unknown root and nested model fields fail closed without rewriting the source.",
    )
)
def test_unknown_persisted_field_is_rejected_without_rewrite(tmp_path):
    config_file = tmp_path / "config.yaml"
    original = "remote_control:\n  require_tokn: true\n"
    config_file.write_text(original, encoding="utf-8")

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        with pytest.raises(ValidationError) as exc_info:
            load_config()

    assert any(
        tuple(item["loc"]) == ("remote_control", "require_tokn")
        for item in exc_info.value.errors(include_input=False)
    )
    assert config_file.read_text(encoding="utf-8") == original


@spec_claim(
    SpecClaim(
        "CFG-S02",
        "configuration_contract",
        "strict persisted config",
        "L2b",
        "Typed dynamic mappings retain arbitrary provider and quota keys.",
    )
)
def test_dynamic_mapping_keys_remain_valid(tmp_path):
    config_file = tmp_path / "config.yaml"
    data = AletheiaConfig().model_dump(mode="json")
    data["models"]["providers"]["operator_selected"] = {
        "type": "openai_compatible",
        "base_url": "https://example.invalid",
    }
    data["universe"]["family_quota"] = {
        "momentum": 0.5,
        "mean_reversion": 0.5,
    }
    config_file.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        loaded = load_config(persist_migrations=False)

    assert loaded.models.providers["operator_selected"].type == "openai_compatible"
    assert loaded.universe.family_quota == {"momentum": 0.5, "mean_reversion": 0.5}


@spec_claim(
    SpecClaim(
        "CFG-S03", "configuration_contract", "canonical equity observation settings", "L2b",
        "Historical equities decode without source rewrite; a canonical empty save persists without a runtime fallback.",
    )
)
def test_historical_equities_clear_persists_through_canonical_writer(tmp_path, monkeypatch):
    from aletheia.config import loader

    config_file = tmp_path / "config.yaml"
    raw = AletheiaConfig().model_dump(mode="json")
    raw["market_data"].update(observed_equities=[], observed_stocks=["TSLA"])
    original = yaml.safe_dump(raw)
    config_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", config_file)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *_args, **_kwargs: None)
    loader._invalidate_config_cache()
    loaded = loader.load_config(persist_migrations=False)
    assert loaded.market_data.observed_equities == []
    assert config_file.read_text(encoding="utf-8") == original
    loader.save_config(loaded)
    reloaded = loader.load_config(persist_migrations=False, use_cache=False)
    assert reloaded.market_data.observed_equities == []
    assert not hasattr(reloaded.market_data, "observed_stocks")
    assert "observed_stocks" not in yaml.safe_load(config_file.read_text())["market_data"]


@spec_claim(
    SpecClaim(
        "CFG-S04", "configuration_contract", "canonical tokenized-equity observation settings", "L2b",
        "Historical tokens decode without source rewrite; canonical API clear persists without a runtime alias.",
    )
)
def test_historical_tokens_clear_persists_through_settings_writer(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.server.routes import settings

    config_file = tmp_path / "config.yaml"
    raw = AletheiaConfig().model_dump(mode="json")
    raw["market_data"].update(observed_stock_tokens=[], observed_xstocks=["AAPLON/USDT"])
    original = yaml.safe_dump(raw)
    config_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", config_file)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *_args, **_kwargs: None)
    loader._invalidate_config_cache()
    loaded = loader.load_config(persist_migrations=False)
    assert loaded.market_data.observed_stock_tokens == ["AAPLON/USDT"]
    assert config_file.read_text(encoding="utf-8") == original
    settings.update_market_data_config(settings.MarketDataConfigUpdate(observed_stock_tokens=[]))
    reloaded = loader.load_config(persist_migrations=False, use_cache=False)
    assert reloaded.market_data.observed_stock_tokens == []
    assert "observed_xstocks" not in yaml.safe_load(config_file.read_text())["market_data"]
