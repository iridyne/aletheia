"""Focused behavior tests for Opportunity filter evidence."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def test_directional_decision_registers_one_shared_outcome_per_horizon():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    key = "opportunity:filter-outcome-registration"
    common = dict(
        candidate_key=key,
        candidate_kind="opportunity",
        stage="paper_ranker",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="short",
        scanner_family="momentum",
        entry_price_usd=100.0,
        entry_spread_bps=6.0,
        funding_rate_at_entry=0.0005,
    )
    record_filter_decision(
        filter_name="direction_present", decision="passed",
        reason_code="direction_available", **common,
    )
    record_filter_decision(
        filter_name="ranker_confidence_gate", decision="rejected",
        reason_code="confidence_below", **common,
    )

    rows = get_db().execute(
        "SELECT * FROM filter_candidate_outcomes WHERE outcome_candidate_key=?",
        (key,),
    ).fetchall()
    assert {row["horizon"] for row in rows} == {"15m", "1h", "4h", "24h"}
    assert {row["market_type"] for row in rows} == {"perp"}
    assert {row["direction"] for row in rows} == {"short"}


def test_blank_venue_keeps_filter_decision_report_only():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    decision_id = record_filter_decision(
        candidate_key="opportunity:blank-filter-venue",
        candidate_kind="opportunity",
        filter_name="direction_present",
        stage="paper_ranker",
        decision="passed",
        reason_code="direction_available",
        symbol="BTC/USDT",
        venue=" \t",
        market_type="perp",
        direction="long",
        entry_price_usd=100.0,
    )

    row = get_db().execute(
        "SELECT venue, outcome_candidate_key FROM filter_decisions WHERE id=?",
        (decision_id,),
    ).fetchone()
    assert dict(row) == {"venue": None, "outcome_candidate_key": None}
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key='opportunity:blank-filter-venue'"
    ).fetchone()[0] == 0


def test_risk_filter_uses_exact_universe_asset_venue(monkeypatch):
    from aletheia.analytics import filter_evidence

    captured = {}
    monkeypatch.setattr(
        filter_evidence.repo,
        "get_opportunity",
        lambda _opportunity_id: {"universe_member_id": "member-exact"},
    )
    monkeypatch.setattr(
        filter_evidence.repo,
        "get_universe_member",
        lambda _member_id: {
            "asset_id": "asset-exact",
            "symbol": "BTC/USDT",
            "market_type": "perp",
        },
    )
    monkeypatch.setattr(
        filter_evidence.repo,
        "get_universe_asset",
        lambda _asset_id: {
            "venue": " okx ",
            "symbol": "BTC/USDT",
            "market_type": "perp",
        },
    )
    monkeypatch.setattr(
        filter_evidence,
        "record_filter_decision",
        lambda **kwargs: captured.update(kwargs) or "risk-evidence",
    )
    proposal = SimpleNamespace(
        id="proposal-exact",
        opportunity_id="opportunity-exact",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        execution_mode="okx_demo",
        price=100.0,
    )

    assert filter_evidence.record_risk_filter_decision(
        proposal,
        risk_decision_id="risk-exact",
        approved=True,
        violations=[],
    ) == "risk-evidence"
    assert captured["venue"] == "okx"


def test_filter_outcome_settlement_uses_net_economics(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome, ShadowTrade
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    now = datetime.now(timezone.utc)
    entry = now - timedelta(hours=25)
    outcome = FilterCandidateOutcome(
        outcome_candidate_key="opportunity:settle-filter-net",
        venue="okx",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        scanner_family="momentum",
        horizon="24h",
        entry_price_usd=100.0,
        entry_spread_bps=10.0,
        entry_ts=entry.isoformat(),
        due_at=(entry + timedelta(hours=24)).isoformat(),
    )
    repo.insert_filter_candidate_outcomes_batch([outcome])
    shadow = ShadowTrade(
        opportunity_id="settle-filter-net",
        batch_id="batch-net",
        symbol="BTC/USDT",
        venue="okx",
        market_type="spot",
        direction="long",
        entry_price=100.0,
        created_at=entry.isoformat(),
        outcome_candidate_key=outcome.outcome_candidate_key,
    )
    repo.insert_shadow_trade(shadow)

    async def observations(rows, *, now):
        return {
            outcome.id: {
                "exit_price": 102.0,
                "range_high": 103.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": True,
                "settlement_lag_sec": 30.0,
            }
        }

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=1)) == 1
    row = get_db().execute(
        "SELECT * FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
    ).fetchone()
    assert row["status"] == "settled"
    assert row["gross_return_pct"] == pytest.approx(2.0)
    assert row["net_return_pct"] == pytest.approx(1.68)
    assert row["mfe_pct"] == pytest.approx(3.0)
    assert row["mae_pct"] == pytest.approx(-1.0)
    shadow_row = get_db().execute(
        "SELECT * FROM shadow_trades WHERE id=?", (shadow.id,)
    ).fetchone()
    assert shadow_row["pnl_pct"] == pytest.approx(2.0)
    assert shadow_row["net_pnl_pct"] == pytest.approx(1.68)
    assert shadow_row["resolved_at"]


@pytest.mark.parametrize("lab_unavailable", [False, True])
def test_candidate_settlement_uses_capture_time_costs_without_learning_tables(monkeypatch, lab_unavailable):
    from aletheia.storage import repositories as repo
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    from aletheia.observation_lab.db import get_lab_db

    get_lab_db()
    repo.get_db().execute("DROP TABLE IF EXISTS learning_exposures")
    if lab_unavailable:
        def unavailable(**kwargs):
            raise FileNotFoundError("lab priority unavailable")
        monkeypatch.setattr(repo, "list_observation_filter_outcome_priority_keys", unavailable)
    outcome = _due_filter_outcome("candidate-selection-utility:v1:frozen-costs:long")
    outcome.scanner_family = "candidate_selection_utility"
    repo.insert_filter_candidate_outcomes_batch([outcome])
    monkeypatch.setattr(
        repo,
        "candidate_selection_cost_assumptions",
        lambda _key: {
            "execution_fee_pct": 0.10,
            "slippage_bps": 0.0,
            "fallback_spread_bps": 99.0,
        },
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _settling_observations(),
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=1)) == 1
    row = repo.get_db().execute(
        "SELECT net_return_pct FROM filter_candidate_outcomes WHERE id=?",
        (outcome.id,),
    ).fetchone()
    # Entry spread is observed (10 bps), so fallback is irrelevant: 2% gross,
    # 0.20% round-trip fee, and 0.10% spread cost.
    assert row["net_return_pct"] == pytest.approx(1.70)


def _due_filter_outcome(key, *, horizon="24h", hours_ago=25):
    """A canonical outcome whose 24h horizon is already past due."""
    from aletheia.storage.models import FilterCandidateOutcome

    entry = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return FilterCandidateOutcome(
        outcome_candidate_key=key,
        venue="okx",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        scanner_family="momentum",
        horizon=horizon,
        entry_price_usd=100.0,
        entry_spread_bps=10.0,
        entry_ts=entry.isoformat(),
        due_at=(entry + timedelta(hours=24)).isoformat(),
    )


def _settling_observations():
    async def observations(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 102.0,
                "range_high": 103.0,
                "range_low": 99.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": True,
                "settlement_lag_sec": 30.0,
            }
            for row in rows
        }

    return observations


def test_slow_priority_prologue_still_settles_due_rows(monkeypatch):
    """A prologue that overruns its slice must cost ORDER, never throughput.

    This is the defect that stopped canonical settlement for hours: expanding the
    open-experiment keys grew past the entire cycle budget, so the settlement loop
    never ran an iteration, the function returned 0 without raising, and the only
    log line fired exclusively when settled > 0.
    """
    import time

    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe import filter_outcome_recorder as fr

    outcome = _due_filter_outcome("opportunity:slow-prologue")
    repo.insert_filter_candidate_outcomes_batch([outcome])

    deadlines = []

    def slow_priority(before_ts, keys, limit, deadline=None):
        deadlines.append(deadline)
        time.sleep(0.45)  # > 0.30 * 1.0s slice, < the 1.0s cycle budget
        return [], True

    monkeypatch.setattr(
        fr.repo, "list_pending_filter_candidate_outcomes_for_keys", slow_priority
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _settling_observations(),
    )

    settled = asyncio.run(fr.settle_due_filter_outcomes(max_rows=1, time_budget_sec=1.0))

    assert settled == 1, "an overrunning prologue must not zero out settlement"
    assert deadlines and deadlines[0] is not None, "prologue must be time-boxed"
    row = get_db().execute(
        "SELECT status FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
    ).fetchone()
    assert row["status"] == "settled"


def test_zero_throughput_cycle_with_work_outstanding_is_logged(caplog):
    """A cycle that settles nothing while rows are due must be audible."""
    import logging

    from aletheia.storage import repositories as repo
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    repo.insert_filter_candidate_outcomes_batch(
        [_due_filter_outcome("opportunity:zero-throughput")]
    )

    with caplog.at_level(
        logging.WARNING, logger="aletheia.universe.filter_outcome_recorder"
    ):
        assert asyncio.run(
            settle_due_filter_outcomes(max_rows=500, time_budget_sec=0.0)
        ) == 0

    assert any("not draining" in r.getMessage() for r in caplog.records)


def test_caught_up_queue_does_not_warn(monkeypatch, caplog):
    """The zero-throughput warning must stay silent when nothing is due.

    Otherwise it fires every cycle once the backlog is actually drained, and an
    always-on warning is indistinguishable from no warning at all.
    """
    import logging

    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _settling_observations(),
    )

    with caplog.at_level(
        logging.WARNING, logger="aletheia.universe.filter_outcome_recorder"
    ):
        assert asyncio.run(
            settle_due_filter_outcomes(max_rows=500, time_budget_sec=5.0)
        ) == 0

    assert not [r for r in caplog.records if "not draining" in r.getMessage()]


def _aligned_1h_window(*, hours_ago=0):
    """An aligned, already-past one-hour window. Exactly 60 expected 1m bars."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    due_ms = (now_ms // 60_000) * 60_000 - 60_000 - hours_ago * 3_600_000
    return due_ms - 3_600_000, due_ms


def _hourly_filter_outcome(key, entry_ms, due_ms, *, venue="okx"):
    from aletheia.storage.models import FilterCandidateOutcome

    return FilterCandidateOutcome(
        outcome_candidate_key=key,
        venue=venue,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        scanner_family="momentum",
        horizon="1h",
        entry_price_usd=100.0,
        entry_spread_bps=10.0,
        entry_ts=datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc).isoformat(),
        due_at=datetime.fromtimestamp(due_ms / 1000, tz=timezone.utc).isoformat(),
    )


def _seed_local_1m_spot(entry_ms, count=60, *, exchange="okx"):
    """A complete local 1m window whose extremes match the network fixture."""
    from aletheia.data.providers import Candle
    from aletheia.storage.repositories import upsert_ohlcv_candles

    candles = [
        Candle(entry_ms + i * 60_000, 100, 101, 99, 100.5, 10) for i in range(count)
    ]
    candles[-1] = Candle(entry_ms + (count - 1) * 60_000, 100.5, 103, 100, 102, 12)
    upsert_ohlcv_candles(
        "BTC/USDT", "1m", candles, exchange=exchange, market_type="spot"
    )


def test_locally_answerable_rows_settle_without_any_exchange_request(monkeypatch):
    """Phase A must settle off the persisted window and hold no exchange resource.

    This is where the throughput comes from. Before the split, settlement cost one
    live request per row, so the row cap and the external load were the same
    number and the only way to drain faster was to fetch harder.

    The economics asserted here are byte-for-byte the ones
    test_filter_outcome_settlement_uses_net_economics gets from the network path.
    That equality is the point: the two phases share one apply() and one window
    reducer, so they cannot drift in cost model, guards, or shadow linkage.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    entry_ms, due_ms = _aligned_1h_window()
    _seed_local_1m_spot(entry_ms)
    outcome = _hourly_filter_outcome("opportunity:local-phase-a", entry_ms, due_ms)
    repo.insert_filter_candidate_outcomes_batch([outcome])

    async def refuse(rows, *, now):
        raise AssertionError("a complete local window must not reach the exchange")

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations", refuse
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8, max_fetches=0)) == 1
    row = get_db().execute(
        "SELECT * FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
    ).fetchone()
    assert row["status"] == "settled"
    assert row["settlement_price_source"] == "horizon_ohlcv_local"
    assert row["gross_return_pct"] == pytest.approx(2.0)
    assert row["net_return_pct"] == pytest.approx(1.68)
    assert row["mfe_pct"] == pytest.approx(3.0)
    assert row["mae_pct"] == pytest.approx(-1.0)


def test_rows_past_the_fetch_ceiling_stay_pending_and_are_never_failed(monkeypatch):
    """Budget exhaustion is not a measurement failure.

    Both settle loops treat "no observation" as terminal and mark the row
    ``failed``, which is correct for a row the exchange answered badly and wrong
    for a row that was never asked. A deferred row must come back next cycle, so
    the ceiling has to stop it before apply() ever sees it.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    # No local window seeded, so every row misses and phase B owns all of them.
    entry_ms, due_ms = _aligned_1h_window()
    outcome = _hourly_filter_outcome("opportunity:over-budget", entry_ms, due_ms)
    repo.insert_filter_candidate_outcomes_batch([outcome])

    calls = []

    async def observations(rows, *, now):
        calls.append(len(rows))
        return {}

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        observations,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8, max_fetches=0)) == 0
    assert not calls, "a zero fetch ceiling must issue no exchange request at all"
    row = get_db().execute(
        "SELECT status FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
    ).fetchone()
    assert row["status"] == "pending", (
        "a row deferred for lack of budget must be retried, not written off"
    )


class _PersistingProvider:
    """A provider that persists what it returns, exactly as ccxt_provider does.

    ``get_ohlcv`` writes every bar to market_ohlcv unconditionally (only the
    in-process cache is gated on ``since``), which is what makes one request able
    to answer more than one row. A fake that returns bars without persisting them
    would test a system that does not exist.
    """

    def __init__(self, entry_ms, bars=300):
        from aletheia.data.providers import Candle

        self.calls = []
        self._candles = [
            Candle(entry_ms + i * 60_000, 100, 103, 99, 102, 10) for i in range(bars)
        ]

    async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
        from aletheia.storage.repositories import upsert_ohlcv_candles

        self.calls.append((str(instrument), timeframe, limit, since))
        upsert_ohlcv_candles(
            instrument.symbol,
            timeframe,
            list(self._candles),
            exchange=instrument.exchange,
            market_type=instrument.market_type,
        )
        return list(self._candles)

    async def get_ticker(self, instrument):
        raise AssertionError("horizon OHLCV should avoid current-ticker settlement")

    async def close(self):
        pass


def test_one_request_settles_every_row_it_brought_bars_for(monkeypatch):
    """The fetch budget is spent per instrument, not per row.

    Rows on one instrument ask the same endpoint for the same series and differ
    only in which slice they need, and _FETCH_PLAN asks for far more bars than any
    one window uses. So the oldest row's response already contains the later rows'
    windows, and those settle off disk for free. Spending the budget per row
    instead would buy one row per request and leave the same bars unused.

    Both rows here settle under a ceiling of ONE request. That is the whole
    capacity argument: throughput rises while external load does not.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    # The oldest row drives the fetch, so the sibling's window has to be both
    # already past AND inside the 300 one-minute bars that fetch brings back.
    entry_ms, due_ms = _aligned_1h_window(hours_ago=4)
    sib_entry, sib_due = _aligned_1h_window(hours_ago=1)
    assert sib_due <= entry_ms + 300 * 60_000, "sibling must fall inside the span"
    repo.insert_filter_candidate_outcomes_batch([
        _hourly_filter_outcome("opportunity:group-oldest", entry_ms, due_ms),
        _hourly_filter_outcome("opportunity:group-sibling", sib_entry, sib_due),
    ])
    provider = _PersistingProvider(entry_ms)
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: provider,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8, max_fetches=1)) == 2
    assert len(provider.calls) == 1, "one instrument must cost one request"
    assert provider.calls[0][3] is not None, "the oldest member drives the fetch"
    sources = {
        row["outcome_candidate_key"]: row["settlement_price_source"]
        for row in get_db().execute(
            "SELECT outcome_candidate_key, settlement_price_source, status "
            "FROM filter_candidate_outcomes"
        ).fetchall()
    }
    assert sources["opportunity:group-oldest"] == "horizon_ohlcv"
    assert sources["opportunity:group-sibling"] == "horizon_ohlcv_local", (
        "the sibling must be harvested from the bars the request persisted"
    )


class _SinceHonouringProvider:
    """Persists 300 bars starting at ``since``, and no further, like the real one.

    _FETCH_PLAN asks for 300 one-minute bars from the row's own entry and OKX caps
    history-candles at 300, so a request issued for an old row physically cannot
    reach a window that sits hours later. That is the case the harvest cannot
    answer, and it has to be a fetch rather than a shrug.
    """

    def __init__(self, span_bars=300):
        self.calls = []
        self._span = span_bars

    async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
        from aletheia.data.providers import Candle
        from aletheia.storage.repositories import upsert_ohlcv_candles

        self.calls.append((str(instrument), timeframe, limit, since))
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start = int(since)
        candles = [
            Candle(start + i * 60_000, 100, 103, 99, 102, 10)
            for i in range(self._span)
            if start + i * 60_000 < now_ms
        ]
        upsert_ohlcv_candles(
            instrument.symbol,
            timeframe,
            candles,
            exchange=instrument.exchange,
            market_type=instrument.market_type,
        )
        return candles

    async def close(self):
        return None


def test_a_sibling_the_fetched_span_missed_is_fetched_not_starved(monkeypatch):
    """Grouping defers a sibling; it must never abandon one.

    Collapsing an instrument's rows onto one representative is a bet that the
    representative's response covers the siblings. When the bet loses — the
    sibling's window falls outside the 300 bars that came back — the sibling has to
    be fetched on its own. Grouping exists to stretch a SCARCE request budget, so
    it may not leave a row unmeasured while budget is still available.

    Without the round loop this settled one row per cycle per instrument no matter
    how much budget was free, which is a throughput regression disguised as
    thrift: the queue would have kept growing while the ceiling went unspent.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    rep_entry, rep_due = _aligned_1h_window(hours_ago=10)
    sib_entry, sib_due = _aligned_1h_window(hours_ago=1)
    assert sib_due > rep_entry + 300 * 60_000, "sibling must fall OUTSIDE the span"
    repo.insert_filter_candidate_outcomes_batch([
        _hourly_filter_outcome("opportunity:span-oldest", rep_entry, rep_due),
        _hourly_filter_outcome("opportunity:span-beyond", sib_entry, sib_due),
    ])
    provider = _SinceHonouringProvider()
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: provider,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8)) == 2
    assert len(provider.calls) == 2, (
        "the uncovered sibling must cost its own request rather than wait a cycle"
    )
    rows = {
        row["outcome_candidate_key"]: (row["status"], row["settlement_price_source"])
        for row in get_db().execute(
            "SELECT outcome_candidate_key, status, settlement_price_source "
            "FROM filter_candidate_outcomes"
        ).fetchall()
    }
    assert rows["opportunity:span-oldest"] == ("settled", "horizon_ohlcv")
    assert rows["opportunity:span-beyond"] == ("settled", "horizon_ohlcv")


def test_an_uncovered_sibling_still_defers_when_the_budget_is_spent(monkeypatch):
    """The round loop must not become a way around the request ceiling.

    Same two rows as above, but only one request is affordable. The sibling the
    span missed then stays 'pending' — deferred, not failed — because budget
    exhaustion is not a measurement failure.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    rep_entry, rep_due = _aligned_1h_window(hours_ago=10)
    sib_entry, sib_due = _aligned_1h_window(hours_ago=1)
    repo.insert_filter_candidate_outcomes_batch([
        _hourly_filter_outcome("opportunity:capped-oldest", rep_entry, rep_due),
        _hourly_filter_outcome("opportunity:capped-beyond", sib_entry, sib_due),
    ])
    provider = _SinceHonouringProvider()
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: provider,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8, max_fetches=1)) == 1
    assert len(provider.calls) == 1
    statuses = {
        row["outcome_candidate_key"]: row["status"]
        for row in get_db().execute(
            "SELECT outcome_candidate_key, status FROM filter_candidate_outcomes"
        ).fetchall()
    }
    assert statuses["opportunity:capped-oldest"] == "settled"
    assert statuses["opportunity:capped-beyond"] == "pending"


def test_the_fetch_ceiling_holds_across_every_round(monkeypatch):
    """The request ceiling is the load-safety property, so it must bound the TOTAL.

    Rounds exist so a sibling the fetched span missed is not abandoned, but that
    must not turn into a second helping of budget. Here six instruments each have
    two rows whose windows cannot share a response, so every row wants its own
    request and the harvest can never answer one. Under a ceiling of five, exactly
    five requests may leave the process no matter how many rounds run.

    This is what keeps 400 requests per cycle a real 0.67/sec rather than a
    per-round figure multiplied by however many rounds a bad cycle needs.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    rep_entry, rep_due = _aligned_1h_window(hours_ago=10)
    sib_entry, sib_due = _aligned_1h_window(hours_ago=1)
    assert sib_due > rep_entry + 300 * 60_000, "the harvest must not be able to help"
    rows = []
    for n in range(6):
        rows.append(
            _hourly_filter_outcome(f"opportunity:cap-{n}-old", rep_entry, rep_due,
                                   venue=f"venue{n}")
        )
        rows.append(
            _hourly_filter_outcome(f"opportunity:cap-{n}-new", sib_entry, sib_due,
                                   venue=f"venue{n}")
        )
    repo.insert_filter_candidate_outcomes_batch(rows)
    provider = _SinceHonouringProvider()
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: provider,
    )

    settled = asyncio.run(settle_due_filter_outcomes(max_rows=64, max_fetches=5))
    assert len(provider.calls) == 5, (
        f"the ceiling must bound total requests across rounds, got "
        f"{len(provider.calls)}"
    )
    assert settled == 5
    pending = get_db().execute(
        "SELECT COUNT(*) AS n FROM filter_candidate_outcomes WHERE status='pending'"
    ).fetchone()["n"]
    assert pending == 7, "the seven unaffordable rows must be deferred, not failed"


def test_each_distinct_instrument_costs_one_unit_of_the_fetch_budget(monkeypatch):
    """The ceiling must bite mid-population, not only at zero.

    Two rows on different instruments cannot share a response, so a ceiling of one
    admits one of them and defers the other to the next cycle. Grouping must not
    quietly turn the request cap into a per-instrument-free-for-all.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    entry_ms, due_ms = _aligned_1h_window()
    repo.insert_filter_candidate_outcomes_batch([
        _hourly_filter_outcome("opportunity:venue-a", entry_ms, due_ms, venue="okx"),
        _hourly_filter_outcome(
            "opportunity:venue-b", entry_ms, due_ms, venue="binance"
        ),
    ])
    provider = _PersistingProvider(entry_ms)
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: provider,
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8, max_fetches=1)) == 1
    assert len(provider.calls) == 1
    statuses = sorted(
        row["status"]
        for row in get_db().execute(
            "SELECT status FROM filter_candidate_outcomes"
        ).fetchall()
    )
    assert statuses == ["pending", "settled"]


def test_pending_for_keys_is_bounded_by_limit_and_deadline():
    """Cost must scale with `limit`, not with the number of open-experiment keys."""
    import time

    from aletheia.storage import repositories as repo

    learning_keys = set()
    rows = []
    for i in range(5):
        base = f"opportunity:bounded-{i}"
        learning_keys.add(f"{base}:24h")
        rows.append(_due_filter_outcome(base, hours_ago=30 + i))
    repo.insert_filter_candidate_outcomes_batch(rows)

    now_iso = datetime.now(timezone.utc).isoformat()
    selected, truncated = repo.list_pending_filter_candidate_outcomes_for_keys(
        now_iso, learning_keys, limit=2,
    )
    assert truncated is False
    # Later i means an earlier entry_ts, hence an earlier due_at: bounded-4 is the
    # most overdue and must be settled first.
    assert [r["outcome_candidate_key"] for r in selected] == [
        "opportunity:bounded-4",
        "opportunity:bounded-3",
    ]

    expired, expired_truncated = repo.list_pending_filter_candidate_outcomes_for_keys(
        now_iso, learning_keys, limit=5, deadline=time.monotonic() - 1.0,
    )
    assert expired == []
    assert expired_truncated is True


def test_rejected_filter_stats_count_avoided_losses_and_missed_alpha():
    from aletheia.analytics.filter_evidence import (
        filter_feedback_stats,
        record_filter_decision,
    )
    from aletheia.storage.sqlite import get_db

    common = {
        "candidate_kind": "opportunity",
        "filter_name": "ranker_confidence_gate",
        "stage": "paper_ranker",
        "reason_code": "confidence_below",
        "symbol": "BTC/USDT",
        "venue": "okx",
        "market_type": "spot",
        "direction": "long",
        "scanner_family": "momentum",
        "entry_price_usd": 100.0,
        "parameters": {"minimum": "medium"},
    }
    for suffix, decision, net in (
        ("pass", "passed", 0.4),
        ("avoided", "rejected", -0.6),
        ("missed", "rejected", 0.8),
    ):
        key = f"opportunity:confidence-{suffix}"
        record_filter_decision(candidate_key=key, decision=decision, **common)
        get_db().execute(
            "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, "
            "directional_return_pct=?, exit_price_usd=?, estimated_fee_pct=0.1, "
            "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0, "
            "net_return_pct=?, min_tradable_edge_pct=0.05, "
            "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
            "cost_model_version='v1' "
            "WHERE outcome_candidate_key=?",
            (net + 0.2, 100.0 + net + 0.2, net, key),
        )
    for suffix, edge in (
        ("missing", None), ("negative", -0.01), ("infinite", float("inf")),
        ("malformed", "malformed"), ("blob", b"0.05"),
    ):
        key = f"opportunity:confidence-{suffix}"
        record_filter_decision(candidate_key=key, decision="rejected", **common)
        get_db().execute(
            "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, "
            "directional_return_pct=1.0, exit_price_usd=101.0, estimated_fee_pct=0.1, "
            "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0, "
            "net_return_pct=0.8, min_tradable_edge_pct=?, "
            "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
            "cost_model_version='v1' "
            "WHERE outcome_candidate_key=?",
            (edge, key),
        )
    max_edge_key = "opportunity:confidence-max-finite"
    record_filter_decision(candidate_key=max_edge_key, decision="rejected", **common)
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, "
        "directional_return_pct=0.2, exit_price_usd=100.2, estimated_fee_pct=0.1, "
        "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0, "
        "net_return_pct=0, min_tradable_edge_pct=?, "
        "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
        "cost_model_version='v1' "
        "WHERE outcome_candidate_key=?",
        (float.fromhex("0x1.fffffffffffffp+1023"), max_edge_key),
    )
    for suffix, cost_model in (
        ("missing-cost-model", None), ("blank-cost-model", " "),
        ("tab-cost-model", "\t"),
        ("unicode-space-cost-model", "\N{NO-BREAK SPACE}"),
        ("blob-cost-model", b"v1"),
    ):
        key = f"opportunity:confidence-{suffix}"
        record_filter_decision(candidate_key=key, decision="rejected", **common)
        get_db().execute(
            "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, "
            "directional_return_pct=1.0, exit_price_usd=101.0, estimated_fee_pct=0.1, "
            "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0, "
            "net_return_pct=0.8, min_tradable_edge_pct=0.05, "
            "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
            "cost_model_version=? "
            "WHERE outcome_candidate_key=?",
            (cost_model, key),
        )
    for suffix, net in (("malformed-net", "malformed"), ("blob-net", b"0.8")):
        key = f"opportunity:confidence-{suffix}"
        record_filter_decision(candidate_key=key, decision="rejected", **common)
        get_db().execute(
            "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, "
            "directional_return_pct=1.0, exit_price_usd=101.0, estimated_fee_pct=0.1, "
            "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0, "
            "net_return_pct=?, min_tradable_edge_pct=0.05, "
            "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
            "cost_model_version='v1' "
            "WHERE outcome_candidate_key=?",
            (net, key),
        )
    get_db().commit()

    rejected = next(
        row for row in filter_feedback_stats(
            as_of_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        )
        if row["filter_name"] == "ranker_confidence_gate"
        and row["decision"] == "rejected"
        and row["horizon"] == "4h"
        and row["cost_model_version"] == "v1"
    )
    assert rejected["blocked_avoided_loss_count"] == 1
    assert rejected["blocked_missed_alpha_count"] == 1
    assert rejected["blocked_net_sum"] == pytest.approx(0.2)
    assert rejected["n_total"] == 10
    assert rejected["n_net"] == 3
    assert rejected["outcome_coverage"] == 0.3


def test_filter_snapshot_drops_sensitive_keys_and_truncates_text():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    decision_id = record_filter_decision(
        candidate_key="opportunity:sanitized-filter",
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="paper_ranker",
        decision="rejected",
        reason_code="confidence_below",
        symbol="ETH/USDT",
        inputs={
            "confidence": "speculative",
            "api_key": "must-not-persist",
            "raw_context": "must-not-persist",
            "note": "x" * 700,
        },
    )
    row = get_db().execute(
        "SELECT input_snapshot FROM filter_decisions WHERE id=?", (decision_id,)
    ).fetchone()
    payload = json.loads(row["input_snapshot"])
    assert payload["confidence"] == "speculative"
    assert "api_key" not in payload
    assert "raw_context" not in payload
    assert len(payload["note"]) == 500


def test_same_named_filter_gates_remain_distinct_across_stages():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    common = dict(
        candidate_key="pi_decision:stage-identity",
        candidate_kind="pi_decision",
        filter_name="direction_bias_brake",
        decision="passed",
        reason_code="direction_not_overheated",
        symbol="BTC/USDT",
        parameters={"max_share": 0.95},
    )
    first_id = record_filter_decision(stage="system_operator", **common)
    second_id = record_filter_decision(stage="proposal_submission", **common)

    rows = get_db().execute(
        "SELECT id, stage FROM filter_decisions "
        "WHERE candidate_key='pi_decision:stage-identity' ORDER BY stage"
    ).fetchall()
    assert first_id != second_id
    assert [row["stage"] for row in rows] == [
        "proposal_submission",
        "system_operator",
    ]


def test_filter_decision_batch_is_complete_counted_and_idempotent():
    from aletheia.analytics.filter_evidence import (
        decode_filter_decision_batch,
        record_filter_decision_batch,
    )
    from aletheia.storage.sqlite import get_db

    facts = [
        {
            "candidate_key": "asset:btc-spot",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "decision": "passed",
            "reason_code": "ticker_available",
        },
        {
            "candidate_key": "asset:eth-perp",
            "symbol": "ETH/USDT",
            "market_type": "perp",
            "decision": "rejected",
            "reason_code": "ticker_unavailable",
        },
        {
            "candidate_key": "asset:missing",
            "symbol": "",
            "market_type": "spot",
            "decision": "not_applicable",
            "reason_code": "identity_missing",
        },
    ]
    kwargs = dict(
        run_id="scanner-run-batch",
        candidate_kind="universe_asset",
        filter_name="ticker_availability",
        stage="ticker_prefilter",
        candidate_facts=facts,
        parameters={"venue": "okx"},
    )

    first = record_filter_decision_batch(**kwargs)
    second = record_filter_decision_batch(**kwargs)
    rows = get_db().execute(
        "SELECT * FROM filter_decision_batches WHERE run_id=?",
        (kwargs["run_id"],),
    ).fetchall()

    assert first == second
    assert len(rows) == 1
    assert rows[0]["candidate_count"] == 3
    assert rows[0]["passed_count"] == 1
    assert rows[0]["rejected_count"] == 1
    assert rows[0]["not_applicable_count"] == 1
    assert rows[0]["error_count"] == 0
    assert decode_filter_decision_batch(rows[0]["candidate_facts"]) == facts

    other_stage = record_filter_decision_batch(
        **{**kwargs, "stage": "secondary_ticker_prefilter"}
    )
    assert other_stage != first
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_decision_batches WHERE run_id=?",
        (kwargs["run_id"],),
    ).fetchone()[0] == 2


def test_filter_decision_batch_cadence_bounds_unchanged_heavy_snapshots():
    from aletheia.analytics.filter_evidence import record_filter_decision_batch
    from aletheia.storage.sqlite import get_db

    common = dict(
        candidate_kind="universe_asset",
        filter_name="ticker_availability",
        stage="ticker_prefilter",
        candidate_facts=[{"candidate_key": "asset:btc", "decision": "passed"}],
        min_interval_sec=55 * 60,
    )
    first = record_filter_decision_batch(
        run_id="bounded-1",
        evaluated_at="2026-08-06T00:00:00+00:00",
        parameters={"venue": "okx"},
        **common,
    )
    suppressed = record_filter_decision_batch(
        run_id="bounded-2",
        evaluated_at="2026-08-06T00:10:00+00:00",
        parameters={"venue": "okx"},
        **common,
    )
    changed = record_filter_decision_batch(
        run_id="bounded-3",
        evaluated_at="2026-08-06T00:20:00+00:00",
        parameters={"venue": "bitget"},
        **common,
    )
    next_hour = record_filter_decision_batch(
        run_id="bounded-4",
        evaluated_at="2026-08-06T01:00:00+00:00",
        parameters={"venue": "okx"},
        **common,
    )

    assert suppressed == first
    assert changed != first
    assert next_hour != first
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_decision_batches "
        "WHERE filter_name='ticker_availability'"
    ).fetchone()[0] == 3


def test_candidate_outcomes_can_select_one_declared_learning_horizon():
    from aletheia.analytics.filter_evidence import build_candidate_outcomes

    common = dict(
        outcome_candidate_key="universe:one-horizon",
        venue="okx",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        regime_tag=None,
        entry_price_usd=100.0,
        entry_spread_bps=1.0,
        funding_rate_at_entry=0.0,
        entry_ts="2026-08-06T00:00:00+00:00",
    )

    outcomes = build_candidate_outcomes(**common)
    assert {row.horizon for row in outcomes} == {"15m", "1h", "4h", "24h"}
    assert {row.analysis_notional_quote for row in outcomes} == {1.0}
    targeted = build_candidate_outcomes(**common, horizons=("4h",))
    assert [row.horizon for row in targeted] == ["4h"]


def test_filter_decision_batch_compacts_repeated_candidate_keys():
    from aletheia.analytics.filter_evidence import (
        decode_filter_decision_batch,
        record_filter_decision_batch,
    )
    from aletheia.storage.sqlite import get_db

    facts = [
        {
            "candidate_key": f"asset:{index}",
            "symbol": f"ASSET{index}/USDT",
            "market_type": "spot",
            "decision": "passed",
            "reason_code": "ticker_available",
        }
        for index in range(100)
    ]
    facts[0]["optional_value"] = None
    facts[1]["inputs"] = {"quote_volume_24h": 10_000_000.0}
    record_filter_decision_batch(
        run_id="scanner-run-compact",
        candidate_kind="universe_asset",
        filter_name="ticker_availability",
        stage="ticker_prefilter",
        candidate_facts=facts,
    )
    row = get_db().execute(
        "SELECT candidate_facts FROM filter_decision_batches WHERE run_id=?",
        ("scanner-run-compact",),
    ).fetchone()
    naive = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    assert decode_filter_decision_batch(row["candidate_facts"]) == facts
    assert len(row["candidate_facts"]) < len(naive) * 0.7
    assert row["candidate_facts"].startswith("zlib-base64:")


def test_batch_controller_reads_only_referenced_outcome_keys():
    from aletheia.analytics.filter_controller import (
        _batch_scanner_weights_quota_stats,
    )
    from aletheia.analytics.filter_evidence import record_filter_decision_batch
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    now = datetime.now(timezone.utc)
    relevant_key = "universe:controller-relevant"
    record_filter_decision_batch(
        run_id="controller-bounded-read",
        candidate_kind="universe_asset",
        filter_name="scanner_weights_quota",
        stage="universe_allocation",
        candidate_facts=[{
            "candidate_key": "asset:relevant",
            "outcome_candidate_key": relevant_key,
            "decision": "passed",
            "scanner_family": "momentum",
            "direction": "long",
            "market_type": "spot",
        }],
    )
    outcomes = []
    for key in (relevant_key, "universe:controller-unrelated"):
        outcomes.append(FilterCandidateOutcome(
            outcome_candidate_key=key,
            venue="okx",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            scanner_family="momentum",
            horizon="1h",
            entry_price_usd=100.0,
            exit_price_usd=100.45,
            entry_ts=now.isoformat(),
            due_at=(now + timedelta(hours=1)).isoformat(),
            settled_at=(now + timedelta(hours=1)).isoformat(),
            status="settled",
            directional_return_pct=0.45,
            estimated_fee_pct=0.1,
            estimated_spread_slippage_pct=0.1,
            estimated_funding_pct=0.0,
            net_return_pct=0.25,
            min_tradable_edge_pct=0.05,
            settlement_price_source="horizon_ohlcv",
            settlement_lag_sec=0,
            cost_model_version=COST_MODEL_VERSION,
        ))
    repo.insert_filter_candidate_outcomes_batch(outcomes)

    statements: list[str] = []
    db = get_db()
    db.set_trace_callback(statements.append)
    try:
        rows = _batch_scanner_weights_quota_stats(
            as_of_at=(now + timedelta(days=2)).isoformat()
        )
    finally:
        db.set_trace_callback(None)

    outcome_selects = [
        statement
        for statement in statements
        if "FROM filter_candidate_outcomes" in statement
    ]
    assert outcome_selects
    assert all("outcome_candidate_key IN" in sql for sql in outcome_selects)
    scoped = [row for row in rows if row["horizon"] == "1h"]
    assert len(scoped) == 1
    assert scoped[0]["n_total"] == 1
    assert scoped[0]["n_net"] == 1


def test_filter_feedback_stats_ignores_legacy_cost_model_outcomes():
    from aletheia.analytics.filter_evidence import (
        filter_feedback_stats,
        record_filter_decision,
    )
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    common = dict(
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="paper_ranker",
        reason_code="test",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        parameters={"minimum_confidence": "medium"},
    )
    for suffix, decision, cost_model in (
        ("current", "passed", COST_MODEL_VERSION),
        ("legacy", "rejected", "legacy-cost-v0"),
    ):
        key = f"opportunity:cost-model:{suffix}"
        record_filter_decision(candidate_key=key, decision=decision, **common)
        get_db().execute(
            "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, "
            "directional_return_pct=0.4, exit_price_usd=100.4, "
            "estimated_fee_pct=0.1, estimated_spread_slippage_pct=0.1, "
            "estimated_funding_pct=0, net_return_pct=0.2, "
            "min_tradable_edge_pct=0.05, "
            "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
            "cost_model_version=? "
            "WHERE outcome_candidate_key=? AND horizon='4h'",
            (cost_model, key),
        )

    rows = [
        row for row in filter_feedback_stats(
            as_of_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        )
        if row["filter_name"] == "ranker_confidence_gate" and row["horizon"] == "4h"
    ]

    by_decision = {row["decision"]: row for row in rows}
    assert by_decision["passed"]["cost_model_version"] == COST_MODEL_VERSION
    assert by_decision["passed"]["n_net"] == 1
    assert by_decision["rejected"]["cost_model_version"] == "legacy-cost-v0"
    assert by_decision["rejected"]["n_net"] == 0




def test_controller_only_flags_overrestriction_after_paired_gates(monkeypatch):
    from aletheia.analytics import filter_controller

    common = {
        "filter_name": "family_ttl",
        "stage": "ttl_expired",
        "filter_version": "v1-test",
        "parameter_snapshot": "momentum=1800s",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "n_total": 25,
        "n_net": 20,
        "n": 20,
        "outcome_coverage": 0.8,
    }
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_stats",
        lambda **kwargs: [
            {**common, "decision": "passed", "shrunk_avg_net": -0.10},
            {**common, "decision": "rejected", "shrunk_avg_net": 0.20},
        ],
    )

    rec = next(
        row for row in filter_controller.compute_filter_feedback()
        if row.filter_name == "family_ttl"
    )
    assert rec.verdict == "overrestrictive_suspect"
    assert rec.suggested_direction == "review_loosen"
    assert rec.passed_n == 20
    assert rec.rejected_n == 20
    assert rec.stage == "ttl_expired"
    assert rec.current_value == "momentum=1800s"


def test_filter_feedback_stats_respects_decision_time_window():
    from aletheia.analytics.filter_evidence import (
        filter_feedback_stats,
        record_filter_decision,
    )
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    common = dict(
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="ranker_excluded",
        reason_code="test",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        parameters={"minimum_confidence": "medium"},
    )
    for age_days, suffix in ((10, "old"), (1, "new")):
        evaluated_at = (now - timedelta(days=age_days)).isoformat()
        for decision in ("passed", "rejected"):
            record_filter_decision(
                candidate_key=f"opportunity:{suffix}:{decision}",
                decision=decision,
                evaluated_at=evaluated_at,
                **common,
            )
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, net_return_pct=0.1, "
        "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0 "
        "WHERE horizon='1h'"
    )

    rows = filter_feedback_stats(
        evaluated_since=(now - timedelta(days=7)).isoformat()
    )
    scoped = [
        row for row in rows
        if row["filter_name"] == "ranker_confidence_gate" and row["horizon"] == "1h"
    ]

    assert {row["decision"] for row in scoped} == {"passed", "rejected"}
    assert all(row["n_total"] == 1 for row in scoped)


def test_filter_feedback_excludes_settlements_after_one_observed_cutoff():
    from aletheia.analytics.filter_controller import compute_filter_feedback
    from aletheia.analytics.filter_evidence import (
        filter_feedback_stats,
        record_filter_decision,
    )
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    as_of = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    common = dict(
        candidate_kind="opportunity",
        filter_name="family_ttl",
        stage="ttl_expired",
        reason_code="test",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        parameters={"minimum": "medium"},
    )
    for suffix, decision in (("passed", "passed"), ("rejected", "rejected")):
        key = f"opportunity:temporal-cutoff:{suffix}"
        record_filter_decision(candidate_key=key, decision=decision, **common)
        get_db().execute(
            "UPDATE filter_candidate_outcomes SET status='settled', "
            "entry_ts=?, due_at=?, settled_at=?, directional_return_pct=1.2, "
            "exit_price_usd=101.2, estimated_fee_pct=0.1, "
            "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0, "
            "net_return_pct=0.8, min_tradable_edge_pct=0.05, "
            "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
            "cost_model_version=? WHERE outcome_candidate_key=? AND horizon='4h'",
            (
                (as_of - timedelta(hours=4)).isoformat(),
                (as_of - timedelta(hours=1)).isoformat(),
                (as_of + timedelta(minutes=1)).isoformat(),
                COST_MODEL_VERSION,
                key,
            ),
        )
    get_db().commit()

    rows = [
        row for row in filter_feedback_stats(as_of_at=as_of)
        if row["filter_name"] == "family_ttl"
    ]
    assert {row["decision"] for row in rows} == {"passed", "rejected"}
    assert all(row["n_total"] == 1 for row in rows)
    assert all(row["n_net"] == 0 for row in rows)
    assert all(row["avg_net"] is None for row in rows)
    assert all(row["shrunk_avg_net"] is None for row in rows)
    assert all(row["outcome_coverage"] == 0.0 for row in rows)

    recommendation = next(
        row for row in compute_filter_feedback(min_samples=1, as_of_at=as_of)
        if row.filter_name == "family_ttl"
        and row.horizon == "4h"
    )
    assert recommendation.verdict == "insufficient_data"


def test_filter_feedback_assigns_each_candidate_to_first_exposure_only():
    from aletheia.analytics.filter_evidence import (
        filter_feedback_stats,
        filter_feedback_trajectory_summary,
        record_filter_decision,
    )
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    common = dict(
        candidate_kind="opportunity",
        filter_name="family_ttl",
        stage="paper_ranker",
        reason_code="test",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        parameters={"ttl_sec": 1800},
    )
    record_filter_decision(
        candidate_key="opportunity:repeated",
        decision="passed",
        evaluated_at=(now - timedelta(hours=2)).isoformat(),
        **common,
    )
    record_filter_decision(
        candidate_key="opportunity:repeated",
        decision="rejected",
        evaluated_at=(now - timedelta(hours=1)).isoformat(),
        **common,
    )
    record_filter_decision(
        candidate_key="opportunity:rejected-only",
        decision="rejected",
        evaluated_at=(now - timedelta(hours=1)).isoformat(),
        **common,
    )
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=due_at, net_return_pct=0.2, "
        "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0 "
        "WHERE horizon='1h'"
    )

    rows = filter_feedback_stats()
    scoped = {
        row["decision"]: row for row in rows
        if row["filter_name"] == "family_ttl" and row["horizon"] == "1h"
    }
    trajectory = filter_feedback_trajectory_summary()

    assert scoped["passed"]["n_total"] == 1
    assert scoped["rejected"]["n_total"] == 1
    assert trajectory["excluded_later_rows"] == 1
    assert trajectory["decision_transition_candidates"] == 1


def test_controller_uses_evidence_stage_instead_of_registry_stage(monkeypatch):
    """The controller must use the stage from the evidence rows, not the registry.

    Uses family_ttl with a custom stage (not the registry's 'ttl_expired') to
    prove the controller preserves what the evidence recorded.
    """
    from aletheia.analytics import filter_controller

    common = {
        "filter_name": "family_ttl",
        "stage": "custom_evidence_stage",
        "filter_version": "v1-stage-test",
        "parameter_snapshot": "momentum=1800s",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "15m",
        "n_total": 25,
        "n_net": 20,
        "n": 20,
        "outcome_coverage": 0.8,
    }
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_stats",
        lambda **kwargs: [
            {**common, "decision": "passed", "shrunk_avg_net": -0.10},
            {**common, "decision": "rejected", "shrunk_avg_net": 0.20},
        ],
    )

    rec = next(
        row for row in filter_controller.compute_filter_feedback()
        if row.filter_name == "family_ttl"
    )
    assert rec.stage == "custom_evidence_stage"
    assert rec.current_value == common["parameter_snapshot"]


def test_controller_persists_new_observational_verdict_through_legacy_schema(monkeypatch):
    from aletheia.analytics import filter_controller
    from aletheia.analytics.filter_controller import FilterRecommendation
    from aletheia.storage.sqlite import get_db

    from aletheia.observation_lab.canonical import set_authority_source
    set_authority_source("lab", actor="test", reason="controller fixture")
    recommendation = FilterRecommendation(
        filter_name="family_ttl",
        scanner_family="momentum",
        stage="ttl_expired",
        current_value="1800s",
        verdict="overrestrictive_suspect",
        suggested_direction="review_loosen",
        error_signal=0.2,
        n_samples=25,
        reasoning="observational only",
        direction="long",
        market_type="perp",
        horizon="4h",
        filter_version="v1-test",
        passed_n=25,
        rejected_n=25,
        passed_shrunk_avg_net=-0.1,
        rejected_shrunk_avg_net=0.1,
        outcome_coverage=0.8,
    )
    monkeypatch.setattr(
        filter_controller,
        "compute_filter_feedback",
        lambda min_samples=20, **_kwargs: [recommendation],
    )

    result = filter_controller.run_controller_observe()
    row = get_db().execute(
        "SELECT * FROM controller_recommendations WHERE run_id=?",
        (result["run_id"],),
    ).fetchone()

    assert result["recommendations_written"] == 1
    assert row["verdict"] == "insufficient_data"
    assert row["suggested_direction"] == "n/a"
    assert row["evidence_verdict"] == "overrestrictive_suspect"
    assert row["evidence_direction"] == "review_loosen"
    assert row["filter_version"] == "v1-test"
    assert row["direction"] == "long"
    assert row["market_type"] == "perp"
    assert row["horizon"] == "4h"


def test_shadow_batch_includes_actual_selection_beyond_top_n(monkeypatch):
    from aletheia.counterfactual.trades import persist_shadow_batch
    from aletheia.storage import repositories as repo

    candidates = [
        {
            "opportunity_id": f"opportunity-{index}",
            "symbol": f"ASSET{index}/USDT",
            "market_type": "spot",
            "direction": "long",
            "trade_score": 100 - index,
        }
        for index in range(6)
    ]
    monkeypatch.setattr(
        "aletheia.counterfactual.trades._current_price",
        lambda *args, **kwargs: 100.0,
    )

    persist_shadow_batch(candidates, [candidates[5]], top_n=5)
    rows = repo.list_shadow_trades(limit=20)

    assert len(rows) == 6
    selected = next(row for row in rows if row["was_selected"])
    assert selected["opportunity_id"] == "opportunity-5"
    assert selected["rank"] == 6
