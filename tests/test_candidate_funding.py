"""#3086: exact T0 proof, scoped history cutover, unchanged MAE veto."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.data.derivatives_facts import instrument_from_market, normalize_funding
from aletheia.universe.candidate_funding import PERP_FUNDING_EVIDENCE_VERSION, proved_funding_interval
from aletheia.universe.outcome_economics import estimate_outcome_economics

T0 = datetime(2026, 9, 2, tzinfo=timezone.utc)
MARKET = dict(
    id="BTCUSDT",
    symbol="BTC/USDT:USDT",
    swap=True,
    linear=True,
    base="BTC",
    quote="USDT",
    settle="USDT",
    info={"fundInterval": "1"},
)


def proof(symbol="BTC/USDT", interval=3600):
    market = dict(MARKET, base=symbol.split("/")[0], id=symbol.replace("/", ""))
    f = normalize_funding(
        {"fundingRate": 0.001, "timestamp": int(T0.timestamp() * 1000)},
        instrument=instrument_from_market(symbol, market),
        provider="ccxt",
        venue="bitget",
        acquired_at=T0,
        interval_seconds=interval,
    )
    f.update(
        version=PERP_FUNDING_EVIDENCE_VERSION,
        interval_source="bitget.contracts.fundInterval",
        interval_acquired_at=T0.isoformat(),
    )
    return json.dumps(f)


@pytest.mark.parametrize("interval,cost", [(3600, 0.4), (28800, 0.05), (None, None)])
def test_proved_interval_controls_candidate_cost(interval, cost):
    row = dict(
        venue="bitget",
        symbol="BTC/USDT",
        entry_ts=T0.isoformat(),
        funding_rate_at_entry=0.001,
        funding_evidence_json=proof(interval=interval),
    )
    e = estimate_outcome_economics(
        entry_price=100,
        exit_price=101,
        direction="long",
        market_type="perp",
        hold_seconds=14400,
        entry_spread_bps=1,
        fallback_spread_bps=25,
        funding_rate_at_entry=0.001,
        funding_interval_seconds=proved_funding_interval(row),
        execution_fee_pct=0.06,
        slippage_bps=5,
        range_high=102,
        range_low=99,
    )
    if cost is None:
        assert e["estimated_funding_pct"] is None and e["net_return_pct"] is None
    else:
        assert e["estimated_funding_pct"] == pytest.approx(cost)
        assert e["net_return_pct"] == pytest.approx(1 - 0.12 - 0.11 - cost)


@pytest.mark.parametrize("change", ["venue", "symbol", "rate", "future", "missing_source"])
def test_unusable_or_wrong_instrument_proof_is_not_funding(change):
    row = dict(
        venue="bitget",
        symbol="BTC/USDT",
        entry_ts=T0.isoformat(),
        funding_rate_at_entry=0.001,
        funding_evidence_json=proof(),
    )
    f = json.loads(row["funding_evidence_json"])
    if change == "venue":
        f["venue"] = "okx"
    if change == "symbol":
        row["symbol"] = "ETH/USDT"
    if change == "rate":
        row["funding_rate_at_entry"] = 0.002
    if change == "future":
        f["source_observed_at"] = (T0 + timedelta(seconds=1)).isoformat()
    if change == "missing_source":
        f["source_observed_at"] = None
    row["funding_evidence_json"] = json.dumps(f)
    assert proved_funding_interval(row) is None


@pytest.mark.asyncio
async def test_bitget_feature_freezes_same_ticker_and_existing_contract_interval(monkeypatch):
    from aletheia.universe.features import _fetch_single_feature
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(repo, "get_latest_feature_snapshot", lambda *a: None)

    class Exchange:
        id = "bitget"
        markets = {"BTC/USDT:USDT": MARKET}

        async def fetch_ohlcv(self, *a, **kw):
            return [[int((now - timedelta(days=1)).timestamp() * 1000), 100, 101, 99, 100, 1000]]

        async def fetch_funding_rate(self, *a, **kw):
            pytest.fail("Bitget funding capture must not add a per-symbol request")

        async def fetch_open_interest(self, *a, **kw):
            return {}

        async def get_funding_observation(self, _symbol, *, ticker=None):
            from aletheia.data.derivatives_facts import (
                funding_interval_seconds,
                instrument_from_market,
                normalize_funding,
            )

            received_at = now
            raw = {
                "fundingRate": ".001",
                "timestamp": int(now.timestamp() * 1000),
            }
            evidence = normalize_funding(
                raw,
                instrument=instrument_from_market("BTC/USDT", MARKET),
                provider="ccxt",
                venue="bitget",
                acquired_at=received_at,
                interval_seconds=funding_interval_seconds({"interval": "1h"}),
            )
            return {
                "funding_rate": 0.001,
                "observed_at": now.isoformat(),
                "received_at": now.isoformat(),
                "evidence": evidence,
                "interval_source": "bitget.contracts.fundInterval",
                "interval_raw": "1h",
                "interval_acquired_at": now.isoformat(),
            }

        async def get_open_interest_observation(self, _symbol):
            return None

    from tests.helpers.ohlcv_test_helpers import observation_reader
    exchange = Exchange()
    feature = await _fetch_single_feature(
        exchange,
        dict(id="btc", symbol="BTC/USDT", venue="bitget", market_type="perp"),
        dict(bid=99, ask=101, timestamp=int(now.timestamp() * 1000), info={"fundingRate": ".001"}),
        now,
        lambda s: s + ":USDT",
        snapshot_buffer=[],
        ohlcv_reader=observation_reader(exchange),
    )
    assert feature["funding_rate"] == 0.001
    row = {
        **feature,
        "entry_ts": datetime.now(timezone.utc).isoformat(),
        "funding_rate_at_entry": feature["funding_rate"],
    }
    assert proved_funding_interval(row) == 3600
    assert json.loads(row["funding_evidence_json"])["interval_source"] == "bitget.contracts.fundInterval"


def test_scoped_cutover_keeps_spot_and_current_incomplete_denominator():
    from aletheia.storage import repositories as repo
    from aletheia.config.schema import AletheiaConfig, UniverseConfig
    from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
    from aletheia.universe.candidate_selection_shadow import (
        build_candidate_selection_shadow_bundle,
        load_candidate_selection_outcomes,
    )
    from aletheia.universe.economic_utility import build_economic_utility_cohort_index
    from tests.test_candidate_selection_utility import _feature

    population = [_feature(i, return_24h=5 + i) for i in range(20)]
    for i, f in enumerate(population[:3]):
        f.update(
            venue="bitget",
            market_type="perp",
            funding_rate=0.001,
            funding_evidence_json=proof(f["symbol"], None if i == 2 else 3600),
        )
    bundle = build_candidate_selection_shadow_bundle(
        population,
        scanner_run_id="funding-cutover",
        captured_at=T0.isoformat(),
        universe_config=UniverseConfig(max_candidates=3),
        historical_outcomes=(),
        selection_policy=candidate_selection_policy_identity(AletheiaConfig()),
        action_route_reasons={
            (f["feature_snapshot_id"], f["venue"], f["market_type"], f["symbol"]): None for f in population
        },
    )
    old_symbol = population[0]["symbol"]
    new_symbol = population[1]["symbol"]
    incomplete_symbol = population[2]["symbol"]
    for o in bundle.outcomes:
        if o.symbol == old_symbol:
            o.funding_evidence_json = None
        o.status = "settled"
        o.settled_at = (T0 + timedelta(hours=4)).isoformat()
        o.net_return_pct = 0.5
        o.mae_pct = -1
        o.min_tradable_edge_pct = 0.05
        o.estimated_fee_pct = 0.12
        o.estimated_spread_slippage_pct = 0.11
        o.estimated_funding_pct = 0
        o.cost_model_version = "v1"
        if o.symbol == incomplete_symbol:
            o.net_return_pct = None
            o.estimated_funding_pct = None
    repo.insert_universe_twin_shadow_bundle(bundle.batch, bundle.evidence, outcomes=bundle.outcomes, min_interval_sec=0)
    db = repo.get_db()
    before = [tuple(r) for r in db.execute("select * from filter_candidate_outcomes order by id")]
    policy = json.loads(bundle.batch.policy_json)["selection_policy"]
    diagnostics = {}
    loaded = load_candidate_selection_outcomes(selection_policy=policy, diagnostics_out=diagnostics)
    assert old_symbol not in {r["symbol"] for r in loaded}
    assert {new_symbol, incomplete_symbol} <= {r["symbol"] for r in loaded}
    assert any(r["market_type"] == "spot" for r in loaded)
    assert diagnostics["perp_funding_historical_incompatible"] > 0
    assert diagnostics["perp_funding_current_complete"] > 0
    assert diagnostics["perp_funding_current_incomplete"] > 0
    assert before == [tuple(r) for r in db.execute("select * from filter_candidate_outcomes order by id")]
    index = build_economic_utility_cohort_index(
        loaded, source_cutoff_at=(T0 + timedelta(hours=5)).isoformat(), cost_assumptions=loaded[0]["cost_assumptions"]
    )
    # The row with an unknown funding cost is excluded from the sample but stays in
    # the denominator: the perp cohort is not voided, its coverage drops.
    perp = [v for k, v in index.cohorts.items() if k[-1] == "perp"]
    assert perp and all(v.failure_reason is None for v in perp)
    assert any(v.coverage < 1.0 for v in perp)
    complete_rows = [r for r in loaded if r["symbol"] == new_symbol]
    healthy = build_economic_utility_cohort_index(
        complete_rows,
        source_cutoff_at=(T0 + timedelta(hours=5)).isoformat(),
        cost_assumptions=loaded[0]["cost_assumptions"],
    )
    assert healthy.cohorts and all(v.failure_reason is None for v in healthy.cohorts.values())
    harmed = build_economic_utility_cohort_index(
        [dict(r, net_return_pct=-20) for r in complete_rows],
        source_cutoff_at=(T0 + timedelta(hours=5)).isoformat(),
        cost_assumptions=loaded[0]["cost_assumptions"],
    )
    assert all(v.failure_reason == "tail_harm" for v in harmed.cohorts.values())
    return loaded


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [3600, None])
async def test_canonical_settler_defers_until_path_evidence_is_available(monkeypatch, interval):
    from aletheia.analytics.filter_evidence import build_candidate_outcomes
    from aletheia.storage import repositories as repo
    from aletheia.universe import outcome_recorder
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    rows = build_candidate_outcomes(
        outcome_candidate_key="funding-settler",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        scanner_family="candidate_selection_utility",
        regime_tag=None,
        entry_price_usd=100,
        entry_spread_bps=1,
        funding_rate_at_entry=0.001,
        entry_ts=T0.isoformat(),
        horizons=("4h",),
        funding_evidence_json=proof(interval=interval),
    )
    repo.insert_filter_candidate_outcomes_batch(rows)
    observation = dict(
        exit_price=101,
        price_quality_ok=True,
        price_source="near_due_ticker",
        range_high=None,
        range_low=None,
        settlement_lag_sec=1,
    )
    monkeypatch.setattr(
        repo,
        "candidate_selection_cost_assumptions",
        lambda key: dict(execution_fee_pct=0.06, slippage_bps=5, fallback_spread_bps=25),
    )
    monkeypatch.setattr(
        outcome_recorder,
        "local_market_observations",
        lambda rows, **kw: ({r["id"]: observation for r in rows}, [], {}),
    )

    assert await settle_due_filter_outcomes() == 0
    stored = dict(repo.get_db().execute("select * from filter_candidate_outcomes where id=?", (rows[0].id,)).fetchone())
    assert stored["mae_pct"] is None and stored["status"] == "pending"

    observation.update(
        price_source="horizon_ohlcv",
        range_high=102,
        range_low=99,
    )
    assert await settle_due_filter_outcomes() == 1
    stored = dict(repo.get_db().execute("select * from filter_candidate_outcomes where id=?", (rows[0].id,)).fetchone())
    assert stored["mae_pct"] == pytest.approx(-1.0) and stored["status"] == "settled"
    assert (
        stored["estimated_funding_pct"] == pytest.approx(0.4)
        if interval
        else stored["estimated_funding_pct"] is None
    )
    assert stored["funding_evidence_json"] == rows[0].funding_evidence_json
    assert await settle_due_filter_outcomes() == 0
