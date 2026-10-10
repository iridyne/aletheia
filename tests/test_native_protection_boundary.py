"""Native protection reads cannot regain an alternate trading implementation."""

import inspect

import pytest


@pytest.mark.real_place_order
def test_native_client_exposes_only_required_private_reads():
    from extensions.exchanges.bitget.client import BitgetClient

    methods = {
        name for name, member in inspect.getmembers(BitgetClient)
        if not name.startswith("_") and callable(member)
    }
    assert methods == {
        "get_plan_order_history", "get_order_fills", "get_order_fill_reports",
        "get_open_tpsl_orders",  # entry-preset read-back, execution_contract §4 (#3848)
    }


@pytest.mark.parametrize("venue", ["bitget", "okx", "binance"])
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_default_guard_blocks_real_ccxt_mutation_transport(venue, method):
    import ccxt.async_support as ccxt

    exchange = getattr(ccxt, venue)()
    try:
        with pytest.raises(RuntimeError, match="real CCXT mutation transport"):
            await exchange.fetch("https://exchange.invalid/orders", method=method)
    finally:
        await exchange.close()
