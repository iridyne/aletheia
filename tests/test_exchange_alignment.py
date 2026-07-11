"""Regression tests for execution-vs-market-data exchange alignment."""

from aletheia.config.schema import AletheiaConfig


def test_exchange_alignment_uses_actual_execution_mode_not_preference_name():
    from aletheia.config.exchange_alignment import exchange_alignment

    cfg = AletheiaConfig()
    cfg.exchange.name = "bitget"  # stale preference must not define actual routing
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.market_data.ccxt_exchange = "binance"

    result = exchange_alignment(cfg)
    assert result.execution_exchange == "okx"
    assert result.data_exchange == "binance"
    assert result.status == "diverged"
    assert result.diverged is True


def test_exchange_alignment_local_paper_never_fabricates_divergence():
    from aletheia.config.exchange_alignment import exchange_alignment

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.market_data.ccxt_exchange = "okx"

    result = exchange_alignment(cfg)
    assert result.execution_exchange is None
    assert result.status == "local_paper"
    assert result.diverged is False


def test_config_diagnostic_names_actual_execution_exchange():
    from aletheia.diagnostics.config_consistency import _check_exchange_consistency

    cfg = AletheiaConfig()
    cfg.exchange.name = "bitget"
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.market_data.ccxt_exchange = "binance"

    finding = _check_exchange_consistency(cfg)
    assert finding is not None
    assert "execution (okx)" in finding.message
    assert "market data (binance)" in finding.message
    assert "bitget" not in finding.message
