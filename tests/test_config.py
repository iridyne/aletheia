"""Tests for config loading, schema defaults, and bootstrap."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from aletheia.config.schema import AletheiaConfig, RiskConfig
from aletheia.config.loader import load_config, save_config, bootstrap, ensure_data_dirs


def test_default_config_has_correct_values():
    cfg = AletheiaConfig()
    assert cfg.app.dashboard_port == 8787
    assert cfg.app.bind_host == "127.0.0.1"
    assert cfg.exchange.mode == "paper"
    assert cfg.live.state == "live_disabled"
    assert cfg.agent.permission_mode == "Paper"
    assert cfg.risk.max_daily_loss_pct == 1.0
    assert cfg.risk.symbol_filter_mode == "blacklist"
    assert cfg.risk.symbol_list == []
    assert cfg.exchange.symbol_list == []
    assert cfg.telegram.enabled is False
    assert cfg.controller.shadow_enabled is False


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
    assert cfg.risk.max_live_position_notional_usdt == 50.0
    assert cfg.risk.max_live_limited_cap_usdt == 100.0
    assert cfg.risk.max_futures_leverage_live == 2
    assert cfg.risk.allowed_exchange == "paper_internal"


def test_legacy_risk_policies_migrate_to_account_equity_capital_base():
    risk = RiskConfig.model_validate({
        "schema_version": 2,
        "demo": {"capital": {"base_usdt": 12_345.0}},
        "live": {"capital": {"base_usdt": 678.0}},
    })

    assert risk.schema_version == 3
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
    assert cfg.models.deep_provider is None
    assert cfg.models.fast_provider is None


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
