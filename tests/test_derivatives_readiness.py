"""Readiness is a durable read: compatible parents, honest coverage, no legacy scalar mutation."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from aletheia.config.schema import AletheiaConfig
from aletheia.data.derivatives_facts import derivatives_readiness, price_snapshot, snapshot_metadata
from aletheia.storage import repositories as repo

NOW = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)


def store(venue, *, age=0, oi=100, interval=28800, settle='USDT', source_id=None):
    stamp = (NOW-timedelta(minutes=age)).isoformat()
    instrument = dict(canonical_symbol='BTC/USDT', source_instrument_id=source_id or venue+'BTC',
        base_asset='BTC', quote_asset=settle, settle_asset=settle, market_type='perp', linear=True)
    metadata = snapshot_metadata(provider='ccxt', venue='hyperliquid' if venue=='hyperliquid_predicted' else venue,
        value_kind='forecast' if venue=='hyperliquid_predicted' else 'observed',
        instrument=instrument, oi_unit='USD', interval_seconds=interval)
    common = dict(symbol='BTC/USDT', exchange=venue, source_observed_at=stamp, acquired_at=stamp,
        source_time_status='provided', observed_at=stamp, derivatives_metadata_json=metadata)
    repo.insert_open_interest_snapshot(dict(common, open_interest_usd=oi))
    repo.insert_funding_rate_snapshot(dict(common, funding_rate=.0008, funding_rate_annualized=87.6))
    return instrument


def test_readiness_requires_compatible_exact_venues_and_keeps_unknowns(monkeypatch):
    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange='bitget'
    store('binance', age=60, oi=80)
    store('binance', age=59, oi=1, source_id='DIFFERENT')
    instrument = store('binance', oi=100, interval=None)
    store('okx', oi=200)
    store('hyperliquid', oi=900, settle='USDC')
    store('coinalyze', oi=10000)  # even a provider-tagged aggregate must never enter the subtotal
    row = price_snapshot('BTC/USDT','binance',{'markPrice':101,'indexPrice':100,'timestamp':int(NOW.timestamp()*1000)},
        instrument=instrument, acquired_at=NOW)
    repo.insert_perp_price_snapshot(row)
    before = repo.latest_open_interest('BTC/USDT','binance')
    coverage = dict(acquired_at=NOW.isoformat(), venues={'bitget':{'BTC/USDT':{'listed':True,'funding':'provider_error','open_interest':'oi_unit_unproven'}}})
    monkeypatch.setattr(repo,'get_collector_health_state',lambda domain: {'derivatives_coverage_json':json.dumps(coverage)})
    result = derivatives_readiness('BTC/USDT',cfg,now=NOW)
    assert result['mode']=='observe_only'
    assert result['exact_venues']['bitget']['families']['funding_rate']['reason']=='provider_error'
    facts = result['exact_venues']['binance']['facts']
    assert next(f for f in facts if f['fact_type']=='open_interest_change_1h')['value']==pytest.approx(.25)
    assert next(f for f in facts if f['fact_type']=='perpetual_premium')['value']==pytest.approx(.01)
    oi = next(c for c in result['comparisons'] if c['fact_type']=='open_interest')
    assert oi['covered_subtotal']==300
    assert oi['covered_venues']==['binance','okx']
    assert not any(c['fact_type']=='funding_rate' for c in result['comparisons'])
    assert 'coinalyze' in result['aggregate_providers']
    assert repo.latest_open_interest('BTC/USDT','binance')==before
    stale = derivatives_readiness('BTC/USDT',cfg,now=NOW+timedelta(hours=1))
    assert not stale['comparisons'] and not stale['coverage_fresh']


def test_missing_baseline_reference_and_current_not_listed_stay_unavailable(monkeypatch):
    cfg=AletheiaConfig()
    instrument=store('okx')
    row=price_snapshot('BTC/USDT','okx',{'markPrice':100},instrument=instrument,acquired_at=NOW)
    repo.insert_perp_price_snapshot(row)
    result=derivatives_readiness('BTC/USDT',cfg,now=NOW)
    families=result['exact_venues']['okx']['families']
    assert families['open_interest_change_1h']['reason']=='compatible_baseline_missing'
    assert families['perpetual_premium']['availability']=='unavailable'
    coverage=dict(acquired_at=NOW.isoformat(),venues={'okx':{'BTC/USDT':{'listed':False}}})
    monkeypatch.setattr(repo,'get_collector_health_state',lambda domain:{'derivatives_coverage_json':json.dumps(coverage)})
    result=derivatives_readiness('BTC/USDT',cfg,now=NOW)
    assert all(f['value'] is None for f in result['exact_venues']['okx']['facts'])
    assert all(f['unavailable_reason']=='not_listed' for f in result['exact_venues']['okx']['facts'])


def test_hourly_funding_comparison_excludes_forecast_and_bad_baseline(monkeypatch):
    cfg=AletheiaConfig()
    store('binance', age=60, oi=0)
    store('binance', interval=3600)
    store('okx', interval=28800)
    store('hyperliquid_predicted', interval=3600)
    result=derivatives_readiness('BTC/USDT',cfg,now=NOW)
    funding=next(c for c in result['comparisons'] if c['fact_type']=='funding_rate')
    assert funding['unit']=='rate_per_hour'
    assert funding['values_by_venue']==pytest.approx({'binance':.0008,'okx':.0001})
    assert funding['dispersion']==pytest.approx(.0007)
    assert result['exact_venues']['binance']['families']['open_interest_change_1h']['reason']=='compatible_baseline_missing'
    from aletheia.server.routes import data
    monkeypatch.setattr(data, 'load_config', lambda:cfg)
    response=data.get_market_derivatives_facts('BTC/USDT')
    assert response['mode']=='observe_only'
    assert response['configured_venues']==result['configured_venues']
