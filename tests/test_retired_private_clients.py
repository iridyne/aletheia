"""Only the UTA implementation remains for frozen OKX/Binance accounts."""

import importlib

import pytest


@pytest.mark.parametrize("venue", ["okx", "binance"])
def test_unused_private_client_cannot_be_imported(venue):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"aletheia.exchanges.{venue}.client")
