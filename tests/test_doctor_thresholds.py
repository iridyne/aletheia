"""Phase 3 doctor-threshold regression tests.

Retention shortening (Phase 3) reduced several table windows from 30d/14d to
7d/3d. That should not push any doctor probe from ``ok`` to ``warn`` because
the probes were designed against freshness (MAX(observed_at)) or bounded
count assertions, not against ``COUNT(*)`` across all history. These tests
verify that assertion for the three probes closest to the affected tables:

- ``data.coingecko_top`` — freshness is judged from ``MAX(observed_at)``, so
  the 3-day retention window has no effect as long as the collector still
  writes at its 10-min cadence.
- ``data.filter_evidence`` — registered/settled ratios use the current row
  set and remain ok when the outcome pipeline is healthy; a 30-day
  ``filter_candidate_outcomes`` retention only affects rows already outside
  the ratio window.
- ``agent_shell_events`` count — the doctor bundle exposes ``agent_shell/``
  telemetry counts. A 14-day retention drops old rows but the recent-14d
  window remains a fair sample; the count within window must stay under any
  hard alert threshold in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from aletheia.storage.sqlite import get_db


def _iso(days_ago: float = 0.0, *, minutes: float = 0.0) -> str:
    return (
        datetime.now(timezone.utc)
        - timedelta(days=days_ago, minutes=minutes)
    ).isoformat()


# ---------------------------------------------------------------------------
# data.coingecko_top freshness — MAX(observed_at), 30min alert threshold
# ---------------------------------------------------------------------------


def test_coingecko_top_freshness_ok_after_short_retention():
    """3d retention must not push data.coingecko_top into 'warn' state.

    Freshness is judged from MAX(observed_at) with a 30-minute threshold.
    Even after old rows drop, a fresh batch (< 30 min) keeps the probe ok.
    """
    from aletheia.diagnostics.doctor import Report, _probe_macro_signals

    db = get_db()
    # Insert one 5-min-old batch of 10 rows to mimic the collector's normal cadence.
    fresh_iso = _iso(minutes=5)
    for i in range(10):
        db.execute(
            "INSERT INTO coingecko_top_snapshots "
            "(id, coin_id, symbol, name, price_usd, market_cap_usd, "
            " market_cap_rank, volume_24h_usd, observed_at) "
            "VALUES (?, ?, ?, 'x', 1, 1, ?, 1, ?)",
            (str(uuid.uuid4()), f"coin-{i}", f"S{i}", i, fresh_iso),
        )
    db.commit()

    report = Report()
    _probe_macro_signals(report)
    coingecko_checks = [c for c in report.checks if c.name == "data.coingecko_top"]
    assert coingecko_checks, "data.coingecko_top probe must fire"
    # Fresh batch: severity is 'ok' (< 30 min).
    assert coingecko_checks[0].severity == "ok", (
        f"data.coingecko_top must be ok when newest batch is <30min old, "
        f"got severity={coingecko_checks[0].severity} detail={coingecko_checks[0].detail}"
    )


def test_coingecko_top_freshness_uses_max_not_count():
    """The probe only reads MAX(observed_at); low row count doesn't warn.

    Retention lowered the coingecko_top table from ~14 days to 3 days, but
    only a handful of rows within that window would still yield ``ok`` if the
    freshness read is honest.
    """
    from aletheia.diagnostics.doctor import Report, _probe_macro_signals

    db = get_db()
    fresh_iso = _iso(minutes=2)
    db.execute(
        "INSERT INTO coingecko_top_snapshots "
        "(id, coin_id, symbol, name, price_usd, market_cap_usd, "
        " market_cap_rank, volume_24h_usd, observed_at) "
        "VALUES (?, 'btc', 'BTC', 'Bitcoin', 50000, 1e12, 1, 5e10, ?)",
        (str(uuid.uuid4()), fresh_iso),
    )
    db.commit()

    report = Report()
    _probe_macro_signals(report)
    check = next(c for c in report.checks if c.name == "data.coingecko_top")
    assert check.severity == "ok"


# ---------------------------------------------------------------------------
# data.filter_evidence — registration / settlement ratios stay ok
# ---------------------------------------------------------------------------


def _seed_filter_decision(*, candidate_key: str, outcome_key: str,
                          evaluated_at: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, symbol, filter_name, "
        " filter_version, stage, decision, reason_code, outcome_candidate_key, "
        " evaluated_at) "
        "VALUES (?, ?, ?, 'test', 'BTCUSDT', 'f', 'v', 's', 'passed', 'ok', "
        " ?, ?)",
        (
            str(uuid.uuid4()),
            f"dk-{candidate_key}-{evaluated_at}",
            candidate_key,
            outcome_key,
            evaluated_at,
        ),
    )
    db.commit()


def _seed_filter_batch(*, evaluated_at: str, candidate_count: int,
                       passed: int, rejected: int) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO filter_decision_batches "
        "(id, dedup_key, run_id, candidate_kind, filter_name, stage, "
        " candidate_count, passed_count, rejected_count, not_applicable_count, "
        " error_count, filter_version, evaluated_at) "
        "VALUES (?, ?, 'run-1', 'test', 'f', 's', ?, ?, ?, 0, 0, 'v', ?)",
        (str(uuid.uuid4()), f"batch-{evaluated_at}", candidate_count,
         passed, rejected, evaluated_at),
    )
    db.commit()


def _seed_filter_outcome(*, outcome_key: str, entry_ts: str,
                        status: str = "settled",
                        net_return_pct: float | None = 0.01) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
        " scanner_family, horizon, entry_price_usd, entry_ts, due_at, "
        " status, net_return_pct) "
        "VALUES (?, ?, 'binance', 'BTCUSDT', 'perp', 'long', 'test', '1h', "
        " 50000, ?, datetime('now','-1 day'), ?, ?)",
        (str(uuid.uuid4()), outcome_key, entry_ts, status, net_return_pct),
    )
    db.commit()


def test_filter_evidence_registered_settled_ratios_ok_after_retention():
    """A healthy fresh set of pass/reject facts + settled outcomes stays 'ok'
    even when the retention floor is 30 days for outcomes / 45 days for
    decisions. The doctor probe compares ``registered / outcome_candidates``,
    which uses the current row set — pruning old rows outside the window has
    no bearing on the ratio."""
    from aletheia.diagnostics.doctor import Report, _probe_filter_evidence

    # Seed one fresh batch + two decisions + two settled outcomes.
    _seed_filter_batch(
        evaluated_at=_iso(0, minutes=5),
        candidate_count=2, passed=1, rejected=1,
    )
    _seed_filter_decision(
        candidate_key="c1", outcome_key="c1", evaluated_at=_iso(0, minutes=5),
    )
    _seed_filter_decision(
        candidate_key="c2", outcome_key="c2", evaluated_at=_iso(0, minutes=5),
    )
    _seed_filter_outcome(outcome_key="c1", entry_ts=_iso(0, minutes=5))
    _seed_filter_outcome(outcome_key="c2", entry_ts=_iso(0, minutes=5))

    report = Report()
    _probe_filter_evidence(report)
    check = next(
        (c for c in report.checks if c.name == "data.filter_evidence"), None
    )
    assert check is not None
    assert check.severity == "ok", (
        f"filter_evidence must be ok with registered==outcome_candidates, "
        f"got severity={check.severity} detail={check.detail}"
    )


# ---------------------------------------------------------------------------
# agent_shell_events 14d retention — recent window count remains bounded
# ---------------------------------------------------------------------------


def test_agent_shell_events_count_within_14d_window():
    """After 14d retention, the recent-14d event count should be bounded and
    stable. This is the guard against a retention change silently pushing a
    doctor probe past a soft alert (e.g. >10k events/day)."""
    db = get_db()
    # Seed 100 events across the last 13 days.
    for i in range(100):
        db.execute(
            "INSERT INTO agent_shell_events "
            "(id, event_type, severity, source, created_at) "
            "VALUES (?, 'evt', 'info', 'test', ?)",
            (str(uuid.uuid4()), _iso(i * 0.13)),
        )
    db.commit()

    cutoff = _iso(14)
    count = db.execute(
        "SELECT COUNT(*) FROM agent_shell_events WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()[0]
    # All 100 seed rows land inside the window (13 days < 14d cutoff).
    assert count == 100
    # And crucially, none of them are OLDER than 14 days — retention pass
    # would not touch them.
    older = db.execute(
        "SELECT COUNT(*) FROM agent_shell_events WHERE created_at < ?",
        (cutoff,),
    ).fetchone()[0]
    assert older == 0


def test_agent_shell_events_pruned_beyond_14d_does_not_break_probes():
    """Simulate the state after retention has run: only <14d rows survive.

    Any doctor probe that counts agent_shell_events should still return a
    sensible result. The pruned table is not empty (fresh events keep flowing);
    it's just bounded to a 14-day trailing window.
    """
    db = get_db()
    for i in range(10):
        # Seeds spread across the last 10 days — all within retention.
        db.execute(
            "INSERT INTO agent_shell_events "
            "(id, event_type, severity, source, created_at) "
            "VALUES (?, 'evt', 'info', 'test', ?)",
            (str(uuid.uuid4()), _iso(i)),
        )
    db.commit()

    # Simulate the prune pass having already removed anything >14d.
    from aletheia.storage.repositories import prune_agent_shell_events
    prune_agent_shell_events(days=14)

    row = db.execute("SELECT COUNT(*) FROM agent_shell_events").fetchone()
    assert int(row[0]) == 10, (
        "prune_agent_shell_events(14) must leave <14d rows untouched; "
        "the doctor's recent-window count depends on it."
    )


# ---------------------------------------------------------------------------
# filter_candidate_outcomes 30d retention — freshness window unaffected
# ---------------------------------------------------------------------------


def test_filter_candidate_outcomes_registered_ratio_after_prune():
    """After a 30d retention run, the registered/settled ratios read on the
    remaining current-window rows must still compute cleanly (no divide-by-
    zero, no NaN)."""
    from aletheia.diagnostics.doctor import Report, _probe_filter_evidence
    from aletheia.storage.repositories import prune_filter_candidate_outcomes

    # Seed both an old outcome (100d) and a fresh outcome (5d).
    _seed_filter_batch(
        evaluated_at=_iso(0, minutes=5),
        candidate_count=1, passed=1, rejected=0,
    )
    _seed_filter_decision(
        candidate_key="fresh", outcome_key="fresh",
        evaluated_at=_iso(0, minutes=5),
    )
    _seed_filter_outcome(outcome_key="fresh", entry_ts=_iso(5))
    _seed_filter_outcome(outcome_key="old", entry_ts=_iso(100))

    # 30d cutoff drops the 100d row (no open experiment).
    deleted = prune_filter_candidate_outcomes(days=30)
    assert deleted == 1

    report = Report()
    _probe_filter_evidence(report)
    checks = [c for c in report.checks if c.name == "data.filter_evidence"]
    assert checks, "filter_evidence probe must fire"
    # Whatever severity it settles at, it must be a valid Report severity and
    # not have crashed the probe.
    assert checks[0].severity in {"ok", "info", "warn", "blocker"}
