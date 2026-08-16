from types import SimpleNamespace

import pytest


def _cfg(per_trade_risk_pct: float):
    policy = SimpleNamespace(
        loss=SimpleNamespace(max_single_trade_risk_pct=per_trade_risk_pct),
    )
    return SimpleNamespace(
        risk=SimpleNamespace(demo=policy, live=policy),
        trading=SimpleNamespace(default_notional_usdt=25.0),
    )


def test_account_equity_risk_budget_is_cost_then_futures_notional():
    from aletheia.trading.sizing import account_equity_risk_budget_size

    trace = {}
    assert account_equity_risk_budget_size(
        600.0, 2.0, leverage=3, sizing_trace=trace
    ) == 36.0
    assert account_equity_risk_budget_size(200.0, 2.0, leverage=3) == 12.0
    assert trace == {
        "policy_version": "account-equity-risk-budget/v1",
        "value_source": "risk_policy_account_equity",
        "account_equity_usdt": 600.0,
        "per_trade_risk_pct": 2.0,
        "cost_budget_usdt": 12.0,
        "leverage": 3.0,
        "target_notional_usdt": 36.0,
    }


def test_active_operator_sizing_does_not_use_strategy_sample_scaling(monkeypatch):
    import aletheia.risk.engine as risk_engine
    import aletheia.risk.policy as risk_policy
    import aletheia.agent_shell.trading_decision as decision_module

    cfg = _cfg(2.0)
    monkeypatch.setattr(
        risk_engine,
        "account_equity_for_execution",
        lambda execution_mode, cfg: 600.0,
    )
    monkeypatch.setattr(
        risk_policy,
        "resolve_risk_policy",
        lambda execution_mode_cfg, execution_mode: SimpleNamespace(
            policy=cfg.risk.demo,
        ),
    )
    monkeypatch.setattr(
        decision_module,
        "_get_strategy_paper_trade_count",
        lambda _strategy_id: pytest.fail("strategy sample count must not be read"),
    )

    trace = {}
    result = decision_module._compute_position_size(
        55,
        symbol="BTC/USDT",
        direction="long",
        paper_trade_count=0,
        sizing_trace=trace,
        execution_mode="paper_internal",
        market_type="usdt_futures",
        leverage=3,
        cfg=cfg,
    )

    assert result == 36.0
    assert trace["cost_budget_usdt"] == 12.0
    assert trace["target_notional_usdt"] == 36.0


def test_account_equity_risk_budget_rejects_invalid_authority_values():
    from aletheia.trading.sizing import account_equity_risk_budget_size

    with pytest.raises(ValueError, match="invalid_account_equity_risk_budget"):
        account_equity_risk_budget_size(0, 2, leverage=3)
    with pytest.raises(ValueError, match="invalid_account_equity_risk_budget"):
        account_equity_risk_budget_size(600, 2, leverage=0)


def test_prepared_operator_proposal_recomputes_notional_from_final_leverage(
    monkeypatch,
):
    import asyncio

    import aletheia.agent_shell.trading_decision as decision_module
    import aletheia.config.loader as config_loader
    import aletheia.risk.engine as risk_engine
    import aletheia.risk.policy as risk_policy

    cfg = _cfg(2.0)
    for mode_policy in (cfg.risk.demo, cfg.risk.live):
        mode_policy.leverage = SimpleNamespace(
            futures_cap=20,
            major_cap=3,
            mid_cap=20,
            alt_cap=20,
        )
    cfg.market_data = SimpleNamespace(ccxt_exchange="binance")
    cfg.serenity = SimpleNamespace(stop_loss_pct=4.0)
    cfg.trading.default_stop_loss_pct = 2.0
    cfg.trading.default_take_profit_pct = 4.0
    monkeypatch.setattr(config_loader, "load_config", lambda: cfg)
    monkeypatch.setattr(
        risk_engine,
        "account_equity_for_execution",
        lambda execution_mode, cfg: 600.0,
    )
    monkeypatch.setattr(
        risk_policy,
        "resolve_risk_policy",
        lambda execution_mode_cfg, execution_mode: SimpleNamespace(
            policy=cfg.risk.demo,
        ),
    )
    monkeypatch.setattr(
        decision_module,
        "_proposal_direction_overheated",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        decision_module,
        "_latest_exact_entry_snapshot",
        lambda *_args: {
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "exchange": "binance",
            "price": 100.0,
        },
    )
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.0,
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda _ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda _items: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_snapshot.capture_attribution_snapshot",
        lambda *_args, **_kwargs: {
            "market_context": {
                "instrument": {
                    "symbol": "BTC/USDT",
                    "exchange": "binance",
                    "market_type": "perp",
                },
                "tradability": {"exchange": "binance", "is_tradable": True},
            },
        },
    )

    decision = decision_module.TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="risk-budget regression",
        strategy_id="obsolete-strategy-id",
        market_type="usdt_futures",
        leverage=50,
        stop_price=98.0,
        take_profit=104.0,
        parameter_rationale={
            "entry_price": "use the fresh mark",
            "stop_loss": "keep the authored stop",
            "take_profit": "keep the authored target",
            "notional_usdt": "use the Risk budget",
            "leverage": "use three times leverage",
        },
        parameter_rationale_required=True,
    )

    prepared = asyncio.run(
        decision_module.prepare_decision_as_proposal(decision, "paper_internal")
    )

    proposal = prepared["proposal"]
    assert proposal.leverage == 3
    assert proposal.strategy_id is None
    assert proposal.quantity == 0.36
    assert decision.position_size_usdt == 36.0
    assert decision.sizing_trace["cost_budget_usdt"] == 12.0
