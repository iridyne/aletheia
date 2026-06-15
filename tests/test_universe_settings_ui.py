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
    assert "Risk & Universe" in settings
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


def test_settings_uses_vertical_information_architecture_and_switches():
    settings = Path("frontend/src/pages/Settings.tsx").read_text(encoding="utf-8")
    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    ui_index = Path("frontend/src/components/ui/index.ts").read_text(encoding="utf-8")

    for label in [
        "Agent & Models",
        "Execution",
        "Risk & Universe",
        "Health & Alerts",
        "Runtime & System",
        "Advanced",
    ]:
        assert label in settings

    assert "settings-shell" in settings
    assert "settings-nav-item" in settings
    assert "SettingsSection" in ui_index
    assert "Switch" in ui_index
    assert "NumberFieldGrid" in ui_index
    assert "SwitchGrid" in ui_index
    assert "settings-toggle-grid" in Path("frontend/src/components/ui/SettingsSection.tsx").read_text(encoding="utf-8")
    assert "type=\"checkbox\"" not in settings
    assert "type=\"checkbox\"" in Path("frontend/src/components/ui/Switch.tsx").read_text(encoding="utf-8")
    assert css.count(".settings-switch-input") >= 1
    assert "settings-switch-input:checked + .settings-switch-track" in css


def test_global_ui_removes_draggy_button_and_number_spinner_patterns():
    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    command_center = Path("frontend/src/pages/CommandCenter.tsx").read_text(encoding="utf-8")

    assert "button:active { transform: none; }" in css
    assert 'input[type="number"]::-webkit-inner-spin-button' in css
    assert 'input[type="number"] { appearance: textfield;' in css
    assert "cc-severity" in command_center
    assert "cc-list-item:active { transform: none; }" in css


def test_p3_pages_use_shared_primitives_instead_of_ad_hoc_capsules_and_buttons():
    """The page-by-page pass should not regress to draggy button rows or heavy badges."""
    pages = [
        "AgentActivity.tsx",
        "Charts.tsx",
        "CommandCenter.tsx",
        "Learning.tsx",
        "NewsIntel.tsx",
        "Overview.tsx",
        "PredictionLens.tsx",
        "Proposals.tsx",
        "RiskControls.tsx",
        "SerenityLens.tsx",
        "StrategyLifecycle.tsx",
        "TradesPositions.tsx",
        "Universe.tsx",
    ]
    for name in pages:
        page = Path("frontend/src/pages") / name
        text = page.read_text(encoding="utf-8")
        assert "../components/ui" in text, name
        assert "<button" not in text, name
        assert 'className="badge' not in text, name
        assert 'className="card"' not in text, name
        assert 'className="card ' not in text, name
        assert "style={{" not in text, name

    command_center = Path("frontend/src/pages/CommandCenter.tsx").read_text(encoding="utf-8")
    assert "ListRowButton" in command_center
    assert "cc-severity" in command_center


def test_local_ui_primitive_barrel_exports_page_layout_components():
    ui_index = Path("frontend/src/components/ui/index.ts").read_text(encoding="utf-8")
    for symbol in [
        "Button",
        "Card",
        "CardHeader",
        "TableWrap",
        "Badge",
        "Switch",
        "SettingsSection",
        "StatusDot",
        "StatusText",
        "ListRowButton",
    ]:
        assert symbol in ui_index
