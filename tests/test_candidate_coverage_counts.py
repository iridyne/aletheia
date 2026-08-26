"""Regression tests for window-bounded candidate coverage counts.

Guards iridite/aletheia#1328: the control-status liveness probe must not
materialize the full filter_decisions / filter_decision_batches /
filter_candidate_outcomes tables (OOM on 4 GB runtimes).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import aletheia.analytics.candidate_facts as candidate_facts
from aletheia.storage import sqlite as sq


LOWER = datetime(2026, 8, 1, tzinfo=timezone.utc)
UPPER = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _decision(
    db,
    ident: str,
    evaluated_at: str,
    *,
    outcome_key: str | None = None,
    candidate_key: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO filter_decisions ("
        "id, dedup_key, candidate_key, candidate_kind, filter_name, stage, "
        "sequence_no, decision, reason_code, filter_version, symbol, "
        "outcome_candidate_key, evaluated_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ident,
            f"dedup-{ident}",
            candidate_key or f"ck-{ident}",
            "opportunity",
            "ranker",
            "selection",
            0,
            "passed",
            "eligible",
            "v1",
            "BTCUSDT",
            outcome_key,
            evaluated_at,
        ),
    )


def _batch(
    db,
    ident: str,
    evaluated_at: str,
    facts: list[dict] | str,
    *,
    candidate_count: int | None = None,
) -> None:
    payload = facts if isinstance(facts, str) else json.dumps(facts)
    declared = candidate_count if candidate_count is not None else (
        len(facts) if not isinstance(facts, str) else 0
    )
    db.execute(
        "INSERT INTO filter_decision_batches ("
        "id, dedup_key, run_id, candidate_kind, filter_name, stage, "
        "candidate_count, candidate_facts, filter_version, evaluated_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            ident,
            f"dedup-{ident}",
            "run-1",
            "opportunity",
            "ranker",
            "selection",
            declared,
            payload,
            "v1",
            evaluated_at,
        ),
    )


def _outcome(
    db,
    ident: str,
    outcome_key: str,
    *,
    status: str = "settled",
    horizon: str = "24h",
    entry_ts: str,
    settled_at: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO filter_candidate_outcomes ("
        "id, outcome_candidate_key, venue, symbol, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, status"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            ident,
            outcome_key,
            "bitget",
            "BTCUSDT",
            "long",
            horizon,
            1.0,
            entry_ts,
            entry_ts,
            settled_at,
            status,
        ),
    )


def test_window_counts_use_first_exposure():
    db = sq.get_db()
    # A: two rows in window under one identity -> 1 unit
    _decision(db, "a1", "2026-08-02T00:00:00+00:00", outcome_key="cand-a")
    _decision(db, "a2", "2026-08-03T00:00:00+00:00", outcome_key="cand-a")
    # B: first exposure before the window -> excluded despite an in-window row
    _decision(db, "b1", "2026-07-20T00:00:00+00:00", outcome_key="cand-b")
    _decision(db, "b2", "2026-08-04T00:00:00+00:00", outcome_key="cand-b")
    # C: fully inside the window -> 1 unit
    _decision(db, "c1", "2026-08-05T00:00:00+00:00", outcome_key="cand-c")
    # D: after the window -> excluded
    _decision(db, "d1", "2026-09-01T00:00:00+00:00", outcome_key="cand-d")

    counts = candidate_facts.candidate_coverage_counts(
        db, horizon="24h", lower=LOWER, upper=UPPER
    )
    assert counts["candidate_units"] == 2
    assert counts["settled_outcome_units"] == 0
    assert counts["outcome_coverage"] == 0.0


def test_batches_stream_counted_with_exclusions():
    db = sq.get_db()
    _batch(
        db,
        "batch-ok",
        "2026-08-02T01:00:00+00:00",
        [
            {"candidate_key": "bk-1", "evaluated_at": "2026-08-02T01:00:00+00:00"},
            {"filter_name": "ranker"},  # falls back to batch id identity
        ],
    )
    _batch(db, "batch-malformed", "2026-08-03T01:00:00+00:00", "not-json{{")
    _batch(
        db,
        "batch-mismatch",
        "2026-08-04T01:00:00+00:00",
        [{"candidate_key": "bk-x"}],
        candidate_count=5,
    )
    _batch(
        db,
        "batch-outside",
        "2026-09-02T01:00:00+00:00",
        [{"candidate_key": "bk-out"}],
    )

    counts = candidate_facts.candidate_coverage_counts(
        db, horizon="24h", lower=LOWER, upper=UPPER
    )
    assert counts["candidate_units"] == 2
    assert counts["unavailable_units"] == 2


def test_candidate_facts_epoch_start_excludes_pre_epoch_malformed_batches():
    db = sq.get_db()
    _batch(db, "old-bad", "2026-07-20T00:00:00+00:00", "{not-json")
    _batch(
        db,
        "boundary-good",
        LOWER.isoformat(),
        [
            {
                "candidate_key": "cand-boundary",
                "outcome_candidate_key": "cand-boundary",
                "evaluated_at": LOWER.isoformat(),
                "decision": "passed",
            }
        ],
    )
    _outcome(
        db,
        "outcome-boundary",
        "cand-boundary",
        entry_ts="2026-08-01T00:30:00+00:00",
        settled_at="2026-08-02T00:30:00+00:00",
    )
    db.commit()

    epoch_report = candidate_facts.build_candidate_facts(
        horizon="24h",
        as_of_at=UPPER.isoformat(),
        epoch_start_at=LOWER.isoformat(),
        db=db,
    )
    assert epoch_report["epoch_start_at"] == LOWER.isoformat()
    assert epoch_report["unavailable_count"] == 0
    assert [fact["fact_id"] for fact in epoch_report["facts"]] == ["cand-boundary"]
    assert epoch_report["settled_outcome_count"] == 1

    forensic_report = candidate_facts.build_candidate_facts(
        horizon="24h", as_of_at=UPPER.isoformat(), db=db
    )
    assert forensic_report["unavailable_count"] == 1
    assert forensic_report["exclusions"][0]["source_batch_id"] == "old-bad"

    db.execute(
        "UPDATE filter_decision_batches SET evaluated_at=? WHERE id='old-bad'",
        (LOWER.isoformat(),),
    )
    db.commit()
    contaminated = candidate_facts.build_candidate_facts(
        horizon="24h",
        as_of_at=UPPER.isoformat(),
        epoch_start_at=LOWER.isoformat(),
        db=db,
    )
    assert contaminated["unavailable_count"] == 1
    assert contaminated["exclusions"][0]["source_batch_id"] == "old-bad"


def test_settled_outcome_intersection():
    db = sq.get_db()
    _decision(db, "a1", "2026-08-02T00:00:00+00:00", outcome_key="cand-a")
    _decision(db, "c1", "2026-08-05T00:00:00+00:00", outcome_key="cand-c")
    # in-window identity, settled within cutoff -> counted
    _outcome(
        db,
        "o1",
        "cand-a",
        entry_ts="2026-08-02T00:30:00+00:00",
        settled_at="2026-08-03T00:30:00+00:00",
    )
    # pending outcome -> not settled
    _outcome(
        db,
        "o2",
        "cand-c",
        status="pending",
        entry_ts="2026-08-05T00:30:00+00:00",
    )
    # settled after the cutoff -> not counted
    _decision(db, "e1", "2026-08-06T00:00:00+00:00", outcome_key="cand-e")
    _outcome(
        db,
        "o3",
        "cand-e",
        entry_ts="2026-08-06T00:30:00+00:00",
        settled_at="2026-08-20T00:30:00+00:00",
    )
    # outcome for a key with no window candidate -> ignored
    _outcome(
        db,
        "o4",
        "cand-ghost",
        entry_ts="2026-08-07T00:30:00+00:00",
    )
    # other horizon -> ignored
    _outcome(
        db,
        "o5",
        "cand-a",
        horizon="1h",
        entry_ts="2026-08-02T00:30:00+00:00",
    )

    counts = candidate_facts.candidate_coverage_counts(
        db, horizon="24h", lower=LOWER, upper=UPPER
    )
    assert counts["candidate_units"] == 3
    assert counts["settled_outcome_units"] == 1
    assert counts["outcome_coverage"] == pytest.approx(1 / 3)


def test_unknown_timestamp_units_are_table_wide():
    db = sq.get_db()
    _decision(db, "a1", "2026-08-02T00:00:00+00:00", outcome_key="cand-a")
    _decision(db, "empty-ts", "", outcome_key="cand-empty")
    _decision(db, "bad-ts", "not-a-timestamp", outcome_key="cand-bad")

    counts = candidate_facts.candidate_coverage_counts(
        db, horizon="24h", lower=LOWER, upper=UPPER
    )
    assert counts["candidate_units"] == 1
    assert counts["unknown_timestamp_units"] == 2


def test_parity_with_full_report_on_window():
    db = sq.get_db()
    _decision(db, "a1", "2026-08-02T00:00:00+00:00", outcome_key="cand-a")
    _decision(db, "a2", "2026-08-03T00:00:00+00:00", outcome_key="cand-a")
    _decision(db, "b1", "2026-07-20T00:00:00+00:00", outcome_key="cand-b")
    _decision(db, "b2", "2026-08-04T00:00:00+00:00", outcome_key="cand-b")
    _decision(db, "c1", "2026-08-05T00:00:00+00:00", outcome_key="cand-c")
    _outcome(
        db,
        "o1",
        "cand-a",
        entry_ts="2026-08-02T00:30:00+00:00",
        settled_at="2026-08-03T00:30:00+00:00",
    )
    _outcome(
        db,
        "o2",
        "cand-c",
        status="pending",
        entry_ts="2026-08-05T00:30:00+00:00",
    )

    report = candidate_facts.build_candidate_facts(
        horizon="24h", as_of_at=UPPER.isoformat(), db=db
    )
    window_facts = []
    for fact in report["facts"]:
        parsed = datetime.fromisoformat(str(fact["event_at"]).replace("Z", "+00:00"))
        if LOWER <= parsed <= UPPER:
            window_facts.append(fact)
    expected_units = len(window_facts)
    expected_settled = sum(
        1
        for fact in window_facts
        if isinstance(fact.get("fixed_horizon_outcome"), dict)
        and fact["fixed_horizon_outcome"].get("status") == "settled"
    )

    counts = candidate_facts.candidate_coverage_counts(
        db, horizon="24h", lower=LOWER, upper=UPPER
    )
    assert counts["candidate_units"] == expected_units == 2
    assert counts["settled_outcome_units"] == expected_settled == 1


def test_profit_loop_coverage_probe_never_builds_full_facts(monkeypatch):
    db = sq.get_db()
    _decision(db, "a1", "2026-08-02T00:00:00+00:00", outcome_key="cand-a")
    _outcome(
        db,
        "o1",
        "cand-a",
        entry_ts="2026-08-02T00:30:00+00:00",
        settled_at="2026-08-03T00:30:00+00:00",
    )

    def _boom(**_kwargs):
        raise AssertionError(
            "liveness probe must not materialize full candidate facts"
        )

    monkeypatch.setattr(candidate_facts, "build_candidate_facts", _boom)

    import aletheia.cli.control_cmd as control_cmd

    result = control_cmd._profit_loop_coverage(
        registration_at="2026-08-01T00:00:00+00:00",
        cutoff_at="2026-08-10T00:00:00+00:00",
    )
    assert result["availability"] == "available"
    assert result["candidate"]["units"] == 1
    assert result["candidate"]["settled_outcome_units"] == 1
    assert result["candidate"]["outcome_coverage"] == 1.0
    assert result["position"]["availability"] == "available"
    assert result["path"]["availability"] == "available"


def test_invalid_window_rejected():
    db = sq.get_db()
    with pytest.raises(ValueError):
        candidate_facts.candidate_coverage_counts(
            db, horizon="24h", lower=UPPER, upper=LOWER
        )
    with pytest.raises(ValueError):
        candidate_facts.candidate_coverage_counts(
            db,
            horizon="24h",
            lower=LOWER.replace(tzinfo=None),
            upper=UPPER,
        )
    with pytest.raises(ValueError):
        candidate_facts.candidate_coverage_counts(
            db, horizon="2h", lower=LOWER, upper=UPPER
        )
