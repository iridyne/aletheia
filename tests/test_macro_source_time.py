"""#1465: an acquisition clock must not rejuvenate daily source facts."""
import json
import builtins
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest

from aletheia.data.collectors import macro
from aletheia.data.context_builder import build_market_context_from_db
from aletheia.data.freshness import fresh_row
from extensions.regime.collector import _get_latest_vix
from aletheia.storage import repositories as repo


def test_stored_macro_context_survives_absent_collector(monkeypatch):
    row = dict(
        observed_at=datetime.now(timezone.utc).isoformat(),
        vix_close=40, risk_regime='risk_off', fng_value=55,
        total_market_cap_change_24h_pct=1.5,
    )
    repo.insert_macro_snapshot(row)
    raw = repo._select_all('macro_snapshots', limit=1)[0]
    real_import = builtins.__import__

    def without_collector(name, *args, **kwargs):
        if name == 'aletheia.data.collectors.macro':
            raise ModuleNotFoundError(name, name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_collector)
    current = repo.latest_macro_snapshot()
    context = build_market_context_from_db([])
    for projected in (current, context.macro):
        assert projected['vix_close'] is None  # no source proof, not rejuvenated
        assert projected['risk_regime'] == 'neutral'
        assert projected['fng_value'] == 55
    assert repo._select_all('macro_snapshots', limit=1)[0] == raw


@pytest.mark.asyncio
async def test_fred_source_dates_survive_collection_storage_and_current_consumers(monkeypatch):
    now = datetime.now(timezone.utc)
    source_date = (now - timedelta(days=4)).date().isoformat()
    @asynccontextmanager
    async def client(**kwargs):
        class Fake:
            async def get(self, url, params):
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                    'observations': [{'date': now.date().isoformat(), 'value': '.'},
                                     {'date': source_date, 'value': '40'}]})
        yield Fake()
    async def cg(): return 52.0, 2e12, 1.5
    async def fng(): return 55, 'Greed'
    monkeypatch.setattr(macro, 'httpx_client', client)
    monkeypatch.setattr(macro, '_fetch_coingecko', cg)
    monkeypatch.setattr(macro, '_fetch_fng', fng)
    monkeypatch.setattr('aletheia.config.secrets.get_secret', lambda _: 'fixture')
    monkeypatch.setattr('aletheia.config.loader.load_config', lambda: SimpleNamespace(
        market_data=SimpleNamespace(enable_fear_greed=True)))
    snapshot = (await macro.MacroCollector().collect([]))['snapshot']
    repo.insert_macro_snapshot(snapshot.model_dump())
    raw = repo._select_all('macro_snapshots', limit=1)[0]
    proof = json.loads(raw['source_times_json'])
    for field in ('spy_close','vix_close','dxy_close','nasdaq_close'):
        assert raw[field] == 40
        assert proof[field]['source_observed_at'] == source_date
        assert proof[field]['precision'] == 'date'
        assert datetime.fromisoformat(proof[field]['acquired_at']) >= now
    current = repo.latest_macro_snapshot()
    assert all(current[field] is None for field in proof)
    assert current['fng_value'] == 55 and current['btc_dominance'] == 52
    assert current['risk_regime'] == 'neutral'  # stale VIX=40 cannot force risk_off
    context = build_market_context_from_db([])
    assert context.macro['vix_close'] is None
    assert context.macro['source_times_json'] == raw['source_times_json']
    assert context.macro['fng_value'] == 55
    assert _get_latest_vix() is None
    from aletheia.server.routes.data import get_macro_snapshot
    assert get_macro_snapshot()['data']['vix_close'] is None
    assert repo._select_all('macro_snapshots', limit=1)[0] == raw


@pytest.mark.parametrize('source,precision', [
    (None, 'date'), ('bad-date', 'date'), ('2099-01-01', 'date'),
    ('today', 'date'), ('today', 'timestamp'), ('old', 'timestamp'), ('future', 'timestamp'),
])
def test_source_clock_cannot_be_replaced_by_receipt(source, precision):
    now = datetime.now(timezone.utc)
    source = {'today':now.date().isoformat(), 'old':(now-timedelta(days=3)).isoformat(),
              'future':(now+timedelta(days=3)).isoformat()}.get(source,source)
    row = dict(observed_at=now.isoformat(), vix_close=40, btc_dominance=52,
               source_times_json=json.dumps({'vix_close':dict(source='fred',
                   source_observed_at=source,acquired_at=now.isoformat(),precision=precision)}))
    result = fresh_row(row,'macro',now=now)
    assert result['vix_close'] is None and result['btc_dominance'] == 52
    assert result['risk_regime'] == 'unavailable'
    assert row['vix_close'] == 40


def test_proved_field_and_independent_field_remain_fresh():
    now = datetime.now(timezone.utc)
    row = dict(observed_at=now.isoformat(),vix_close=40,btc_dominance=52,
               source_times_json=json.dumps({'vix_close':dict(source='fixture',precision='timestamp',
                   source_observed_at=(now-timedelta(minutes=5)).isoformat(),acquired_at=now.isoformat())}))
    result=fresh_row(row,'macro',now=now)
    assert result['vix_close']==40 and result['risk_regime']=='risk_off'
    assert fresh_row(row,'macro',now=now+timedelta(minutes=31)) is None


def test_historical_macro_is_not_rewritten_or_given_source_proof():
    row=dict(observed_at=datetime.now(timezone.utc).isoformat(),vix_close=40,risk_regime='risk_off',fng_value=55)
    repo.insert_macro_snapshot(row)
    raw=repo._select_all('macro_snapshots',limit=1)[0]
    current=repo.latest_macro_snapshot()
    assert raw['vix_close']==40 and raw['source_times_json'] is None
    assert current['vix_close'] is None and current['risk_regime']=='unavailable'
    assert current['fng_value']==55
    assert repo._select_all('macro_snapshots',limit=1)[0]==raw


def test_yfinance_daily_bar_preserves_its_source_date(monkeypatch):
    import sys
    import pandas as pd
    hist=pd.DataFrame({'Close':[30.0]}, index=pd.DatetimeIndex(['2026-09-04'],tz='America/New_York'))
    monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=lambda **_:hist)))
    value,proof=macro._fetch_one_tradfi_ticker('^VIX')
    assert value==30 and proof['source_observed_at']=='2026-09-04'
    assert proof['precision']=='date' and proof['source']=='yfinance'
