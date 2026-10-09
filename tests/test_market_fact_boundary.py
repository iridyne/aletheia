"""Pure market facts remain usable without the concrete network provider."""

from pathlib import Path
import subprocess
import sys


def test_market_facts_and_causal_windows_without_ccxt_provider():
    script = '''
import sys
from datetime import datetime, timezone
class MissingProvider:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions.data.ccxt_provider":
            raise AssertionError("Core facts must not import the concrete provider")
sys.meta_path.insert(0, MissingProvider())
from aletheia.data.market_structure import MarketStructureProvider
from aletheia.trading.entry_analysis import entry_causal_windows
from aletheia.data.providers import Ticker, timeframe_ms, require_exact_ticker_venue

expected = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000,
            "1h": 3600000, "2h": 7200000, "4h": 14400000, "6h": 21600000,
            "8h": 28800000, "12h": 43200000, "1d": 86400000,
            "3d": 259200000, "1w": 604800000, "2m": 0, "1M": 0, "": 0}
assert {key: timeframe_ms(key) for key in expected} == expected
ticker = Ticker("BTC/USDT", 100, 10, 0, provider="bitget")
assert require_exact_ticker_venue(ticker, " BITGET ") is ticker
for value, venue in ((ticker, "okx"), (ticker, ""), (object(), "bitget"),
                     (Ticker("BTC/USDT", 100, 10, 0), "bitget")):
    try:
        require_exact_ticker_venue(value, venue)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown/mismatched venue must remain rejected")
windows, structures, refs = entry_causal_windows(
    "bitget", "BTC/USDT", "perp", {}, datetime.now(timezone.utc),
)
assert all(row["availability"] == "unavailable" for row in windows.values())
assert all(row["availability"] == "unavailable" for row in structures.values())
assert not refs
assert "extensions.data.ccxt_provider" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_concrete_provider_has_no_forwarding_fact_exports():
    from extensions.data import ccxt_provider

    assert not hasattr(ccxt_provider, "timeframe_ms")
    assert not hasattr(ccxt_provider, "require_exact_ticker_venue")
