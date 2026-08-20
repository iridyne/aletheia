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


def test_available_balance_risk_budget_is_cost_then_futures_notional():
    from aletheia.trading.sizing import available_balance_risk_budget_size

    trace = {}
    assert available_balance_risk_budget_size(
        600.0, 2.0, leverage=3, sizing_trace=trace
    ) == 36.0
    assert available_balance_risk_budget_size(200.0, 2.0, leverage=3) == 12.0
    assert trace == {
        "policy_version": "available-balance-risk-budget/v1",
        "value_source": "risk_policy_available_balance",
        "available_balance_usdt": 600.0,
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
        "account_available_for_execution",
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


def test_available_balance_risk_budget_rejects_invalid_authority_values():
    from aletheia.trading.sizing import available_balance_risk_budget_size

    with pytest.raises(ValueError, match="invalid_available_balance_risk_budget"):
        available_balance_risk_budget_size(0, 2, leverage=3)
    with pytest.raises(ValueError, match="invalid_available_balance_risk_budget"):
        available_balance_risk_budget_size(600, 2, leverage=0)


def test_operator_learning_epoch_carries_sizing_policy_identity(monkeypatch):
    from aletheia.learning import experiments

    policy = SimpleNamespace(loss=SimpleNamespace(max_single_trade_risk_pct=2.0))
    cfg = SimpleNamespace(
        agent=SimpleNamespace(autonomous_learning=False, permission_mode="Paper"),
        universe=SimpleNamespace(scanner_weights={}, scanner_regime_scoring=False),
        learning=SimpleNamespace(
            lifecycle_automation_enabled=False,
            paper_influence_enabled=False,
            paper_influence_paths=[],
            auto_refreeze=False,
            paper_active_allocation_pct=0.0,
        ),
        risk=SimpleNamespace(demo=policy, live=policy),
    )
    monkeypatch.setattr(experiments, "load_config", lambda **_kwargs: cfg)
    monkeypatch.setattr(
        experiments,
        "get_learning_path",
        lambda _path_id: SimpleNamespace(behavior_contract_version="test-v1"),
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.skip_policy.skip_policy_context",
        lambda: {"version": "skip-v1"},
    )
    monkeypatch.setattr(
        "aletheia.learning.opportunity_information_policy.opportunity_information_policy_context",
        lambda: {"version": "information-v1"},
    )

    epoch = experiments._policy_epoch_definition(
        "operator_learning_bundle", persist_config_migrations=False
    )

    assert epoch.effective_values["operator_sizing_policy"] == {
        "policy_version": "available-balance-risk-budget/v1",
        "sizing_basis": "available_balance",
        "per_trade_risk_source": "risk_policy.loss.max_single_trade_risk_pct",
        "futures_notional_source": "cost_budget_times_effective_leverage",
        "demo_per_trade_risk_pct": 2.0,
        "live_per_trade_risk_pct": 2.0,
    }


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
    equity = {"value": 600.0}
    price = {"value": 100.0}
    monkeypatch.setattr(config_loader, "load_config", lambda: cfg)
    monkeypatch.setattr(
        risk_engine,
        "account_available_for_execution",
        lambda execution_mode, cfg: equity["value"],
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
            "price": price["value"],
        },
    )
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: price["value"],
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
    assert proposal.strategy_id == "obsolete-strategy-id"
    assert proposal.quantity == 0.36
    assert decision.position_size_usdt == 36.0
    assert decision.sizing_trace["cost_budget_usdt"] == 12.0

    equity["value"] = 1.0
    price["value"] = 100_000.0
    tiny_decision = decision_module.TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="unrepresentable risk-budget regression",
        market_type="usdt_futures",
        leverage=1,
        stop_price=98_000.0,
        take_profit=104_000.0,
        parameter_rationale=decision.parameter_rationale,
        parameter_rationale_required=True,
    )
    with pytest.raises(
        decision_module.DecisionUnavailable,
        match="unrepresentable_position_size",
    ):
        asyncio.run(
            decision_module.prepare_decision_as_proposal(
                tiny_decision, "paper_internal"
            )
        )


def test_successive_entries_shrink_so_margin_never_exhausts():
    """The reason the base is available balance and not equity.

    Sizing from equity gives every entry the same full budget, so N entries
    pledge N x the per-trade margin and the portfolio over-commits. Sizing from
    available balance makes each entry shrink the next, converging instead.
    """
    from aletheia.trading.sizing import available_balance_risk_budget_size

    equity = 668.52648937
    risk_pct, leverage = 20.0, 3.0

    available = equity
    margin_used = 0.0
    notionals = []
    for _ in range(8):
        notional = available_balance_risk_budget_size(
            available, risk_pct, leverage=leverage
        )
        margin = notional / leverage
        notionals.append(notional)
        margin_used += margin
        available = equity - margin_used

    # Each entry is strictly smaller than the one before it.
    assert all(b < a for a, b in zip(notionals, notionals[1:])), notionals
    # The first entry still matches the equity-based number (nothing pledged yet).
    assert notionals[0] == round(equity * (risk_pct / 100.0) * leverage, 8)
    # Margin converges below equity — the property equity-based sizing lacked,
    # where 5 entries would have pledged 100% of the account.
    assert margin_used < equity
    assert available > 0


def test_zero_available_balance_is_rejected_not_silently_sized():
    """A fully-committed account must fail loudly, never fall back to equity."""
    import pytest

    from aletheia.trading.sizing import available_balance_risk_budget_size

    with pytest.raises(ValueError, match="invalid_available_balance_risk_budget"):
        available_balance_risk_budget_size(0.0, 20.0, leverage=3)
