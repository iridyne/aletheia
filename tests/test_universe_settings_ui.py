from pathlib import Path


def test_universe_board_has_direct_settings_link():
    """Disabled Universe Board must point to the exact setting that enables it."""
    page = Path("frontend/src/pages/Universe.tsx").read_text(encoding="utf-8")
    assert "/settings?tab=risk&section=universe" in page
    assert "Open Universe setting" in page
    assert "Universe generator is disabled" in page


def test_settings_exposes_universe_control_in_symbols_tab():
    """Universe is a symbol-selection control, so it must be discoverable there."""
    settings = Path("frontend/src/pages/Settings.tsx").read_text(encoding="utf-8")
    assert "Risk, Symbols & Universe" in settings
    assert 'id="universe"' in settings
    assert "settings-section-${section}" in settings
    assert settings.count("<UniverseConfigCard settings={settings} qc={qc} />") == 1

    risk_tab = settings.index("function RiskTab")
    universe_card = settings.index("<UniverseConfigCard settings={settings} qc={qc} />")
    assert universe_card > risk_tab


def test_settings_has_config_coverage_matrix_for_hidden_keys():
    """Config keys without a dedicated control must still be discoverable."""
    settings = Path("frontend/src/pages/Settings.tsx").read_text(encoding="utf-8")
    assert "Configuration Matrix" in settings
    assert "flattenConfig(settings)" in settings
    assert "read-only / config.yaml" in settings
    assert "operator" in settings
    assert "guard" in settings
    assert "market_data" in settings


def test_settings_controls_match_backend_config_fields():
    settings = Path("frontend/src/pages/Settings.tsx").read_text(encoding="utf-8")
    client = Path("frontend/src/api/client.ts").read_text(encoding="utf-8")

    expected_fields = [
        "paper_execution_mode",
        "slippage_bps",
        "execution_fee_pct",
        "auto_adopt_enabled",
        "self_heal_enabled",
        "self_heal_min_interval_sec",
        "self_heal_max_per_hour",
        "self_heal_code_drift",
        "self_heal_market_data",
        "self_heal_port_cleanup",
        "min_depth_10bps_usd",
        "fallback_symbols",
    ]
    for field in expected_fields:
        assert field in settings
        assert field in client

    assert '<option value="bitget_demo">bitget_demo</option>' in settings
    assert '<option value="paper_internal">paper_internal</option>' in settings
