import pytest


def test_flat_risk_config_migrates_to_mode_policies_without_changing_values():
    from aletheia.config.schema import RiskConfig

    risk = RiskConfig.model_validate({
        "max_daily_loss_pct": 3.5,
        "max_per_symbol_notional_usdt": 750.0,
        "futures_margin_warning_ratio": 0.6,
        "futures_margin_force_close_ratio": 0.8,
        "min_liquidation_buffer_pct": 7.0,
        "correlation_guard_enabled": False,
        "max_sector_concentration_pct": 55.0,
        "max_portfolio_beta": 2.5,
        "max_demo_position_notional_usdt": 900.0,
        "max_live_position_notional_usdt": 45.0,
    })

    assert risk.schema_version == 3
    assert risk.demo.capital.base_source == "account_equity"
    assert risk.live.capital.base_source == "account_equity"
    assert risk.demo.loss.max_daily_loss_pct == 3.5
    assert risk.live.loss.max_daily_loss_pct == 3.5
    assert risk.demo.capital.max_per_symbol_notional_usdt == 750.0
    assert risk.live.capital.max_per_symbol_notional_usdt == 750.0
    assert risk.demo.margin.warning_ratio == 0.6
    assert risk.live.margin.block_ratio == 0.8
    assert risk.demo.correlation.enabled is False
    assert risk.live.correlation.max_portfolio_beta == 2.5
    assert risk.demo.capital.max_position_notional_usdt == 900.0
    assert risk.live.capital.max_position_notional_usdt == 45.0


def test_nested_risk_policy_is_authoritative_over_legacy_projection():
    from aletheia.config.schema import RiskConfig

    risk = RiskConfig.model_validate({
        "max_daily_loss_pct": 1.0,
        "demo": {
            "loss": {
                "max_daily_loss_pct": 25.0,
                "max_single_trade_risk_pct": 30.0,
            },
        },
    })

    assert risk.demo.loss.max_daily_loss_pct == 25.0
    assert risk.live.loss.max_daily_loss_pct == 1.0


@pytest.mark.parametrize(
    ("execution_mode", "expected"),
    [
        ("paper_internal", "demo"),
        ("bitget_demo", "demo"),
        ("binance_testnet", "demo"),
        ("okx_demo", "demo"),
        ("bitget_live_limited", "live"),
        ("binance_live_limited", "live"),
        ("okx_live_limited", "live"),
    ],
)
def test_resolve_risk_policy_uses_explicit_mode_allowlists(execution_mode, expected):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.risk.policy import resolve_risk_policy

    cfg = AletheiaConfig()
    resolved = resolve_risk_policy(cfg, execution_mode)

    assert resolved.mode == expected
    assert resolved.policy is getattr(cfg.risk, expected)


def test_resolve_risk_policy_rejects_unknown_mode():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.risk.policy import UnknownRiskExecutionMode

    with pytest.raises(UnknownRiskExecutionMode):
        from aletheia.risk.policy import resolve_risk_policy

        resolve_risk_policy(AletheiaConfig(), "future_unregistered_mode")


def test_nested_risk_policy_rejects_unknown_fields():
    from aletheia.config.schema import RiskConfig

    with pytest.raises(Exception, match="extra_forbidden"):
        RiskConfig.model_validate({
            "demo": {"capital": {"max_position_notional_usdt_typo": 999}},
            "live": {},
        })
