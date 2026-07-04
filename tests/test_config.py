"""Tests for config loading, schema defaults, and bootstrap."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from aletheia.config.schema import AletheiaConfig
from aletheia.config.loader import load_config, save_config, bootstrap, ensure_data_dirs


def test_default_config_has_correct_values():
    cfg = AletheiaConfig()
    assert cfg.app.dashboard_port == 8787
    assert cfg.exchange.mode == "demo"
    assert cfg.live.state == "live_disabled"
    assert cfg.agent.permission_mode == "Paper"
    assert cfg.risk.max_daily_loss_pct == 1.0
    assert cfg.risk.symbol_filter_mode == "blacklist"
    assert cfg.risk.symbol_list == []
    assert cfg.exchange.symbol_list == []
    assert cfg.telegram.enabled is False


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
    assert cfg.risk.allowed_exchange == "bitget"
