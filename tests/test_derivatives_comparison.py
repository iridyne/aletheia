from tests.market_data import parse_funding, parse_oi
from extensions.market_data import decode_derivatives_metadata as ccxt_metadata
from extensions.exchanges.ccxt import market_data as wire
"""Public-source replay, single-writer ownership and legacy-consumer isolation."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.data.collectors import derivatives
from aletheia.data.derivatives_facts import comparison_row, read_derivatives_facts
from aletheia.storage import repositories as repo

PUBLIC = json.loads((Path(__file__).parent / 'fixtures/derivatives_public_ccxt_20260910.json').read_text())


@pytest.mark.parametrize("fails", [False, True])
def test_comparison_core_uses_normalized_provider_and_closes(monkeypatch, fails):
    from aletheia.data import ccxt_provider
    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "fixture"
    events = []
    monkeypatch.setattr(derivatives, "comparison_workset", lambda cfg: {"fixture": ["BTC/USDT"]})

    class Provider:
        # The orchestration path must not need a raw exchange/private name.
        async def get_derivatives_comparison(self, symbols, *, owns_primary, acquired):
            assert symbols == ["BTC/USDT"] and owns_primary is True
            assert acquired.tzinfo is not None
            if fails:
                raise RuntimeError("adapter unavailable")
            return dict(funding=[], open_interest=[], prices=[], errors=["missing"],
                        coverage={"BTC/USDT": {"price": "unsupported"}})

        async def close(self):
            events.append("closed")

    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", lambda **kwargs: Provider())
    if fails:
        with pytest.raises(RuntimeError, match="adapter unavailable"):
            asyncio.run(derivatives.DerivativesCollector().collect_comparison(cfg))
    else:
        result = asyncio.run(derivatives.DerivativesCollector().collect_comparison(cfg))
        assert result["errors"] == ["missing"]
        assert result["coverage"]["venues"] == {"fixture": {"BTC/USDT": {"price": "unsupported"}}}
    assert events == ["closed"]
    assert not hasattr(derivatives, "_parse_funding")


@pytest.mark.parametrize('primary', ['binance', 'bitget'])
def test_public_replay_preserves_ownership_partial_failure_and_price_identity(monkeypatch, primary):
    from aletheia.data import ccxt_provider, freshness
    now = datetime(2026, 9, 10, 14, 10, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(derivatives, 'datetime', Clock)
    monkeypatch.setattr(wire, 'datetime', Clock)
    monkeypatch.setattr(freshness, 'datetime', Clock)
    calls = []
    payloads = {r['venue']: r for r in PUBLIC['results']}
    class Exchange:
        def __init__(self, venue):
            self.venue = venue
            self.raw = payloads[venue]
            self.markets = {'BTC/USDT:USDT': self.raw['market']}
            self.has = {**self.raw['has'], 'fetchOpenInterestHistory': venue == 'binance'}
        async def load_markets(self):
            return self.markets
        async def fetch_funding_rates(self, symbols, **kwargs):
            calls.append((self.venue, 'funding'))
            return {'BTC/USDT:USDT': self.raw['fetch_funding_rate']}
        async def fetch_open_interests(self, symbols):
            calls.append((self.venue, 'oi'))
            raise RuntimeError('simulated public OI outage')
        async def fetch_open_interest_history(self, symbol, timeframe, limit):
            calls.append((self.venue, 'oi'))
            return self.raw['fetch_open_interest_history']
        async def fetch_mark_prices(self, symbols):
            calls.append((self.venue, 'price'))
            return {'BTC/USDT:USDT': self.raw['fetch_mark_price']}
        async def fetch_mark_price(self, symbol):
            calls.append((self.venue, 'price'))
            return self.raw['fetch_mark_price']
        async def close(self):
            pass

    def Provider(*, exchange, fallback_exchange, native_ccxt):
        from extensions.market_data import build_native_provider
        assert native_ccxt is True
        assert exchange == fallback_exchange
        provider = build_native_provider(exchange, fallback_exchange)
        provider._primary = provider._primary_perp = Exchange(exchange)
        return provider
    monkeypatch.setattr(ccxt_provider, 'get_market_data_provider', Provider)
    monkeypatch.setattr(repo, 'list_universe_assets', lambda **kw: [{'symbol':'NOTLISTED/USDT', 'market_type':'perp'}])
    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = primary
    cfg.market_data.observed_instruments = [f'{primary}:BTC/USDT:perp']
    result = asyncio.run(derivatives.DerivativesCollector().collect_comparison(cfg))
    assert (primary, 'funding') not in calls and (primary, 'oi') not in calls
    assert len(result['funding']) == 2
    assert {r['exchange'] for r in result['prices']} == {'binance','okx','bitget'}
    for row in result['prices']:
        assert json.loads(row['instrument_json'])['source_instrument_id'] == payloads[row['exchange']]['market']['id']
        assert row['source_observed_at'] is not None
    assert next(r for r in result['prices'] if r['exchange']=='okx')['reference_price'] is None
    assert result['coverage']['venues']['okx']['BTC/USDT']['open_interest'] == 'provider_error'
    assert result['coverage']['venues']['okx']['NOTLISTED/USDT']['listed'] is False
    assert result['errors']
    with repo.transaction():
        for _ in range(2):
            repo.insert_collector_snapshot_batch(funding=result['funding'], open_interest=result['open_interest'])
            for row in result['prices']:
                repo.insert_perp_price_snapshot(row)
    assert len(repo._select_all('funding_rate_snapshots')) == 2
    assert len(repo._select_all('perp_price_snapshots')) == 3
    assert repo.latest_funding_rate('BTC/USDT') is None
    assert repo.batch_funding_rates(['BTC/USDT'], 'okx') == {}
    canonical = read_derivatives_facts('BTC/USDT', venue='okx', now=now)
    assert canonical[0]['value'] == payloads['okx']['fetch_funding_rate']['fundingRate']
    assert canonical[0]['interval_seconds'] == 28800
    if primary != 'binance':
        assert read_derivatives_facts('BTC/USDT', venue='binance', now=now)[0]['interval_seconds'] is None


def test_newer_comparison_rows_cannot_change_legacy_scalar_consumers(monkeypatch):
    from aletheia.server.routes.data import get_market_microstructure
    now = datetime.now(timezone.utc)
    market = next(r for r in PUBLIC['results'] if r['venue']=='okx')['market']
    funding = parse_funding('BTC/USDT','okx', {'fundingRate':.001,'interval':'8h'},market=market).model_dump()
    oi = parse_oi('BTC/USDT','okx', {'openInterestValue':1000000,'info':{'oiUsd':1000000}},market=market).model_dump()
    repo.insert_collector_snapshot_batch(funding=[funding], open_interest=[oi])
    stamp = (now+timedelta(seconds=1)).isoformat()
    for row, family, writer in ((funding,'funding',repo.insert_funding_rate_snapshot),
                                (oi,'open_interest',repo.insert_open_interest_snapshot)):
        newer = {**row, 'observed_at':stamp, 'acquired_at':stamp,
            'derivatives_metadata_json':ccxt_metadata('BTC/USDT','okx',market,{},family=family)}
        if family == 'funding':
            newer['funding_rate'] = .9
        else:
            newer['open_interest_usd'] = 9000000
        writer(comparison_row(newer))
    assert repo.latest_funding_rate('BTC/USDT')['funding_rate'] == .001
    assert repo.latest_open_interest('BTC/USDT','okx')['open_interest_usd'] == 1000000
    assert repo.batch_funding_rates(['BTC/USDT'],'okx')['BTC/USDT']['funding_rate'] == .001
    assert repo.batch_open_interest(['BTC/USDT'],'okx')['BTC/USDT']['open_interest_usd'] == 1000000
    assert repo.open_interest_near('BTC/USDT','okx', reference_at=now+timedelta(hours=1,seconds=1))['open_interest_usd'] == 1000000
    telemetry = get_market_microstructure('BTC/USDT', exchange='okx', market_type='perp')
    assert telemetry['funding_rate'] == .001 and telemetry['open_interest_usd'] == 1000000
    assert read_derivatives_facts('BTC/USDT', venue='okx')[0]['value'] == .9


def test_workset_uses_complete_registry_and_config_not_candidate_rank(monkeypatch):
    cfg = AletheiaConfig()
    cfg.market_data.observed_instruments = ['bitget:BTC/USDT:perp']
    monkeypatch.setattr(repo, 'list_universe_assets', lambda **kw: [{'symbol':f'A{i}/USDT','market_type':'perp'} for i in range(30)])
    monkeypatch.setattr(repo, 'list_all_open_positions', lambda: [{'symbol':'OPEN/USDT','market_type':'perp'}])
    workset = derivatives.comparison_workset(cfg)
    assert set(workset) == {'binance','okx','bitget'}
    assert all(len(symbols) == 32 and 'OPEN/USDT' in symbols and 'BTC/USDT' in symbols for symbols in workset.values())


def test_bitget_comparison_funding_keeps_settlement_groups():
    from extensions.exchanges.bitget.market_funding import read_comparison_funding
    calls = []
    class Exchange:
        async def fetch_funding_rates(self, symbols, *, params):
            calls.append((symbols, params))
            return {symbol: {"fundingRate": 0} for symbol in symbols}
    async def fetch(method, *args, **kwargs):
        return await method(*args, **kwargs)
    markets = {"BTC/USDT": {"symbol": "BTC/USDT:USDT", "settle": "USDT"},
               "ETH/USDC": {"symbol": "ETH/USDC:USDC", "settle": "USDC"}}
    result = asyncio.run(read_comparison_funding(Exchange(), markets, fetch))
    assert set(result) == {"BTC/USDT:USDT", "ETH/USDC:USDC"}
    assert calls == [
        (["ETH/USDC:USDC"], {"method": "publicMixGetV2MixMarketCurrentFundRate"}),
        (["BTC/USDT:USDT"], {"method": "publicMixGetV2MixMarketCurrentFundRate"}),
    ]


@pytest.mark.parametrize("venue", ["binance", "fixture"])
def test_history_comparison_reader_is_explicit_and_keeps_rotation(venue):
    from extensions.market_data import build_native_provider
    symbols = [f"S{i}/USDT" for i in range(9)]
    calls = []
    class Exchange:
        has = {"fetchOpenInterestHistory": True}
        markets = {s + ":USDT": dict(symbol=s + ":USDT", swap=True, linear=True,
                    active=True, settle="USDT", base=s.split("/")[0], quote="USDT")
                   for s in symbols}
        async def load_markets(self):
            return self.markets
        async def fetch_funding_rate(self, symbol):
            return None
        async def fetch_open_interest_history(self, symbol, timeframe, *, limit):
            assert timeframe == "5m" and limit == 1
            calls.append(symbol)
            return []
    provider = build_native_provider(venue, venue)
    provider._primary = provider._primary_perp = Exchange()
    for seconds in (0, 600):
        result = asyncio.run(provider.get_derivatives_comparison(
            symbols, owns_primary=False, acquired=datetime.fromtimestamp(seconds, timezone.utc)))
        assert len(result["coverage"]) == 9
    assert calls == ([s + ":USDT" for s in symbols[:8]] if venue == "binance" else [])


def test_comparison_setting_can_disable_collection_without_changing_legacy_targets(monkeypatch):
    from aletheia.server.routes import settings
    from pydantic import ValidationError
    cfg = AletheiaConfig()
    before = (cfg.market_data.ccxt_exchange, list(cfg.market_data.observed_instruments), list(cfg.market_data.observed_symbols))
    monkeypatch.setattr(settings, 'load_config', lambda: cfg)
    monkeypatch.setattr(settings, '_mutate_config_with_candidate_publication_permit', lambda change: change(cfg))
    monkeypatch.setattr('aletheia.files.jsonl.write_audit', lambda event: None)
    result = settings.update_market_data_config(settings.MarketDataConfigUpdate(derivatives_comparison_venues=[]))
    assert result['updated'] == {'derivatives_comparison_venues': []}
    assert derivatives.comparison_workset(cfg) == {}
    assert (cfg.market_data.ccxt_exchange, cfg.market_data.observed_instruments, cfg.market_data.observed_symbols) == before
    with pytest.raises(ValidationError):
        settings.MarketDataConfigUpdate(derivatives_comparison_venues=['hyperliquid'])


def test_comparison_okx_source_event_deduplicates_across_acquisition_times():
    raw = next(r for r in PUBLIC['results'] if r['venue']=='okx')
    first = parse_funding('BTC/USDT','okx',raw['fetch_funding_rate']).model_dump()
    first['derivatives_metadata_json'] = ccxt_metadata('BTC/USDT','okx',raw['market'],raw['fetch_funding_rate'],family='funding',acquired_at=first['acquired_at'])
    later = dict(first, acquired_at=(datetime.fromisoformat(first['acquired_at'])+timedelta(minutes=1)).isoformat(),
        observed_at=(datetime.fromisoformat(first['observed_at'])+timedelta(minutes=1)).isoformat())
    assert comparison_row(first)['id'] == comparison_row(later)['id']
