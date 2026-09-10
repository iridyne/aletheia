"""Frozen pre-convergence consumer outputs, not proof of provider unit semantics."""

from datetime import datetime, timezone
import json
from pathlib import Path

from aletheia.data.collectors import derivatives, hyperliquid
from aletheia.data import normalizer
from aletheia.data.context_builder import render_enriched_context_md
from aletheia.storage import repositories as repo

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def legacy_outputs(monkeypatch):
    for module in (derivatives, normalizer):
        monkeypatch.setattr(module, 'datetime', FrozenClock)
    monkeypatch.setattr(repo, 'open_interest_near', lambda *a, **kw: {'open_interest_usd': 1_000_000})
    stamp = NOW.isoformat()
    funding = derivatives._parse_funding('BTC/USDT', 'bitget', {
        'fundingRate': .0012, 'markPrice': 60000, 'timestamp': int(NOW.timestamp()*1000),
    }).model_dump()
    oi = derivatives._parse_oi('BTC/USDT', 'bitget', {
        'openInterestValue': 1_100_000, 'openInterestAmount': 20,
        'timestamp': int(NOW.timestamp()*1000),
    }, lsr=1.8, top_position_ratio=.7).model_dump()
    hl = hyperliquid._parse_funding('BTC/USDT', {'funding': '.0001', 'markPx': '60000'}, stamp).model_dump()
    hl_oi = hyperliquid._parse_oi('BTC/USDT', {'openInterest': '20', 'markPx': '60000'}, stamp).model_dump()
    # Already persisted input shapes; do not claim these are upstream API fixtures.
    predicted = {**hl, 'exchange': 'hyperliquid_predicted', 'funding_rate': .0002,
                 'funding_rate_annualized': 1.752, 'mark_price': None}
    aggregate_oi = {**oi, 'exchange': 'coinalyze', 'open_interest_usd': 9_000_000,
                    'open_interest_coin': None, 'long_short_ratio': None, 'top_trader_position_ratio': None}
    liquidation = {'symbol': 'BTC/USDT', 'exchange': 'coinalyze', 'side': 'long',
                   'quantity_usd': 250000, 'price': 0.0, 'event_time': stamp, 'observed_at': stamp}
    leverage = normalizer.compute_leverage_state('BTC/USDT', funding['funding_rate'],
        funding['funding_rate_annualized'], oi['open_interest_usd'], oi['oi_change_1h_pct'],
        oi['long_short_ratio'], liquidation['quantity_usd'])
    cross = {'BTC/USDT': {'hl_ann': hl['funding_rate_annualized'],
        'hl_predicted_ann': predicted['funding_rate_annualized'],
        'primary_ann': funding['funding_rate_annualized'],
        'hl_open_interest_usd': hl_oi['open_interest_usd'],
        'hl_oi_change_1h_pct': hl_oi['oi_change_1h_pct'], 'hl_observed_at': stamp}}
    return {'funding': funding, 'oi': oi, 'hyperliquid': hl, 'hyperliquid_oi': hl_oi,
        'predicted': predicted, 'aggregate_oi': aggregate_oi, 'liquidation': liquidation,
        'leverage': leverage.model_dump(), 'rendered': render_enriched_context_md(
            ['BTC/USDT'], {}, {'BTC/USDT': leverage}, None, xvenue_funding=cross)}


def test_existing_consumers_match_frozen_baseline(monkeypatch):
    expected = json.loads((Path(__file__).parent/'fixtures/derivatives_consumers_784b7d55.json').read_text())
    assert legacy_outputs(monkeypatch) == expected


def test_existing_persistence_keeps_exact_sources_and_populations(monkeypatch):
    data = legacy_outputs(monkeypatch)
    repo.insert_collector_snapshot_batch(
        funding=[data[k] for k in ('funding','hyperliquid','predicted')],
        open_interest=[data[k] for k in ('oi','hyperliquid_oi','aggregate_oi')],
        liquidations=[data['liquidation']],
    )
    for source, key in [('bitget','funding'),('hyperliquid','hyperliquid'),('hyperliquid_predicted','predicted')]:
        actual = repo.latest_funding_rate('BTC/USDT', exchange=source)
        assert {k: actual[k] for k in data[key]} == data[key]
    for source, key in [('bitget','oi'),('hyperliquid','hyperliquid_oi'),('coinalyze','aggregate_oi')]:
        actual = repo.latest_open_interest('BTC/USDT', exchange=source)
        assert {k: actual[k] for k in data[key]} == data[key]
    assert data['oi']['long_short_ratio'] != data['oi']['top_trader_position_ratio']
    assert repo.latest_funding_rate('BTC/USDT', exchange='okx') is None
