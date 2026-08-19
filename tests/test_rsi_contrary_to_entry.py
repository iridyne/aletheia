"""RSI contrary-to-entry uses Trend agent 30/70 bands; mean-reversion is exempt."""

from aletheia.agent_shell.decision_facts import _rsi_contrary_refs, rsi_contrary_to_entry_refs


def test_short_oversold_rsi_15m_is_contrary_for_short_bias():
    refs = _rsi_contrary_refs("short", {"rsi_15m": 22.0, "rsi_14": 41.0}, "short_bias")
    assert refs == ["technical.rsi_15m"]


def test_mean_reversion_oversold_long_is_exempt():
    assert _rsi_contrary_refs("long", {"rsi_14": 22.0}, "mean_reversion") is None


def test_overbought_long_momentum_is_contrary():
    assert _rsi_contrary_refs("long", {"rsi_14": 81.0}, "momentum") == ["technical.rsi_14"]


def test_missing_rsi_fail_open():
    assert _rsi_contrary_refs("short", {}, "short_bias") is None


def test_rsi_contrary_reads_selected_candidate_facts():
    class _Decision:
        opportunity_id = "opp-near"
        direction = "short"
        scanner_family = "short_bias"

    ctx = {
        "layer2": {
            "pending_signals": [{
                "opportunity_id": "opp-near",
                "symbol": "NEAR/USDT",
                "direction": "short",
                "market_type": "usdt_futures",
                "scanner_family": "short_bias",
            }]
        },
        "layer1": {
            "features": [{
                "feature_snapshot_id": "fs-1",
                "feature_ts": "2026-08-19T00:00:00+00:00",
                "symbol": "NEAR/USDT",
                "market_type": "perp",
                "rsi_15m": 24.0,
                "rsi_14": 40.0,
            }]
        },
    }
    assert rsi_contrary_to_entry_refs(_Decision(), ctx) == ["technical.rsi_15m"]


def test_evaluate_trading_opportunity_defers_oversold_short(tmp_path, monkeypatch):
    import asyncio

    import pytest

    import aletheia.agent_shell.trading_decision as td
    import aletheia.config.loader as loader
    from aletheia.agent_shell.trading_decision import DecisionUnavailable

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    payload = {
        "action": "trade",
        "symbol": "NEAR/USDT",
        "direction": "short",
        "market_type": "usdt_futures",
        "conviction": 90,
        "reasoning": "committee short with falling 4h",
        "bear_case": "15m RSI is oversold",
        "stop_price": 1.62,
        "take_profit": 1.50,
        "parameter_rationale": {
            "entry_price": "mark",
            "stop_loss": "structure",
            "take_profit": "target",
            "notional_usdt": "bounded",
            "leverage": "2",
        },
    }

    async def assess(_ctx):
        return payload

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_proposal_bias_brake_reason", lambda _decision: None)

    ctx = {
        "layer2": {
            "pending_signals": [{
                "opportunity_id": "opp-near",
                "symbol": "NEAR/USDT",
                "direction": "short",
                "market_type": "usdt_futures",
                "scanner_family": "short_bias",
                "venue": "bitget",
            }],
            "entry_mark": {
                "status": "available",
                "symbol": "NEAR/USDT",
                "market_type": "usdt_futures",
                "venue": "bitget",
                "price": 1.595,
                "authority": "fresh_exact_market_mark",
            },
        },
        "layer1": {
            "features": [{
                "feature_snapshot_id": "fs-near",
                "feature_ts": "2026-08-19T00:00:00+00:00",
                "symbol": "NEAR/USDT",
                "market_type": "perp",
                "rsi_15m": 24.0,
            }]
        },
    }
    with pytest.raises(DecisionUnavailable, match="rsi_contrary_to_entry") as exc:
        asyncio.run(td.evaluate_trading_opportunity(ctx))
    assert exc.value.stage == "operator_contrary_signal_gate"
