"""Shared test fixtures + safety guards.

`bitget_demo` (now the default `paper_execution_mode`) places REAL orders on Bitget's
demo API. Secrets are read from the OS keyring first, which the per-test `tmp_path`
DATA_DIR monkeypatch does NOT sandbox — so without a guard an unmocked test that runs
paper_trader to a trade could fire a real demo order over the network during pytest.

This autouse guard makes "tests never touch the real exchange" structural: it replaces
`BitgetClient.place_order` with a loud error by default. Tests that exercise the
exchange path provide their own `monkeypatch.setattr(...place_order, fake)`, which
overrides this within the test body. A test that reaches the exchange without a mock
fails loudly instead of silently placing a real order.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_place_order: test exercises BitgetClient.place_order directly with httpx "
        "mocked (no network) — opt out of the place_order guard.",
    )


@pytest.fixture(autouse=True)
def _block_real_exchange_orders(request, monkeypatch):
    # Tests that verify place_order's own body-building mock httpx instead, so they
    # opt out of this guard via @pytest.mark.real_place_order.
    if request.node.get_closest_marker("real_place_order"):
        return
    from aletheia.exchanges.bitget import client as bg

    async def _blocked(self, order):  # noqa: ARG001
        raise RuntimeError(
            "BitgetClient.place_order reached the real exchange in a test. "
            "Mock it with monkeypatch.setattr(BitgetClient, 'place_order', fake) "
            "or route the proposal through paper_internal."
        )

    monkeypatch.setattr(bg.BitgetClient, "place_order", _blocked)
