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
    # After removing sizing_trace.clear(), helper appends its keys instead of replacing.
    # Check subset: the helper's keys must be present and correct; caller context preserved.
    assert trace["policy_version"] == "available-balance-risk-budget/v1"
    assert trace["value_source"] == "risk_policy_available_balance"
    assert trace["available_balance_usdt"] == 600.0
    assert trace["per_trade_risk_pct"] == 2.0
    assert trace["cost_budget_usdt"] == 12.0
    assert trace["leverage"] == 3.0
    assert trace["target_notional_usdt"] == 36.0




def test_available_balance_risk_budget_rejects_invalid_authority_values():
    from aletheia.trading.sizing import available_balance_risk_budget_size

    with pytest.raises(ValueError, match="invalid_available_balance_risk_budget"):
        available_balance_risk_budget_size(0, 2, leverage=3)
    with pytest.raises(ValueError, match="invalid_available_balance_risk_budget"):
        available_balance_risk_budget_size(600, 2, leverage=0)


def test_operator_learning_epoch_carries_sizing_policy_identity(monkeypatch):
    from extensions.learning import experiments

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
        "extensions.learning.skip_policy.skip_policy_context",
        lambda: {"version": "skip-v1"},
    )
    monkeypatch.setattr(
        "extensions.learning.opportunity_information_policy.opportunity_information_policy_context",
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
