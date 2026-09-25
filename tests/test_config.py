"""Tests for config loading, schema defaults, and bootstrap."""

import tempfile
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
import yaml

from aletheia.config.schema import AletheiaConfig, GuardConfig, RiskConfig
from aletheia.config.loader import load_config, save_config, bootstrap, ensure_data_dirs


def test_default_config_has_correct_values():
    cfg = AletheiaConfig()
    assert cfg.app.dashboard_port == 8787
    assert cfg.app.bind_host == "127.0.0.1"
    assert cfg.exchange.mode == "demo"
    assert cfg.live.state == "live_disabled"
    assert cfg.agent.permission_mode == "Paper"
    # Off by default in every mode; 0 disables the check.
    assert cfg.risk.max_daily_loss_pct == 0.0
    assert cfg.risk.symbol_filter_mode == "blacklist"
    assert cfg.risk.symbol_list == []
    assert cfg.exchange.symbol_list == []
    assert cfg.telegram.enabled is False
    assert cfg.controller.shadow_enabled is False
    assert cfg.fast_trigger.mode == "shadow"


@pytest.mark.parametrize("mode", ["blacklst", "off", "", "WHITELIST"])
def test_risk_symbol_filter_mode_rejects_unknown_persisted_values(mode):
    with pytest.raises(ValidationError, match="symbol_filter_mode"):
        AletheiaConfig.model_validate({"risk": {"symbol_filter_mode": mode}})


@pytest.mark.parametrize("value", [0, -1])
def test_guard_min_matching_trades_requires_evidence(value):
    with pytest.raises(ValidationError):
        GuardConfig(min_matching_trades=value)
    with pytest.raises(ValidationError):
        AletheiaConfig.model_validate({"guard": {"min_matching_trades": value}})


def test_default_news_feeds_include_official_sources():
    cfg = AletheiaConfig()
    feeds = {feed.source: feed for feed in cfg.news.feeds}

    for source in ("sec-press", "cftc-press", "ethereum-foundation"):
        assert source in feeds
        assert feeds[source].enabled is True
        assert "official" in feeds[source].categories
        assert feeds[source].url.startswith("https://")

    assert len(feeds) == len(cfg.news.feeds)


def test_config_roundtrip(tmp_path):
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path

    with patch("aletheia.config.loader.DATA_DIR", data_dir), \
         patch("aletheia.config.loader.CONFIG_PATH", config_file):
        cfg = AletheiaConfig()
        cfg.agent.permission_mode = "Paper"
        save_config(cfg)
        loaded = load_config()
        assert loaded.agent.permission_mode == "Paper"
        assert loaded.app.dashboard_port == 8787


def test_config_save_revalidates_mutated_risk_authority(tmp_path):
    config_file = tmp_path / "config.yaml"

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        cfg = AletheiaConfig()
        save_config(cfg)
        valid_content = config_file.read_text(encoding="utf-8")

        cfg.risk.symbol_filter_mode = "off"
        with pytest.raises(ValidationError, match="symbol_filter_mode"):
            save_config(cfg)
        assert config_file.read_text(encoding="utf-8") == valid_content

        cfg.risk.symbol_filter_mode = "whitelist"
        save_config(cfg)
        assert load_config().risk.symbol_filter_mode == "whitelist"


def test_config_save_fsyncs_temp_file_before_directory(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr(loader, "CONFIG_PATH", config_file)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    calls = []
    real_fsync = os.fsync

    def record_fsync(fd):
        calls.append(stat.S_ISREG(os.fstat(fd).st_mode))
        return real_fsync(fd)

    monkeypatch.setattr(loader.os, "fsync", record_fsync)

    loader._save_config_locked(AletheiaConfig())

    assert calls == [True, False]


def test_config_save_replace_failure_leaves_complete_file_and_no_temp(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr(loader, "CONFIG_PATH", config_file)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    original_replace = loader.os.replace

    def replace_then_crash(source, target):
        original_replace(source, target)
        raise RuntimeError("injected crash after replace")

    monkeypatch.setattr(loader.os, "replace", replace_then_crash)

    with pytest.raises(RuntimeError, match="injected crash"):
        loader._save_config_locked(AletheiaConfig())

    assert list(tmp_path.glob("*.tmp")) == []
    AletheiaConfig.model_validate(yaml.safe_load(config_file.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("field", "yaml_value"),
    [
        ("execution_fee_pct", "-1"),
        ("slippage_bps", ".nan"),
    ],
)
def test_restored_invalid_execution_cost_config_fails_closed(
    tmp_path, field, yaml_value,
):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"trading:\n  {field}: {yaml_value}\n",
        encoding="utf-8",
    )

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        with pytest.raises(ValidationError, match=field):
            load_config()


@pytest.mark.parametrize(
    ("yaml_text", "field"),
    [
        ("risk:\n  demo:\n    loss:\n      max_daily_loss_pct: .nan\n", "max_daily_loss_pct"),
        ("risk:\n  max_daily_loss_pct: .inf\n", "max_daily_loss_pct"),
    ],
)
def test_restored_nonfinite_risk_policy_fails_closed_without_rewriting_source(
    tmp_path, yaml_text, field,
):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_text, encoding="utf-8")

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        with pytest.raises(ValidationError, match=field):
            load_config()

    assert config_file.read_text(encoding="utf-8") == yaml_text


@pytest.mark.parametrize(
    "yaml_text",
    [
        "unknown_root: true\n",
        "runtime:\n  pausedd: true\n",
        "runtime:\n  scheduler_enabledd: false\n",
        "remote_control:\n  require_tokn: true\n",
        "agent:\n  permission_mode_typo: Observe\n",
    ],
)
def test_unknown_config_keys_fail_closed_without_rewriting_source(tmp_path, yaml_text):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_text, encoding="utf-8")

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        with pytest.raises(ValidationError) as exc_info:
            load_config()

    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())
    assert config_file.read_text(encoding="utf-8") == yaml_text


def test_unknown_config_error_exposes_safe_path_and_repair_hint(tmp_path):
    secret = "do-not-log-this-token"
    config_file = tmp_path / "config.yaml"
    yaml_text = f"remote_control:\n  require_tokn: {secret}\n"
    config_file.write_text(yaml_text, encoding="utf-8")

    with patch("aletheia.config.loader.DATA_DIR", tmp_path), patch(
        "aletheia.config.loader.CONFIG_PATH", config_file
    ):
        with pytest.raises(ValidationError) as exc_info:
            load_config()

    error = exc_info.value
    assert any(
        tuple(item["loc"]) == ("remote_control", "require_tokn")
        for item in error.errors(include_input=False)
    )
    assert "remote_control.require_tokn" in str(error)
    assert secret not in str(error)
    assert secret not in error.json()
    assert all(item.get("input") is None for item in error.errors())
    assert config_file.read_text(encoding="utf-8") == yaml_text


def test_legacy_coinalyze_key_is_rejected_without_echoing_or_rewriting(tmp_path):
    import aletheia.config.loader as loader

    config_file = tmp_path / "config.yaml"
    raw = AletheiaConfig().model_dump(mode="json")
    raw["market_data"]["coinalyze_api_key"] = "synthetic-not-a-credential"
    config_file.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    original = config_file.read_text(encoding="utf-8")

    with patch.object(loader, "DATA_DIR", tmp_path), patch.object(
        loader, "CONFIG_PATH", config_file
    ), patch.object(loader, "_config_cache", None):
        with pytest.raises(ValueError) as exc_info:
            loader.load_config(use_cache=False)

    message = str(exc_info.value)
    assert "market_data.coinalyze_api_key" in message
    assert "remove this field from config.yaml" in message
    assert "synthetic-not-a-credential" not in message
    assert config_file.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("enabled", "activated_at"),
    [(False, None), (True, "2026-08-31T12:00:00+00:00")],
)
def test_removed_entry_keys_are_migrated_before_strict_validation(
    tmp_path, enabled, activated_at,
):
    import aletheia.config.loader as loader

    config_file = tmp_path / "config.yaml"
    raw = AletheiaConfig().model_dump(mode="json")
    raw["agent"].update({
        "ai_entry_hypothesis_enabled": enabled,
        "ai_entry_hypothesis_activated_at": activated_at,
    })
    raw["trading"]["min_confidence_to_trade"] = "high"
    raw["serenity"]["stop_loss_pct"] = 8.0
    raw["exchange"]["uta_read_boundaries"] = {"bitget_demo": "legacy"}
    raw["runtime"].update(runtime_continuity_enabled=True, runtime_continuity_lock_ttl_sec=300, runtime_continuity_shadow_port_offset=100)
    config_file.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    original = config_file.read_text(encoding="utf-8")

    with patch.object(loader, "DATA_DIR", tmp_path), patch.object(
        loader, "CONFIG_PATH", config_file
    ), patch.object(loader, "_config_cache", None):
        loaded = loader.load_config(persist_migrations=False)

    assert loaded.agent.permission_mode == "Paper"
    assert config_file.read_text(encoding="utf-8") == original


def test_removed_entry_keys_are_persisted_once_when_requested(tmp_path):
    import aletheia.config.loader as loader

    config_file = tmp_path / "config.yaml"
    raw = AletheiaConfig().model_dump(mode="json")
    raw["agent"].update({
        "ai_entry_hypothesis_enabled": True,
        "ai_entry_hypothesis_activated_at": "2026-08-31T12:00:00+00:00",
    })
    raw["trading"]["min_confidence_to_trade"] = "high"
    raw["serenity"]["stop_loss_pct"] = 8.0
    raw["exchange"]["uta_read_boundaries"] = {"bitget_demo": "shadow_read"}
    raw["exchange"]["uta_account_config_generations"] = {"bitget_demo": 7}
    raw["runtime"].update(runtime_continuity_enabled=True, runtime_continuity_lock_ttl_sec=300, runtime_continuity_shadow_port_offset=100)
    config_file.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with patch.object(loader, "DATA_DIR", tmp_path), patch.object(
        loader, "CONFIG_PATH", config_file
    ), patch.object(loader, "_config_cache", None):
        loaded = loader.load_config()

    persisted = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert loaded.agent.permission_mode == "Paper"
    assert "ai_entry_hypothesis_enabled" not in persisted["agent"]
    assert "ai_entry_hypothesis_activated_at" not in persisted["agent"]
    assert "min_confidence_to_trade" not in persisted["trading"]
    assert "stop_loss_pct" not in persisted["serenity"]
    assert "uta_read_boundaries" not in persisted["exchange"]
    assert persisted["exchange"]["uta_account_config_generations"] == {"bitget_demo": 7}
    assert not {"runtime_continuity_enabled", "runtime_continuity_lock_ttl_sec", "runtime_continuity_shadow_port_offset"}.intersection(persisted["runtime"])

def test_strict_config_load_preserves_dynamic_mappings(tmp_path):
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


@pytest.mark.parametrize("legacy_url", ["", "not a url"])
def test_legacy_enabled_webhook_with_invalid_url_is_safely_disabled(
    tmp_path, legacy_url,
):
    import aletheia.config.loader as loader

    config_file = tmp_path / "config.yaml"
    raw = AletheiaConfig().model_dump(mode="json")
    raw["webhook"].update({
        "enabled": True,
        "url": legacy_url,
        "secret": "sensitive-legacy-secret",
        "notify_alerts": False,
    })
    webhook_before = raw["webhook"].copy()
    config_file.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with patch.object(loader, "DATA_DIR", tmp_path), patch.object(
        loader, "CONFIG_PATH", config_file
    ), patch.object(loader, "is_preview_mode", return_value=True), patch(
        "aletheia.files.jsonl.write_audit"
    ) as write_audit:
        loader._invalidate_config_cache()
        loaded = loader.load_config()

    assert loaded.webhook.enabled is False
    persisted = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert persisted["webhook"] == {**webhook_before, "enabled": False}
    audit = write_audit.call_args.args[0]
    assert audit["event_type"] == "webhook_config_migrated"
    assert "sensitive-legacy-secret" not in str(audit)


def test_bootstrap_creates_dirs(tmp_path):
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path

    with patch("aletheia.config.loader.DATA_DIR", data_dir), \
         patch("aletheia.config.loader.CONFIG_PATH", config_file):
        cfg = bootstrap()
        assert config_file.exists()
        assert (data_dir / "logs").is_dir()
        assert (data_dir / "knowledge" / "research_notes").is_dir()
        assert (data_dir / "playbooks").is_dir()
        assert cfg.live.state == "live_disabled"


def test_risk_defaults_conservative():
    cfg = AletheiaConfig()
    assert cfg.risk.max_live_position_margin_usdt == 50.0
    assert cfg.risk.max_live_limited_cap_usdt == 100.0
    assert cfg.risk.max_futures_leverage_live == 2
    assert cfg.risk.allowed_exchange == ""


def test_legacy_risk_policies_migrate_to_account_equity_capital_base():
    risk = RiskConfig.model_validate({
        "schema_version": 2,
        "demo": {"capital": {"base_usdt": 12_345.0}},
        "live": {"capital": {"base_usdt": 678.0}},
    })

    assert risk.schema_version == 4
    assert risk.demo.capital.base_source == "account_equity"
    assert risk.live.capital.base_source == "account_equity"
    # Preserve the old values so switching to Custom remains reversible.
    assert risk.demo.capital.base_usdt == 12_345.0
    assert risk.live.capital.base_usdt == 678.0


def test_explicit_custom_capital_base_survives_roundtrip():
    risk = RiskConfig.model_validate({
        "schema_version": 3,
        "demo": {"capital": {"base_source": "custom", "base_usdt": 250.0}},
    })

    assert risk.demo.capital.base_source == "custom"
    assert risk.demo.capital.base_usdt == 250.0


# ---------------------------------------------------------------------------
# LLM provider defaults: fresh install ships blank (TASK-2026-016)
# ---------------------------------------------------------------------------


def test_fresh_config_has_no_llm_providers():
    """A brand-new AletheiaConfig must not ship with any pre-configured LLM
    providers. Every entry should be an explicit user choice, never a stub
    pointing at localhost that will fail probes on a fresh clone."""
    cfg = AletheiaConfig()
    assert cfg.models.providers == {}, (
        "fresh config must ship with no LLM provider stubs; found: "
        f"{sorted(cfg.models.providers)}"
    )


def test_fresh_config_has_empty_default_provider():
    cfg = AletheiaConfig()
    assert cfg.models.default_provider == "", (
        "fresh config must not name a default provider; got "
        f"{cfg.models.default_provider!r}"
    )
    assert cfg.models.deep is None
    assert cfg.models.fast is None


def test_provider_config_defaults_to_disabled():
    """When adding a bare ProviderConfig the user must opt in — the field
    default must not silently enable a stub."""
    from aletheia.config.schema import ProviderConfig
    assert ProviderConfig(type="openai_compatible").enabled is False


def test_fresh_config_does_not_gate_startup_on_llm():
    """A fresh install has no providers configured, so require_llm_at_startup
    must default off — otherwise the boot gate pauses trading before the
    operator has any UI path to configure a provider."""
    cfg = AletheiaConfig()
    assert cfg.health.require_llm_at_startup is False


def test_bootstrap_seeded_config_stays_blank(tmp_path):
    """The initial runtime-home config.yaml written on first launch reflects the blank
    defaults — no phantom localhost:8000/8001 providers on disk."""
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path
    with patch("aletheia.config.loader.DATA_DIR", data_dir), \
         patch("aletheia.config.loader.CONFIG_PATH", config_file):
        bootstrap()
        seeded = load_config()
        assert seeded.models.providers == {}
        assert seeded.models.default_provider == ""
        assert seeded.health.require_llm_at_startup is False


def test_existing_config_yaml_is_not_overwritten(tmp_path):
    """If the operator already committed provider config, loader must preserve
    it verbatim across the schema-default change."""
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path
    with patch("aletheia.config.loader.DATA_DIR", data_dir), \
         patch("aletheia.config.loader.CONFIG_PATH", config_file):
        # First-run bootstrap creates blank config.
        bootstrap()
        # Simulate the operator adding their own provider entry.
        cfg = load_config()
        from aletheia.config.schema import ProviderConfig
        cfg.models.providers["anthropic_compatible"] = ProviderConfig(
            type="anthropic_compatible",
            base_url="https://example.internal/",
            enabled=True,
            model="databricks-claude-sonnet-4-6",
        )
        cfg.models.default_provider = "anthropic_compatible"
        cfg.health.require_llm_at_startup = True
        save_config(cfg)
        # Re-bootstrap must NOT clobber user's settings.
        bootstrap()
        reloaded = load_config()
        assert "anthropic_compatible" in reloaded.models.providers
        assert reloaded.models.default_provider == "anthropic_compatible"
        assert reloaded.health.require_llm_at_startup is True
        assert reloaded.models.providers["anthropic_compatible"].base_url == "https://example.internal/"
