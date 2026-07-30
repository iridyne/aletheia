"""An unreachable venue is not a measurement failure.

Every settle loop reduces its observation attempt to one question — "is there an
exit price?" — and writes ``status='failed'`` when the answer is no. That write is
terminal: no job revisits a failed row, so the counterfactual it represents is
never measured again.

The write is correct for exactly one of the two ways the answer can be no. If the
venue answered and its answer contains no usable price for the window, the row is
genuinely unmeasurable. If the venue could not be reached at all — transport
error, timeout, rate-limit ban — then nothing was measured, the bars the row would
have been measured from still exist, and the row must come back next cycle.

Measured consequence of conflating them: a 20-minute Binance IP ban
(``418 -1003 ... IP banned until ...``) permanently wrote off 335 rows across
``filter_candidate_outcomes`` and ``universe_forward_outcomes`` in two settlement
cycles. Every one had ``exit_price_usd IS NULL`` and
``settlement_price_source IS NULL`` — no observation was ever taken — while their
1m bars sat in ``market_ohlcv`` the whole time.

These tests pin both directions: a deferral must never become terminal, and a
venue that does answer must still be able to close a row out.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------


def _due_filter_outcome(key, *, horizon="24h", hours_ago=25):
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


def _forward_row(row_id="outcome-defer", *, hours_ago=25):
    entry = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "id": row_id,
        "member_id": f"member-{row_id}",
        "snapshot_id": f"snapshot-{row_id}",
        "symbol": "ADA/USDT",
        "venue": "binance",
        "market_type": "spot",
        "scanner_family": "momentum",
        "direction": "long",
        "horizon": "1h",
        "entry_price_usd": 100.0,
        "entry_spread_bps": 8.0,
        "funding_rate_at_entry": None,
        "entry_ts": entry.isoformat(),
        "due_at": (entry + timedelta(hours=1)).isoformat(),
        "status": "pending",
    }


def _deferring_observations(calls=None):
    """Stand in for a venue that answered nothing at all."""
    from aletheia.universe.outcome_recorder import deferred_observation

    async def observations(rows, *, now):
        if calls is not None:
            calls.append([row["id"] for row in rows])
        return {
            row["id"]: deferred_observation("ohlcv:RateLimitExceeded")
            for row in rows
        }

    return observations


def _silent_observations(calls=None):
    """Stand in for a venue that answered, with no usable price in it."""

    async def observations(rows, *, now):
        if calls is not None:
            calls.append([row["id"] for row in rows])
        return {}

    return observations


class _BannedProvider:
    """Every endpoint raises, exactly as a rate-limit ban presents through ccxt."""

    def __init__(self):
        self.ohlcv_calls = 0
        self.ticker_calls = 0

    async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
        self.ohlcv_calls += 1
        raise RuntimeError(
            'binance 418 -1003 {"msg":"Way too many requests; IP(1.2.3.4) banned"}'
        )

    async def get_ticker(self, instrument):
        self.ticker_calls += 1
        raise RuntimeError("binance 418 -1003 IP banned")

    async def close(self):
        pass


class _EmptyAnsweringProvider:
    """A venue that answers both probes intact and simply has no price."""

    def __init__(self):
        self.ohlcv_calls = 0
        self.ticker_calls = 0

    async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
        self.ohlcv_calls += 1
        return []

    async def get_ticker(self, instrument):
        from aletheia.data.providers import Ticker

        self.ticker_calls += 1
        return Ticker(symbol="ADA/USDT", last_price=0.0, volume_24h=0.0,
                      change_pct_24h=0.0)

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# the observation layer's own classification
# ---------------------------------------------------------------------------


def test_a_transport_error_is_reported_as_deferred_not_as_no_price(monkeypatch):
    """The classification has to happen here; the settle loops cannot infer it.

    By the time a loop sees ``observation is None`` the reason is gone, so the
    distinction has to be carried in the observation map itself.
    """
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        is_deferred,
    )

    provider = _BannedProvider()
    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider", lambda: provider
    )
    row = _forward_row("row-banned")

    observations = asyncio.run(
        _batch_fetch_market_observations([row], now=datetime.now(timezone.utc))
    )

    assert provider.ohlcv_calls == 1 and provider.ticker_calls == 1
    assert is_deferred(observations.get("row-banned")), (
        "a venue that raised on every probe measured nothing — it did not report "
        "'no price'"
    )
    assert observations["row-banned"]["exit_price"] is None


def test_an_intact_empty_answer_is_reported_as_no_price(monkeypatch):
    """The terminal path must survive: this is what `failed` is actually for."""
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        is_deferred,
    )

    provider = _EmptyAnsweringProvider()
    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider", lambda: provider
    )
    row = _forward_row("row-empty")

    observations = asyncio.run(
        _batch_fetch_market_observations([row], now=datetime.now(timezone.utc))
    )

    assert provider.ohlcv_calls == 1 and provider.ticker_calls == 1
    assert not is_deferred(observations.get("row-empty"))
    assert "row-empty" not in observations, (
        "an intact answer with no price stays absent from the map, so the caller's "
        "terminal write still fires"
    )


def test_an_unusable_instrument_stays_terminal(monkeypatch):
    """A deterministic data defect must NOT be deferred.

    Deferral is bounded only by the outage that caused it: it ends when the venue
    answers again. A row that can never be planned has no such end, so deferring
    it would park it in the queue until retention deleted it, and every cycle
    would re-plan it. That is the queue-growth failure mode, not a repair.
    """
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        is_deferred,
    )

    provider = _BannedProvider()
    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider", lambda: provider
    )
    row = _forward_row("row-unplannable")
    row["entry_ts"] = "not-a-timestamp"

    observations = asyncio.run(
        _batch_fetch_market_observations([row], now=datetime.now(timezone.utc))
    )

    assert provider.ohlcv_calls == 0, "an unplannable row must not reach the venue"
    assert not is_deferred(observations.get("row-unplannable"))
    assert "row-unplannable" not in observations


def test_deferrals_are_logged_once_per_batch_as_an_outage(monkeypatch, caplog):
    """The ban destroyed 335 rows while the log said only 'price unavailable'.

    Per-row warnings phrased as data problems are why a venue outage read as a
    data-quality drift. One aggregated line per batch names the real cause.
    """
    import logging

    from aletheia.universe.outcome_recorder import _batch_fetch_market_observations

    provider = _BannedProvider()
    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider", lambda: provider
    )
    rows = [_forward_row(f"row-log-{i}") for i in range(3)]

    with caplog.at_level(
        logging.WARNING, logger="aletheia.universe.outcome_recorder"
    ):
        asyncio.run(
            _batch_fetch_market_observations(rows, now=datetime.now(timezone.utc))
        )

    outage = [r for r in caplog.records if "could not be reached" in r.getMessage()]
    assert len(outage) == 1, "one line per batch, not one per row"
    assert "3" in outage[0].getMessage()


# ---------------------------------------------------------------------------
# the canonical (filter) settle loop
# ---------------------------------------------------------------------------


def test_canonical_row_stays_pending_when_the_venue_is_unreachable(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    outcome = _due_filter_outcome("opportunity:canonical-deferred")
    repo.insert_filter_candidate_outcomes_batch([outcome])
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _deferring_observations(),
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8)) == 0
    row = get_db().execute(
        "SELECT status, settled_at FROM filter_candidate_outcomes WHERE id=?",
        (outcome.id,),
    ).fetchone()
    assert row["status"] == "pending", (
        "the venue was never reached, so this counterfactual is still measurable"
    )
    assert row["settled_at"] is None


def test_canonical_row_still_fails_when_the_venue_answers_with_no_price(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    outcome = _due_filter_outcome("opportunity:canonical-terminal")
    repo.insert_filter_candidate_outcomes_batch([outcome])
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _silent_observations(),
    )

    assert asyncio.run(settle_due_filter_outcomes(max_rows=8)) == 0
    row = get_db().execute(
        "SELECT status FROM filter_candidate_outcomes WHERE id=?", (outcome.id,)
    ).fetchone()
    assert row["status"] == "failed", (
        "an answered-but-unmeasurable row must still reach a terminal state, or the "
        "queue never drains"
    )


def test_an_unreachable_venue_stops_the_cycle_instead_of_spending_the_budget(
    monkeypatch,
):
    """A wave that reaches nothing must not be followed by more waves.

    Each round regroups whatever is still unanswered and fetches a fresh
    representative, so without this a ban converts the whole request allowance
    into requests against an endpoint that is not answering — and, on a
    rate-limit ban, extends the ban that caused the outage.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    outcomes = [
        _due_filter_outcome(f"opportunity:banned-{i}", horizon="24h")
        for i in range(4)
    ]
    for i, outcome in enumerate(outcomes):
        outcome.symbol = f"SYM{i}/USDT"  # distinct instruments → distinct groups
    repo.insert_filter_candidate_outcomes_batch(outcomes)

    calls = []
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _deferring_observations(calls),
    )

    assert asyncio.run(
        settle_due_filter_outcomes(max_rows=16, max_fetches=400)
    ) == 0
    assert len(calls) == 1, (
        f"the exchange phase must stop after the first fully-deferred wave, got "
        f"{len(calls)} waves"
    )
    statuses = {
        r["status"]
        for r in get_db().execute(
            "SELECT status FROM filter_candidate_outcomes WHERE id IN "
            f"({','.join('?' for _ in outcomes)})",
            tuple(o.id for o in outcomes),
        )
    }
    assert statuses == {"pending"}


def test_a_deferred_cycle_says_the_venue_was_unreachable(monkeypatch, caplog):
    """'the canonical queue is not draining' is the wrong diagnosis for a ban.

    Both produce a zero-throughput cycle, and they need different operator
    actions, so the warning has to name which one happened.
    """
    import logging

    from aletheia.storage import repositories as repo
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    repo.insert_filter_candidate_outcomes_batch(
        [_due_filter_outcome("opportunity:deferred-log")]
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _deferring_observations(),
    )

    with caplog.at_level(
        logging.WARNING, logger="aletheia.universe.filter_outcome_recorder"
    ):
        assert asyncio.run(settle_due_filter_outcomes(max_rows=8)) == 0

    messages = [r.getMessage() for r in caplog.records]
    assert any("unreachable" in m for m in messages), messages
    assert not any("not draining" in m for m in messages), (
        "a reachability outage must not be reported as a stalled queue"
    )


# ---------------------------------------------------------------------------
# the forward mirror settle loop
# ---------------------------------------------------------------------------


def test_forward_row_stays_pending_when_the_venue_is_unreachable(monkeypatch):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_recorder import settle_due

    entry = datetime.now(timezone.utc) - timedelta(hours=25)
    outcome = UniverseForwardOutcome(
        member_id="member-fwd-defer",
        snapshot_id="snapshot-fwd-defer",
        symbol="ADA/USDT",
        venue="binance",
        market_type="spot",
        scanner_family="momentum",
        direction="long",
        horizon="1h",
        entry_price_usd=100.0,
        entry_ts=entry.isoformat(),
        due_at=(entry + timedelta(hours=1)).isoformat(),
    )
    repo.insert_forward_outcomes_batch([outcome])
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _deferring_observations(),
    )

    asyncio.run(settle_due(max_rows=2, time_budget_sec=5.0))

    row = get_db().execute(
        "SELECT status, settled_at FROM universe_forward_outcomes WHERE id=?",
        (outcome.id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["settled_at"] is None


def test_the_forward_loop_does_not_reselect_a_row_it_deferred(monkeypatch):
    """The forward loop re-queries the same oldest-due head every batch.

    A deferred row keeps ``status='pending'``, so it is still at the head of that
    query. Without excluding it the loop asks the same unreachable venue about the
    same row until the wall clock runs out, which is both an infinite retry inside
    one cycle and load on a banned IP.
    """
    from aletheia.universe.outcome_recorder import settle_due

    rows = [_forward_row(f"row-reselect-{i}") for i in range(3)]
    calls = []

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.list_pending_forward_outcomes",
        lambda **kwargs: list(rows),
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.fail_forward_outcome",
        lambda *a, **k: pytest.fail("a deferred row must not be written off"),
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _deferring_observations(calls),
    )

    asyncio.run(settle_due(max_rows=8, time_budget_sec=5.0))

    assert len(calls) == 1, (
        f"each row may be asked about once per cycle, got waves {calls}"
    )


# ---------------------------------------------------------------------------
# the Position X observation-only loop
# ---------------------------------------------------------------------------


def test_perp_structure_row_stays_due_when_the_venue_is_unreachable(monkeypatch):
    from aletheia.data.perp_structure_shadow import (
        settle_due_perp_structure_outcomes,
    )
    from aletheia.storage.sqlite import get_db

    entry = datetime.now(timezone.utc) - timedelta(hours=25)
    db = get_db()
    db.execute(
        "INSERT INTO perp_structure_snapshots (id, symbol, venue, as_of_time, "
        "reference_price, structure_bias, data_quality, source_ref, source_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("snap-defer", "BTC/USDT", "hyperliquid", entry.isoformat(), 100.0,
         "bullish", "complete", "test", "hash"),
    )
    db.execute(
        "INSERT INTO perp_structure_shadow_outcomes (id, snapshot_id, symbol, venue, "
        "market_type, direction, horizon, entry_price_usd, entry_ts, due_at, status, "
        "policy_epoch_id, source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("psso-defer", "snap-defer", "BTC/USDT", "hyperliquid", "perp", "long", "1h",
         100.0, entry.isoformat(), (entry + timedelta(hours=1)).isoformat(),
         "pending", "epoch", "hash"),
    )
    db.commit()

    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        _deferring_observations(),
    )

    assert asyncio.run(settle_due_perp_structure_outcomes(limit=10)) == 0
    row = db.execute(
        "SELECT status FROM perp_structure_shadow_outcomes WHERE id=?",
        ("psso-defer",),
    ).fetchone()
    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# reclaiming what the ban already destroyed
# ---------------------------------------------------------------------------


def _insert_failed(table, row_id, *, observed):
    """A row already written off, with or without an observation behind it."""
    from aletheia.storage.sqlite import get_db

    entry = datetime.now(timezone.utc) - timedelta(hours=25)
    db = get_db()
    common = (
        row_id, "ADA/USDT", "binance", "spot", "long", "1h", 100.0,
        entry.isoformat(), (entry + timedelta(hours=1)).isoformat(),
        datetime.now(timezone.utc).isoformat(),
        102.0 if observed else None,
        "horizon_ohlcv" if observed else None,
    )
    if table == "filter_candidate_outcomes":
        db.execute(
            "INSERT INTO filter_candidate_outcomes (id, outcome_candidate_key, symbol, "
            "venue, market_type, direction, horizon, entry_price_usd, entry_ts, due_at, "
            "settled_at, exit_price_usd, settlement_price_source, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'failed')",
            (row_id, f"key-{row_id}", *common[1:]),
        )
    else:
        db.execute(
            "INSERT INTO universe_forward_outcomes (id, member_id, snapshot_id, symbol, "
            "venue, market_type, direction, horizon, entry_price_usd, entry_ts, due_at, "
            "settled_at, exit_price_usd, settlement_price_source, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'failed')",
            (row_id, f"member-{row_id}", f"snap-{row_id}", *common[1:]),
        )
    db.commit()


def test_requeue_restores_only_the_rows_that_were_never_observed():
    """Reclaim the evidence the ban destroyed, and nothing else.

    ``exit_price_usd IS NULL AND settlement_price_source IS NULL`` on a failed row
    means no observation was ever taken — the only way to reach that state is a
    write-off before any price arrived. A row that failed WITH an observation
    behind it (the 10x contamination guard) was a real verdict and must stay.
    """
    from aletheia.maintenance.settlement_requeue import (
        requeue_unobserved_failed_outcomes,
    )
    from aletheia.storage.sqlite import get_db

    _insert_failed("filter_candidate_outcomes", "fco-unobserved", observed=False)
    _insert_failed("filter_candidate_outcomes", "fco-observed", observed=True)
    _insert_failed("universe_forward_outcomes", "ufo-unobserved", observed=False)

    result = requeue_unobserved_failed_outcomes(dry_run=False)

    assert result["filter_candidate_outcomes"] == 1
    assert result["universe_forward_outcomes"] == 1
    db = get_db()
    for table, row_id, expected in (
        ("filter_candidate_outcomes", "fco-unobserved", "pending"),
        ("filter_candidate_outcomes", "fco-observed", "failed"),
        ("universe_forward_outcomes", "ufo-unobserved", "pending"),
    ):
        row = db.execute(
            f"SELECT status, settled_at FROM {table} WHERE id=?", (row_id,)
        ).fetchone()
        assert row["status"] == expected, f"{table}/{row_id}"
        if expected == "pending":
            assert row["settled_at"] is None, (
                "a requeued row must not keep the settled_at of its write-off"
            )

    # Idempotent: a second pass has nothing left to reclaim.
    again = requeue_unobserved_failed_outcomes(dry_run=False)
    assert again["filter_candidate_outcomes"] == 0
    assert again["universe_forward_outcomes"] == 0


def test_doctor_reports_destroyed_evidence_and_how_to_reclaim_it():
    """Every other number in this probe drains on its own; this one does not.

    Overdue and stale_overdue describe rows the settler will still get to. A
    never-observed write-off is terminal — it stays until someone runs the
    reclaim — so it must not be reported by the same 'wait for the settler'
    recommendations, and it must name the command that fixes it.
    """
    from aletheia.diagnostics.doctor import Report, _probe_filter_evidence
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO filter_decision_batches (id, dedup_key, run_id, candidate_kind, "
        "filter_name, stage, candidate_count, passed_count, rejected_count, "
        "not_applicable_count, error_count, filter_version, evaluated_at) "
        "VALUES ('batch-doc','dk-batch-doc','run-doc','test','f','s',1,1,0,0,0,'v',"
        "datetime('now','-5 minutes'))"
    )
    db.execute(
        "INSERT INTO filter_decisions (id, dedup_key, candidate_key, candidate_kind, "
        "symbol, filter_name, filter_version, stage, decision, reason_code, "
        "outcome_candidate_key, evaluated_at) "
        "VALUES ('dec-doc','dk-dec-doc','cand-doc','test','BTC/USDT','f','v','s',"
        "'passed','ok','key-doc',datetime('now','-5 minutes'))"
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes (id, outcome_candidate_key, venue, "
        "symbol, market_type, direction, scanner_family, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, settlement_price_source, "
        "net_return_pct, status) VALUES ('fco-doc-ok','key-doc','binance','BTC/USDT',"
        "'spot','long','momentum','1h',100.0,datetime('now','-2 hours'),"
        "datetime('now','-1 hours'),datetime('now'),102.0,'horizon_ohlcv',1.5,'settled')"
    )
    db.commit()

    healthy = Report()
    _probe_filter_evidence(healthy)
    baseline = next(c for c in healthy.checks if c.name == "data.filter_evidence")
    assert baseline.severity == "ok", baseline.detail
    assert "unobserved_failed=0" in baseline.detail

    _insert_failed("universe_forward_outcomes", "ufo-doc-destroyed", observed=False)

    report = Report()
    _probe_filter_evidence(report)
    check = next(c for c in report.checks if c.name == "data.filter_evidence")
    assert check.severity == "warn"
    assert "unobserved_failed=1" in check.detail
    assert "requeue-unobserved-settlements" in check.fix_hint, (
        "a state that needs an operator action must say which action"
    )


def test_requeue_dry_run_reports_without_writing():
    from aletheia.maintenance.settlement_requeue import (
        requeue_unobserved_failed_outcomes,
    )
    from aletheia.storage.sqlite import get_db

    _insert_failed("filter_candidate_outcomes", "fco-dry", observed=False)

    result = requeue_unobserved_failed_outcomes()

    assert result["dry_run"] is True
    assert result["filter_candidate_outcomes"] == 1
    row = get_db().execute(
        "SELECT status FROM filter_candidate_outcomes WHERE id=?", ("fco-dry",)
    ).fetchone()
    assert row["status"] == "failed", "dry run must not change terminal state"
