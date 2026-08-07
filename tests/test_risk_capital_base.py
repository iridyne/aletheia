from aletheia.config.schema import AletheiaConfig
from aletheia.risk import engine


def test_account_equity_is_the_default_effective_capital_base(monkeypatch):
    cfg = AletheiaConfig()
    monkeypatch.setattr(engine, "_account_equity", lambda mode, _: 4321.0)

    value, reason = engine._resolve_capital_base(
        "paper_internal", cfg.risk.demo, cfg,
    )

    assert value == 4321.0
    assert reason is None


def test_custom_capital_base_is_used_only_when_explicitly_selected(monkeypatch):
    cfg = AletheiaConfig()
    cfg.risk.demo.capital.base_source = "custom"
    cfg.risk.demo.capital.base_usdt = 275.0
    monkeypatch.setattr(engine, "_account_equity", lambda mode, _: 4321.0)

    value, reason = engine._resolve_capital_base(
        "paper_internal", cfg.risk.demo, cfg,
    )

    assert value == 275.0
    assert reason is None


def test_account_equity_mode_does_not_fall_back_to_stored_custom_value(monkeypatch):
    cfg = AletheiaConfig()
    cfg.risk.demo.capital.base_usdt = 9999.0
    monkeypatch.setattr(engine, "_account_equity", lambda mode, _: None)

    value, reason = engine._resolve_capital_base(
        "okx_demo", cfg.risk.demo, cfg,
    )

    assert value is None
    assert reason == "account_equity_unavailable:okx_demo"
