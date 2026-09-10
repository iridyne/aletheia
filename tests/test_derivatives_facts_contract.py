"""P0 RED probes for the proposed #2890 contract; no implementation yet."""

from datetime import datetime, timezone
import importlib
import pytest

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
INSTRUMENT = dict(canonical_symbol='BTC/USDT', source_instrument_id='BTCUSDT',
    market_type='perp', base_asset='BTC', quote_asset='USDT', settle_asset='USDT', linear=True)


def facts():
    return importlib.import_module('aletheia.data.derivatives_facts')


@pytest.mark.parametrize('interval', [None, 3600, 28800])
def test_funding_preserves_observed_rate_without_inventing_interval(interval):
    fact = facts().normalize_funding({'fundingRate': .001, 'timestamp': int(NOW.timestamp()*1000)},
        instrument=INSTRUMENT, provider='ccxt', venue='bitget', acquired_at=NOW,
        interval_seconds=interval, value_kind='observed')
    assert fact['value'] == .001
    assert fact['interval_seconds'] == interval
    assert fact['value_kind'] == 'observed'
    assert fact['source_scope'] == 'venue_native'
    assert fact['venue'] == 'bitget'


def test_ambiguous_oi_amount_is_unavailable_even_with_mark():
    fact = facts().normalize_open_interest({'openInterestAmount': 100},
        instrument=INSTRUMENT, provider='ccxt', venue='okx', acquired_at=NOW,
        amount_unit=None, mark_price=60000)
    assert fact['availability'] == 'unavailable'
    assert fact['value'] is None
    assert fact['unavailable_reason'] == 'oi_unit_unproven'


def test_missing_source_clock_remains_missing_and_forecast_stays_forecast():
    fact = facts().normalize_funding({'fundingRate': .001},
        instrument={**INSTRUMENT, 'source_instrument_id': 'BTC', 'quote_asset': 'USDC', 'settle_asset': 'USDC'},
        provider='hyperliquid_public', venue='hyperliquid', acquired_at=NOW,
        interval_seconds=3600, value_kind='forecast')
    assert fact['source_observed_at'] is None
    assert fact['acquired_at'] == NOW.isoformat()
    assert fact['value_kind'] == 'forecast'


def test_nonfinite_funding_cannot_be_available():
    fact = facts().normalize_funding({'fundingRate': float('nan')}, instrument=INSTRUMENT,
        provider='ccxt', venue='bitget', acquired_at=NOW, interval_seconds=None, value_kind='observed')
    assert fact['availability'] == 'invalid'
    assert fact['value'] is None


def test_aggregate_liquidation_does_not_gain_exact_venue_or_zero_price():
    fact = facts().project_liquidation({'symbol':'BTC/USDT','exchange':'coinalyze',
        'quantity_usd':250000,'price':0.0,'side':'long','event_time':NOW.isoformat(),
        'observed_at':NOW.isoformat()}, source_scope='market_aggregate', instrument=INSTRUMENT, now=NOW)
    assert fact['venue'] is None
    assert fact['source_scope'] == 'market_aggregate'
    assert fact['event_price'] is None
    assert fact['unit'] == 'USD'
