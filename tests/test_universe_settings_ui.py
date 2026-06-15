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
