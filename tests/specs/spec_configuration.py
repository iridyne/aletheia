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
