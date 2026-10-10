"""Regression tests for execution-vs-market-data exchange alignment."""

from aletheia.config.schema import AletheiaConfig


def test_exchange_alignment_uses_actual_execution_mode_not_preference_name():
    from aletheia.config.exchange_alignment import exchange_alignment

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.market_data.ccxt_exchange = "binance"

    result = exchange_alignment(cfg)
    assert result.execution_exchange == "okx"
    assert result.data_exchange == "binance"
    assert result.status == "diverged"
    assert result.diverged is True


def test_exchange_alignment_unconfigured_target_never_fabricates_a_venue():
    from aletheia.config.exchange_alignment import exchange_alignment

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = ""
    cfg.market_data.ccxt_exchange = "okx"

    result = exchange_alignment(cfg)
    assert result.execution_exchange is None
    assert result.status == "not_configured"
    assert result.diverged is False


def test_config_diagnostic_names_actual_execution_exchange():
    from aletheia.diagnostics.config_consistency import _check_exchange_consistency

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.market_data.ccxt_exchange = "binance"

    finding = _check_exchange_consistency(cfg)
    assert finding is not None
    assert "execution (okx)" in finding.message
    assert "market data (binance)" in finding.message
    assert "bitget" not in finding.message


def test_paper_execution_mode_vocabulary_excludes_uta_credential_profiles():
    """`binance_live` / `okx_live` are UTA credential profile names, not execution modes.

    They were accepted by POST /settings/exchange-markets and listed in the alignment
    map, and the mutator persists paper_execution_mode verbatim. Neither is in any Risk
    vocabulary, so resolve_risk_policy raises UnknownRiskExecutionMode and every
    subsequent proposal is rejected `unregistered_risk_execution_mode:...` — a total
    trading halt reachable from one dashboard POST. Both surfaces now derive from the
    authority instead of restating it, so they cannot drift apart again.
    """
    from aletheia.config.exchanges import execution_mode_venue
    from aletheia.risk.policy import (
        DEMO_EXECUTION_MODES,
        LIVE_EXECUTION_MODES,
        UnknownRiskExecutionMode,
        resolve_risk_policy,
    )

    profiles = {"binance_live", "okx_live"}
    assert all(execution_mode_venue(profile) is None for profile in profiles)
    assert not profiles & DEMO_EXECUTION_MODES
    assert not profiles & LIVE_EXECUTION_MODES

    # The reason they must never be accepted: Risk cannot resolve a policy for them.
    cfg = AletheiaConfig()
    for profile in sorted(profiles):
        try:
            resolve_risk_policy(cfg, profile)
        except UnknownRiskExecutionMode:
            continue
        raise AssertionError(f"{profile} unexpectedly resolves to a risk policy")


def test_exchange_markets_route_rejects_uta_credential_profiles():
    """Behaviour, not source text: the route must 400 before the mutator persists.

    A source-text scan is what made AC-S13 rot; assert the rejection itself.
    """
    import pytest
    from fastapi import HTTPException

    from aletheia.risk.policy import DEMO_EXECUTION_MODES
    from aletheia.server.routes.settings import (
        ExchangeMarketsRequest,
        set_exchange_markets,
    )

    for profile in ("binance_live", "okx_live", "paper_internal"):
        with pytest.raises(HTTPException) as excinfo:
            set_exchange_markets(
                ExchangeMarketsRequest(
                    spot=True, usdt_futures=True, paper_execution_mode=profile,
                )
            )
        assert excinfo.value.status_code == 400
        assert "paper_execution_mode must be one of" in str(excinfo.value.detail)

    assert DEMO_EXECUTION_MODES == {"bitget_demo", "binance_testnet", "okx_demo"}


def test_market_data_follows_the_trading_account():
    """One switch: with no explicit override, market data moves with the account."""
    from aletheia.config.exchange_alignment import (
        apply_primary_exchange,
        exchange_alignment,
        market_data_exchange,
    )

    cfg = AletheiaConfig()
    assert cfg.market_data.ccxt_exchange == ""
    assert market_data_exchange(cfg) == "binance"  # no account configured

    cfg.exchange.paper_execution_mode = "bitget_demo"
    assert market_data_exchange(cfg) == "bitget"
    cfg.exchange.paper_execution_mode = "okx_demo"
    assert market_data_exchange(cfg) == "okx"
    assert exchange_alignment(cfg).status == "aligned"

    cfg.market_data.ccxt_exchange = "binance"  # an explicit split stays visible
    assert market_data_exchange(cfg) == "binance"
    assert exchange_alignment(cfg).status == "diverged"

    apply_primary_exchange(cfg, "bitget")  # the unified switch clears the override
    assert cfg.market_data.ccxt_exchange == ""
    assert (cfg.exchange.paper_execution_mode, market_data_exchange(cfg)) == ("bitget_demo", "bitget")
