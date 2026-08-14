from __future__ import annotations

import base64
import json
import sqlite3
import zlib

import aletheia.analytics.candidate_facts as candidate_facts


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE filter_decisions (
            id TEXT PRIMARY KEY, candidate_key TEXT, candidate_kind TEXT,
            outcome_candidate_key TEXT, opportunity_id TEXT, proposal_id TEXT,
            filter_name TEXT, stage TEXT, sequence_no INTEGER, decision TEXT,
            reason_code TEXT, evaluated_at TEXT
        );
        CREATE TABLE filter_decision_batches (
            id TEXT PRIMARY KEY, run_id TEXT, candidate_kind TEXT, filter_name TEXT,
            stage TEXT, candidate_facts TEXT, filter_version TEXT, code_commit TEXT,
            evaluated_at TEXT
        );
        CREATE TABLE filter_candidate_outcomes (
            id TEXT PRIMARY KEY, outcome_candidate_key TEXT, horizon TEXT,
            entry_ts TEXT, settled_at TEXT, status TEXT, net_return_pct REAL
        );
        CREATE TABLE opportunities (id TEXT PRIMARY KEY, created_at TEXT);
        CREATE TABLE pi_decisions (
            id TEXT PRIMARY KEY, opportunity_id TEXT, proposal_id TEXT,
            action TEXT, created_at TEXT
        );
        CREATE TABLE trade_proposals (
            id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT
        );
        CREATE TABLE risk_decisions (
            id TEXT PRIMARY KEY, proposal_id TEXT, approved INTEGER,
            decision_role TEXT, created_at TEXT
        );
        CREATE TABLE orders (
            id TEXT PRIMARY KEY, proposal_id TEXT, risk_decision_id TEXT,
            position_id TEXT, close_reason TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE positions (id TEXT PRIMARY KEY, opened_at TEXT);
        """
    )
    exposures = [
        ("filter-traded", "candidate-traded", "outcome-traded", "opp-traded", "proposal-traded"),
        ("filter-skip", "candidate-skip", "outcome-skip", "opp-skip", None),
        ("filter-risk", "candidate-risk", "outcome-risk", "opp-risk", "proposal-risk"),
    ]
    for index, (identity, candidate_key, outcome_key, opportunity_id, proposal_id) in enumerate(exposures):
        db.execute(
            "INSERT INTO filter_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                candidate_key,
                "opportunity",
                outcome_key,
                opportunity_id,
                proposal_id,
                "ranker",
                "selection",
                index,
                "passed",
                "eligible",
                f"2026-08-13T00:0{index}:00+00:00",
            ),
        )
        db.execute(
            "INSERT INTO opportunities VALUES (?,?)",
            (opportunity_id, f"2026-08-13T00:0{index}:01+00:00"),
        )
    db.execute(
        "INSERT INTO filter_decision_batches VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "batch-filtered",
            "run-1",
            "universe_member",
            "liquidity",
            "pre_opportunity",
            json.dumps(
                [
                    {
                        "candidate_key": "candidate-filtered",
                        "outcome_candidate_key": "outcome-filtered",
                        "decision": "rejected",
                        "reason_code": "spread_too_wide",
                    }
                ]
            ),
            "liquidity-v1",
            "commit-a",
            "2026-08-13T00:00:30+00:00",
        ),
    )
    db.execute(
        "INSERT INTO pi_decisions VALUES "
        "('pi-traded','opp-traded','proposal-traded','trade','2026-08-13T00:00:10+00:00')"
    )
    db.execute("INSERT INTO pi_decisions VALUES ('pi-skip','opp-skip',NULL,'skip','2026-08-13T00:01:10+00:00')")
    db.execute(
        "INSERT INTO pi_decisions VALUES ('pi-risk','opp-risk','proposal-risk','trade','2026-08-13T00:02:10+00:00')"
    )
    db.execute("INSERT INTO trade_proposals VALUES ('proposal-traded','opp-traded','2026-08-13T00:00:20+00:00')")
    db.execute("INSERT INTO trade_proposals VALUES ('proposal-risk','opp-risk','2026-08-13T00:02:20+00:00')")
    db.execute(
        "INSERT INTO risk_decisions VALUES "
        "('risk-traded','proposal-traded',1,'authoritative','2026-08-13T00:00:30+00:00')"
    )
    db.execute(
        "INSERT INTO risk_decisions VALUES "
        "('risk-reject','proposal-risk',0,'authoritative','2026-08-13T00:02:30+00:00')"
    )
    db.execute(
        "INSERT INTO orders VALUES "
        "('order-traded','proposal-traded','risk-traded','position-traded',NULL,'filled',"
        "'2026-08-13T00:00:40+00:00')"
    )
    db.execute("INSERT INTO positions VALUES ('position-traded','2026-08-13T00:00:50+00:00')")
    for index, key in enumerate(("outcome-traded", "outcome-skip", "outcome-risk", "outcome-filtered")):
        settled_at = "2026-08-14T00:00:00+00:00" if key != "outcome-filtered" else "2026-08-15T00:00:00+00:00"
        db.execute(
            "INSERT INTO filter_candidate_outcomes VALUES (?,?,?,?,?,?,?)",
            (
                f"fixed-{index}",
                key,
                "24h",
                "2026-08-13T00:00:00+00:00",
                settled_at,
                "settled",
                float(index),
            ),
        )
    db.commit()
    return db


def test_candidate_facts_preserve_traded_skip_reject_and_filtered_units():
    db = _db()
    before = db.total_changes

    report = candidate_facts.build_candidate_facts(horizon="24h", as_of_at="2026-08-14T12:00:00+00:00", db=db)

    assert db.total_changes == before
    facts = {fact["fact_id"]: fact for fact in report["facts"]}
    assert report["candidate_count"] == 4
    assert facts["outcome-traded"]["terminal_state"] == "traded"
    assert facts["outcome-traded"]["identity"]["position_id"] == "position-traded"
    assert facts["outcome-skip"]["terminal_state"] == "pi_skip"
    assert facts["outcome-skip"]["identity"]["proposal_id"] is None
    assert facts["outcome-risk"]["terminal_state"] == "risk_reject"
    assert facts["outcome-risk"]["identity"]["order_id"] is None
    assert facts["outcome-filtered"]["terminal_state"] == "filtered"
    assert facts["outcome-filtered"]["identity"]["opportunity_id"] is None
    assert facts["outcome-filtered"]["fixed_horizon_outcome"] is None
    assert all(fact["variable_horizon_position_outcome"] is None for fact in facts.values())
    assert report["settled_outcome_count"] == 3
    assert report["outcome_coverage"] == 0.75


def test_candidate_facts_reject_variable_horizon():
    db = _db()

    try:
        candidate_facts.build_candidate_facts(horizon="realized_close", db=db)
    except ValueError as exc:
        assert str(exc) == "unsupported fixed horizon: realized_close"
    else:
        raise AssertionError("variable horizon was accepted")


def test_candidate_facts_decode_current_compressed_batches():
    db = _db()
    raw = json.dumps(
        [
            {
                "candidate_key": "compressed-filtered",
                "outcome_candidate_key": "compressed-outcome",
                "decision": "rejected",
            }
        ],
        separators=(",", ":"),
    ).encode()
    payload = "zlib-base64:" + base64.b64encode(zlib.compress(raw)).decode()
    db.execute(
        "UPDATE filter_decision_batches SET candidate_facts=? WHERE id='batch-filtered'",
        (payload,),
    )

    report = candidate_facts.build_candidate_facts(horizon="24h", as_of_at="2026-08-14T12:00:00+00:00", db=db)

    fact = next(item for item in report["facts"] if item["fact_id"] == "compressed-outcome")
    assert fact["terminal_state"] == "filtered"


def test_candidate_facts_preserve_malformed_plain_json_as_unavailable_evidence():
    db = _db()
    db.execute(
        "UPDATE filter_decision_batches SET candidate_facts=? WHERE id='batch-filtered'",
        ('{"not": valid json',),
    )

    report = candidate_facts.build_candidate_facts(
        horizon="24h", as_of_at="2026-08-14T12:00:00+00:00", db=db
    )

    assert report["candidate_count"] == 3
    assert report["unavailable_count"] == 1
    assert report["unavailable_reasons"] == {"malformed_candidate_facts_json": 1}
    assert report["exclusions"] == [
        {
            "source_table": "filter_decision_batches",
            "source_batch_id": "batch-filtered",
            "event_at": "2026-08-13T00:00:30+00:00",
            "availability": "unavailable",
            "unavailable_reason": "malformed_candidate_facts_json",
        }
    ]


def test_candidate_facts_reject_decoded_batch_count_mismatch():
    db = _db()
    db.execute("ALTER TABLE filter_decision_batches ADD COLUMN candidate_count INTEGER")
    db.execute(
        "UPDATE filter_decision_batches SET candidate_count=?, candidate_facts=? WHERE id=?",
        (
            2,
            json.dumps(
                {
                    "fields": ["candidate_key"],
                    "rows": [[1, "decoded"], [1]],
                }
            ),
            "batch-filtered",
        ),
    )

    report = candidate_facts.build_candidate_facts(
        horizon="24h", as_of_at="2026-08-14T12:00:00+00:00", db=db
    )

    assert report["unavailable_count"] == 1
    assert report["unavailable_reasons"] == {"candidate_fact_count_mismatch": 1}
    assert report["exclusions"] == [
        {
            "source_table": "filter_decision_batches",
            "source_batch_id": "batch-filtered",
            "event_at": "2026-08-13T00:00:30+00:00",
            "availability": "unavailable",
            "unavailable_reason": "candidate_fact_count_mismatch",
            "declared_candidate_count": 2,
            "decoded_candidate_count": 1,
        }
    ]


def test_candidate_exact_pi_identity_does_not_join_other_retry():
    db = _db()
    db.execute(
        "UPDATE filter_decisions SET candidate_key='pi_decision:pi-skip', "
        "candidate_kind='pi_decision' WHERE id='filter-skip'"
    )
    db.execute(
        "INSERT INTO pi_decisions VALUES ('pi-retry','opp-skip','proposal-risk','trade','2026-08-13T00:01:11+00:00')"
    )

    report = candidate_facts.build_candidate_facts(horizon="24h", as_of_at="2026-08-14T12:00:00+00:00", db=db)

    fact = next(item for item in report["facts"] if item["fact_id"] == "outcome-skip")
    assert fact["identity"]["pi_decision_id"] == "pi-skip"
    assert fact["terminal_state"] == "pi_skip"


def test_candidate_facts_resolve_serenity_opportunity_by_decision_source():
    db = _db()
    db.execute("ALTER TABLE pi_decisions ADD COLUMN signal_source TEXT")
    db.execute("CREATE TABLE serenity_opportunities (id TEXT PRIMARY KEY, created_at TEXT)")
    db.execute("INSERT INTO serenity_opportunities VALUES ('serenity-1','2026-08-13T00:03:01+00:00')")
    db.execute(
        "INSERT INTO filter_decisions VALUES "
        "('serenity-filter','pi_decision:serenity-decision','pi_decision',"
        "'serenity-outcome','serenity-1',NULL,'operator','decision',0,'passed',"
        "'eligible','2026-08-13T00:03:00+00:00')"
    )
    db.execute(
        "INSERT INTO pi_decisions "
        "(id,opportunity_id,proposal_id,action,created_at,signal_source) VALUES "
        "('serenity-decision','serenity-1',NULL,'skip',"
        "'2026-08-13T00:03:10+00:00','serenity')"
    )

    report = candidate_facts.build_candidate_facts(horizon="24h", as_of_at="2026-08-14T12:00:00+00:00", db=db)

    fact = next(item for item in report["facts"] if item["fact_id"] == "serenity-outcome")
    assert fact["funnel"]["opportunity_reached"] is True
    assert fact["identity"]["pi_decision_id"] == "serenity-decision"
