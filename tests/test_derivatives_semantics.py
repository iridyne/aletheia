"""Current economic consumers must use source-proved units and intervals."""
from datetime import datetime, timedelta, timezone
import pytest
from aletheia.data.collectors import derivatives
from aletheia.storage import repositories as repo

MARKET=dict(id='BTC-USDT-SWAP',symbol='BTC/USDT:USDT',swap=True,base='BTC',quote='USDT',settle='USDT',linear=True,contractSize=.01)

@pytest.mark.parametrize('interval,expected',[('1h',8.76),('8h',1.095),(None,None)])
def test_current_funding_annualization_and_history_read(interval,expected):
    raw={'fundingRate':.001,'interval':interval}
    snap=derivatives._parse_funding('BTC/USDT','okx',raw,market=MARKET)
    assert snap.funding_rate_annualized==pytest.approx(expected) if expected is not None else snap.funding_rate_annualized is None
    repo.insert_funding_rate_snapshot(snap.model_dump())
    current=repo.latest_funding_rate('BTC/USDT','okx')
    assert current['funding_rate_annualized']==pytest.approx(expected) if expected is not None else current['funding_rate_annualized'] is None

@pytest.mark.asyncio
@pytest.mark.parametrize('raw,expected',[
    ({'openInterestAmount':100},None),
    ({'openInterestValue':123,'info':{'oiUsd':'123'}},123),
    ({'openInterestValue':123},None),
])
async def test_universe_live_oi_never_guesses_units(monkeypatch,raw,expected):
    from aletheia.universe.features import _fetch_single_feature
    now=datetime.now(timezone.utc)
    monkeypatch.setattr(repo,'get_latest_feature_snapshot',lambda *a:{'id':'old-feature','ts':now.isoformat(),'open_interest':6000000,'oi_delta_1h':None})
    class Exchange:
        id='okx'
        markets={'BTC/USDT:USDT':MARKET}
        async def fetch_ohlcv(self,*a,**kw):
            return [[int((now-timedelta(days=1)).timestamp()*1000),100,101,99,100,1000]]
        async def fetch_funding_rate(self,*a,**kw):
            return {'fundingRate':.001,'markPrice':60000,'interval':'8h','timestamp':int(now.timestamp()*1000)}
        async def fetch_open_interest(self,*a,**kw):
            return {**raw,'symbol':'BTC/USDT:USDT','timestamp':int(now.timestamp()*1000)}

        async def get_funding_observation(self, _symbol, *, ticker=None):
            return {
                "funding_rate": .001,
                "observed_at": now.isoformat(),
                "received_at": now.isoformat(),
                "evidence": {},
            }

        async def get_open_interest_observation(self, _symbol):
            available = raw.get('openInterestValue') == 123 and raw.get('info') == {'oiUsd': '123'}
            return {
                "observed_at": now.isoformat(),
                "received_at": now.isoformat(),
                "fact": {
                    "availability": "available" if available else "unavailable",
                    "value": 123 if available else None,
                },
            }
    feature=await _fetch_single_feature(Exchange(),{'id':'btc','symbol':'BTC/USDT','venue':'okx','market_type':'perp'},
        {'bid':99,'ask':101},now,lambda s:s+':USDT',snapshot_buffer=[])
    assert feature['open_interest']==expected
    assert feature['feature_snapshot_id'] != 'old-feature'


def test_unproved_history_remains_readable_without_current_economic_authority():
    now=datetime.now(timezone.utc).isoformat()
    repo.insert_funding_rate_snapshot(dict(id='old-f',symbol='BTC/USDT',exchange='okx',funding_rate=.001,funding_rate_annualized=1.095,observed_at=now))
    repo.insert_open_interest_snapshot(dict(id='old-oi',symbol='BTC/USDT',exchange='okx',open_interest_usd=6000000,observed_at=now))
    assert repo.latest_funding_rate('BTC/USDT','okx')['funding_rate_annualized'] is None
    assert repo.latest_open_interest('BTC/USDT','okx') is None
    assert repo._select_all('funding_rate_snapshots')[0]['funding_rate_annualized']==1.095
    assert repo._select_all('open_interest_snapshots')[0]['open_interest_usd']==6000000


def test_contract_quantity_and_nonfinite_values_use_one_canonical_primitive():
    from aletheia.data.derivatives_facts import normalize_open_interest, instrument_from_market
    now=datetime.now(timezone.utc)
    args=dict(instrument=instrument_from_market('BTC/USDT',MARKET),provider='ccxt',venue='okx',acquired_at=now,
        amount_unit='contracts',mark_price=60000)
    assert normalize_open_interest({'openInterestAmount':100},**args)['value']==60000
    assert normalize_open_interest({'openInterestAmount':float('nan')},**args)['value'] is None
    args['instrument']={**args['instrument'],'contract_size':None}
    assert normalize_open_interest({'openInterestAmount':100},**args)['unavailable_reason']=='oi_unit_unproven'


def test_nullable_migration_preserves_historical_values_rowids_and_indexes():
    import sqlite3
    from aletheia.storage.migrations import _apply_nullable_funding_annualization_v82
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE funding_rate_snapshots (id TEXT PRIMARY KEY, funding_rate_annualized REAL NOT NULL, derivatives_metadata_json TEXT)')
    db.execute('CREATE INDEX old_funding_index ON funding_rate_snapshots (funding_rate_annualized)')
    db.execute("INSERT INTO funding_rate_snapshots(rowid,id,funding_rate_annualized,derivatives_metadata_json) VALUES(17,'old',1.095,NULL)")
    before=db.execute('SELECT rowid,* FROM funding_rate_snapshots').fetchall()
    _apply_nullable_funding_annualization_v82(db)
    assert db.execute('SELECT rowid,* FROM funding_rate_snapshots').fetchall()==before
    db.execute("INSERT INTO funding_rate_snapshots VALUES('new',NULL,NULL)")
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='old_funding_index'").fetchone()
    _apply_nullable_funding_annualization_v82(db)
    assert db.execute('SELECT count(*) FROM funding_rate_snapshots').fetchone()[0]==2


def test_context_and_microstructure_do_not_display_unproved_annualization():
    from aletheia.server.routes.data import get_market_microstructure
    from aletheia.data.normalizer import compute_leverage_state
    from aletheia.data.context_builder import render_enriched_context_md
    now=datetime.now(timezone.utc).isoformat()
    repo.insert_funding_rate_snapshot(dict(symbol='BTC/USDT',exchange='okx',funding_rate=.001,funding_rate_annualized=1.095,observed_at=now))
    current=repo.latest_funding_rate('BTC/USDT','okx')
    state=compute_leverage_state('BTC/USDT',funding_rate=current['funding_rate'],funding_annualized=current['funding_rate_annualized'])
    assert state.funding_annualized is None
    text=render_enriched_context_md(['BTC/USDT'],{}, {'BTC/USDT':state}, None)
    assert '109.5%' not in text
    assert get_market_microstructure('BTC/USDT',exchange='okx',market_type='perp')['funding_rate_annualized'] is None


def test_current_oi_baseline_does_not_promote_comparison_observations():
    from aletheia.data.derivatives_facts import ccxt_metadata, comparison_row, read_derivatives_facts
    now=datetime.now(timezone.utc)
    old=(now-timedelta(hours=1)).isoformat()
    raw={'openInterestValue':100,'info':{'oiUsd':100}}
    metadata=ccxt_metadata('BTC/USDT','okx',MARKET,raw,family='open_interest')
    repo.insert_open_interest_snapshot(comparison_row(dict(symbol='BTC/USDT',exchange='okx',open_interest_usd=100,
        observed_at=old,acquired_at=old,source_time_status='missing',derivatives_metadata_json=metadata)))
    current=derivatives._parse_oi('BTC/USDT','okx',{'openInterestValue':200,'info':{'oiUsd':200}},market=MARKET)
    assert current.oi_change_1h_pct is None
    repo.insert_open_interest_snapshot(current.model_dump())
    assert repo.latest_open_interest('BTC/USDT','okx')['oi_change_1h_pct'] is None
    diagnostic=read_derivatives_facts('BTC/USDT',venue='okx')
    assert next(f for f in diagnostic if f['fact_type']=='open_interest_change_1h')['value']==1
