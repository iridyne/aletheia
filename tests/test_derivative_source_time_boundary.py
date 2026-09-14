"""Pure derivatives normalization must work without the optional collector."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_facts_and_feature_time_do_not_import_derivatives_collector():
    script = '''
import sys
from datetime import datetime, timezone
class MissingCollector:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.data.collectors.derivatives":
            raise ModuleNotFoundError("collector intentionally absent", name=fullname)
sys.meta_path.insert(0, MissingCollector())
from aletheia.data.derivatives_facts import ccxt_metadata, normalize_funding, normalize_open_interest, price_snapshot
from aletheia.universe.features import _provider_observed_at
now = datetime(2026, 9, 14, tzinfo=timezone.utc)
raw = {"timestamp": int(now.timestamp() * 1000), "fundingRate": .001, "openInterestValue": 12, "markPrice": 100}
instrument = {"canonical_symbol": "BTC/USDT", "source_instrument_id": "BTCUSDT", "market_type": "perp", "linear": True, "base_asset": "BTC", "quote_asset": "USDT", "settle_asset": "USDT"}
assert normalize_funding(raw, instrument=instrument, provider="fixture", venue="bitget", acquired_at=now, interval_seconds=3600)["source_observed_at"] == now.isoformat()
assert normalize_open_interest(raw, instrument=instrument, provider="fixture", venue="bitget", acquired_at=now, value_unit="quote_currency")["source_observed_at"] == now.isoformat()
assert price_snapshot("BTC/USDT", "bitget", raw, instrument=instrument, acquired_at=now)["source_observed_at"] == now.isoformat()
import json
assert json.loads(ccxt_metadata("BTC/USDT", "okx", {}, {"info": {"ts": raw["timestamp"]}}, family="funding", acquired_at=now.isoformat()))["source_observed_at"] == now.isoformat()
assert _provider_observed_at(raw, received_at=now) == now.isoformat()
assert "aletheia.data.collectors.derivatives" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("raw,status", [
    ({}, "missing"),
    ({"timestamp": True}, "invalid"),
    ({"timestamp": "nan"}, "invalid"),
    ({"timestamp": "inf"}, "invalid"),
    ({"timestamp": "broken", "datetime": "2026-09-14T00:00:00Z"}, "invalid"),
    ({"datetime": "2026-09-14T00:00:00"}, "provided"),
    ({"datetime": "2026-09-14T08:00:00+08:00"}, "provided"),
])
def test_source_time_preserves_missing_invalid_and_timezone_semantics(raw, status):
    from datetime import datetime, timezone
    from aletheia.data.derivatives_facts import provider_source_time

    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    source, actual = provider_source_time(raw, now)
    assert actual == status
    assert source == (now.isoformat() if status == "provided" else None)


def test_future_skew_boundary_and_retired_collector_entry():
    from datetime import datetime, timedelta, timezone
    from aletheia.data.collectors import derivatives
    from aletheia.data.derivatives_facts import provider_source_time
    from aletheia.data.freshness import MAX_FUTURE_SKEW

    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    at_limit = now + MAX_FUTURE_SKEW
    for source, status in [(at_limit, "provided"), (at_limit + timedelta(milliseconds=1), "future")]:
        assert provider_source_time({"timestamp": source.timestamp() * 1000}, now) == (source.isoformat(), status)
    assert not hasattr(derivatives, "provider_source_time")
