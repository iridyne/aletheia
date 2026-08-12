from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

import pytest


def _now(minutes: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _seed_opportunity(db, oid: str, member_id: str | None = None) -> None:
    db.execute(
        "INSERT INTO opportunities (id,symbol,market,direction_bias,confidence,created_at,universe_member_id) "
        "VALUES (?, 'BTC/USDT','spot','long','high',?,?)",
        (oid, _now(), member_id),
    )


def _seed_member(db, member_id: str, *, penalty=None, flags=None) -> None:
    db.execute(
        "INSERT INTO universe_registry (id,venue,symbol,market_type,status) "
        "VALUES (?,'bitget',?,'spot','active')",
        (f"asset-{member_id}", f"{member_id}/USDT"),
    )
    db.execute("INSERT INTO universe_snapshots (id) VALUES (?)", (f"snap-{member_id}",))
    db.execute(
        "INSERT INTO universe_members (id,snapshot_id,asset_id,symbol,rank,total_score,expires_at,late_entry_penalty,risk_flags) "
        "VALUES (?,?,?,?,1,1,?,?,?)",
        (member_id, f"snap-{member_id}", f"asset-{member_id}", f"{member_id}/USDT", _now(60), penalty, json.dumps(flags) if flags is not None else None),
    )


def _shadow(db, sid: str | None, oid: str, key: str | None, *, score: float = 50, rank: int = 1, symbol: str = "BTC/USDT", venue: str | None = "bitget", market: str = "spot", direction: str = "long", family: str = "momentum", epoch: str | None = "epoch-a", batch: str | None = "batch-a", minute: int = 0, cost_model: str | None = "v1", entry_price=100) -> None:
    db.execute(
        "INSERT INTO shadow_trades (id,batch_id,opportunity_id,symbol,venue,market_type,direction,scanner_family,trade_score,rank,outcome_candidate_key,policy_epoch_id,created_at,cost_model_version,entry_price) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, batch, oid, symbol, venue, market, direction, family, score, rank, key, epoch, _now(minute), cost_model, entry_price),
    )


def _outcome(db, key: str, horizon: str, net, *, status: str = "settled", venue: str | None = "bitget", symbol: str | None = "BTC/USDT", market: str | None = "spot", direction: str | None = "long", entry_minute: float = -0.5, cost_model: str | None = "v1", entry_price=100) -> None:
    db.execute(
        "INSERT INTO filter_candidate_outcomes (id,outcome_candidate_key,venue,symbol,market_type,direction,horizon,entry_price_usd,entry_ts,due_at,settled_at,net_return_pct,cost_model_version,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"out-{key}-{horizon}", key, venue, symbol, market, direction, horizon, entry_price, _now(entry_minute), _now(-0.1), _now() if status.lower() == "settled" else None, net, cost_model, status),
    )


def _decision(db, index: int, oid: str, key: str, reason: str, minute: int = 0) -> None:
    db.execute(
        "INSERT INTO filter_decisions (id,dedup_key,candidate_key,candidate_kind,opportunity_id,symbol,market_type,direction,filter_name,stage,decision,reason_code,filter_version,outcome_candidate_key,evaluated_at) "
        "VALUES (?,?,?,?,?,'BTC/USDT','spot','long','paper_trader','paper_trader_selection','rejected',?,'v1',?,?)",
        (f"fd-{index}", f"dedup-{index}", f"candidate-{index}", "opportunity", oid, reason, key, _now(minute)),
    )


def test_variants_deduplicate_candidates_and_setups_deterministically():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for oid in ("opp-1", "opp-2", "opp-3"):
        _seed_opportunity(db, oid)
    _shadow(db, "shadow-a", "opp-1", "key-1", score=70, rank=2, minute=-2)
    _shadow(db, "shadow-b", "opp-1", "key-1", score=60, rank=1, minute=-1)
    _shadow(db, "shadow-c", "opp-2", "key-2", score=80, rank=3)
    _shadow(db, "shadow-d", "opp-3", "key-3", score=80, rank=1)
    _outcome(db, "key-1", "15m", 1.0)
    _outcome(db, "key-2", "15m", 2.0)
    _outcome(db, "key-3", "15m", 3.0)
    db.commit()

    report = build_ranker_objective_audit(hours=24)

    assert report["variants"]["current_persisted_ranking"]["sample_count"] == 4
    assert report["populations"]["candidate_exposure_deduplicated"]["sample_count"] == 3
    setup = report["populations"]["setup_exposure_deduplicated"]
    assert setup["sample_count"] == 1
    assert setup["representative_ids"] == ["shadow-d"]  # score, then persisted rank


def test_headlines_and_setup_dedup_never_pool_policy_epochs_or_batches():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-a")
    _seed_opportunity(db, "opp-b")
    _shadow(db, "a", "opp-a", "key-a", epoch="epoch-a", batch="batch-a")
    _shadow(db, "b", "opp-b", "key-b", epoch="epoch-b", batch="batch-b")
    _outcome(db, "key-a", "15m", 10)
    _outcome(db, "key-b", "15m", -10)
    db.commit()

    report = build_ranker_objective_audit(hours=24)
    setup = report["populations"]["setup_exposure_deduplicated"]

    assert setup["sample_count"] == 2
    assert "horizons" not in setup  # no pooled headline conclusion
    assert set(setup["by_policy_epoch"]) == {"epoch-a", "epoch-b"}
    assert setup["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]["horizons"]["15m"]["mean_net_return_pct"] == 10
    assert setup["by_policy_epoch"]["epoch-b"]["by_batch"]["batch-b"]["horizons"]["15m"]["mean_net_return_pct"] == -10


def test_same_epoch_distinct_batches_keep_separate_objective_comparisons():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-a")
    _seed_opportunity(db, "opp-b")
    _shadow(db, "a", "opp-a", "key-a", epoch="epoch-a", batch="batch-a")
    _shadow(db, "b", "opp-b", "key-b", epoch="epoch-a", batch="batch-b")
    _outcome(db, "key-a", "15m", 10)
    _outcome(db, "key-b", "15m", -10)
    db.commit()

    epoch = build_ranker_objective_audit(hours=24)["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]

    assert "horizons" not in epoch
    assert set(epoch["by_batch"]) == {"batch-a", "batch-b"}
    assert epoch["by_batch"]["batch-a"]["horizons"]["15m"]["mean_net_return_pct"] == 10
    assert epoch["by_batch"]["batch-b"]["horizons"]["15m"]["mean_net_return_pct"] == -10


def test_outcomes_epochs_strata_and_unavailable_facts_are_honest():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_member(db, "member-late", penalty=5)
    _seed_opportunity(db, "opp-a", "member-late")
    _seed_opportunity(db, "opp-b")
    _shadow(db, "a", "opp-a", "key-a", market="spot", direction="long", family="mom", epoch="epoch-a")
    _shadow(db, "b", "opp-b", "key-b", market="usdt_futures", direction="short", family="breakout", epoch=None)
    for horizon, net in (("15m", 1), ("1h", 2), ("4h", None), ("24h", 4)):
        _outcome(db, "key-a", horizon, net)
    _outcome(db, "key-b", "15m", 9, status="pending")
    db.commit()

    report = build_ranker_objective_audit(hours=24)
    variant = report["variants"]["current_persisted_ranking"]

    epoch_a = variant["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]
    assert epoch_a["horizons"]["15m"]["settled_count"] == 1
    assert epoch_a["horizons"]["4h"]["settled_count"] == 0
    assert epoch_a["outcome_states"]["4h"] == {"null": 1}
    assert "legacy_or_unavailable" not in variant["by_policy_epoch"]
    epochs = {s["value"] for s in report["strata"] if s["dimension"] == "policy_epoch"}
    assert epochs == {"epoch-a"}
    assert {s["value"] for s in report["strata"] if s["dimension"] == "direction"} == {"long"}
    assert report["batch_comparison_exclusions"]["reason_counts"] == {"missing_policy_epoch_id": 1}
    assert report["availability"]["late_entry"] == {"available": 1, "unavailable": 1}
    assert report["availability"]["queue_constraint"]["unavailable"] == 2
    assert report["status"] == "partial_data"  # legacy epoch and missing dimensions


def test_candidate_population_dedup_is_scoped_to_epoch_not_repeated_batch_exposure():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for oid in ("opp-a", "opp-b", "opp-c"):
        _seed_opportunity(db, oid)
    _shadow(db, "a", "opp-a", "shared-key", epoch="epoch-a", batch="batch-a")
    _shadow(db, "b", "opp-b", "shared-key", epoch="epoch-b", batch="batch-a")
    _shadow(db, "c", "opp-c", "shared-key", epoch="epoch-a", batch="batch-b")
    db.commit()

    report = build_ranker_objective_audit(hours=24)

    assert report["populations"]["candidate_exposure_deduplicated"]["sample_count"] == 2


def test_conflicting_settled_outcomes_are_excluded_deterministically():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-conflict")
    _shadow(db, "shadow-conflict", "opp-conflict", "key-conflict")
    db.execute("DROP TABLE filter_candidate_outcomes")
    db.execute(
        "CREATE TABLE filter_candidate_outcomes (outcome_candidate_key TEXT,horizon TEXT,"
        "status TEXT,net_return_pct REAL,settled_at TEXT,venue TEXT,symbol TEXT,market_type TEXT,"
        "direction TEXT,entry_ts TEXT,cost_model_version TEXT,entry_price_usd REAL)"
    )
    db.executemany(
        "INSERT INTO filter_candidate_outcomes VALUES "
        "('key-conflict','15m','settled',?,?,'bitget','BTC/USDT','spot','long',?,'v1',100)",
        [(1.0, _now(), _now(-0.5)), (-1.0, _now(), _now(-0.5))],
    )
    db.commit()

    epoch = build_ranker_objective_audit(hours=24)["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]

    assert epoch["outcome_states"]["15m"] == {"conflict": 1}
    assert epoch["horizons"]["15m"]["settled_count"] == 0


def test_mixed_numeric_and_null_settled_outcomes_conflict():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-mixed")
    _shadow(db, "shadow-mixed", "opp-mixed", "key-mixed")
    db.execute("DROP TABLE filter_candidate_outcomes")
    db.execute(
        "CREATE TABLE filter_candidate_outcomes (outcome_candidate_key TEXT,horizon TEXT,"
        "status TEXT,net_return_pct REAL,settled_at TEXT,venue TEXT,symbol TEXT,market_type TEXT,"
        "direction TEXT,entry_ts TEXT,cost_model_version TEXT,entry_price_usd REAL)"
    )
    db.executemany(
        "INSERT INTO filter_candidate_outcomes VALUES "
        "('key-mixed','15m','settled',?,?,'bitget','BTC/USDT','spot','long',?,'v1',100)",
        [(1.0, _now(), _now(-0.5)), (None, _now(), _now(-0.5))],
    )
    db.commit()

    epoch = build_ranker_objective_audit(hours=24)["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]
    assert epoch["outcome_states"]["15m"] == {"conflict": 1}


def test_outcome_linkage_fails_closed_on_identity_cost_and_entry_alignment():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cases: tuple[tuple[str, dict[str, Any], dict[str, Any]], ...] = (
        ("missing-venue", {"venue": None}, {}),
        ("symbol-mismatch", {}, {"symbol": "ETH/USDT"}),
        ("market-mismatch", {"market": "usdt_futures"}, {"market": "spot"}),
        ("direction-mismatch", {}, {"direction": "short"}),
        ("missing-cost", {}, {"cost_model": None}),
        ("entry-misaligned", {}, {"entry_minute": -2}),
    )
    for name, shadow_kwargs, outcome_kwargs in cases:
        _seed_opportunity(db, f"opp-{name}")
        _shadow(db, name, f"opp-{name}", f"key-{name}", **shadow_kwargs)
        _outcome(db, f"key-{name}", "15m", 10, **outcome_kwargs)
    db.commit()

    report = build_ranker_objective_audit(hours=24)
    batch = report["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]

    assert batch["horizons"]["15m"]["settled_count"] == 0
    assert report["outcome_linkage_exclusions"] == {
        "entry_alignment_exceeded": 1,
        "instrument_identity_mismatch": 3,
        "missing_cost_model_identity": 1,
        "missing_instrument_identity": 1,
    }

def test_outcome_linkage_requires_exact_finite_entry_price():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-price")
    _shadow(db, "shadow-price", "opp-price", "key-price", entry_price=100.0)
    _outcome(db, "key-price", "15m", 3.0, entry_price=100.0000000001)
    report = build_ranker_objective_audit()
    assert report["outcome_linkage_exclusions"]["entry_price_mismatch"] == 1
@pytest.mark.parametrize("value", ["not-a-number", "NaN", "Infinity"])
def test_malformed_or_nonfinite_net_returns_fail_closed(value):
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, f"opp-{value}")
    _shadow(db, f"shadow-{value}", f"opp-{value}", f"key-{value}")
    _outcome(db, f"key-{value}", "15m", value)
    report = build_ranker_objective_audit()
    batch = report["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]
    assert batch["outcome_states"]["15m"] == {"conflict": 1}
    assert batch["horizons"]["15m"]["settled_count"] == 0



@pytest.mark.parametrize("settled_at", [None, "", "not-a-timestamp"])
def test_settled_outcome_requires_parseable_settlement_timestamp(settled_at):
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-settlement-time")
    _shadow(db, "shadow-settlement-time", "opp-settlement-time", "key-settlement-time")
    _outcome(db, "key-settlement-time", "15m", 2.0)
    db.execute(
        "UPDATE filter_candidate_outcomes SET settled_at=? "
        "WHERE outcome_candidate_key='key-settlement-time'",
        (settled_at,),
    )
    db.commit()

    report = build_ranker_objective_audit()
    batch = report["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]
    assert batch["outcome_states"]["15m"] == {"invalid_settled_at": 1}
    assert batch["horizons"]["15m"]["settled_count"] == 0
    assert report["outcome_linkage_exclusions"] == {"invalid_settled_at": 1}


@pytest.mark.parametrize(
    ("ranked_market", "outcome_market"),
    [("cash", "spot"), ("spot", "options"), ("cash", "options")],
)
def test_outcome_linkage_rejects_unsupported_market_types(
    ranked_market, outcome_market
):
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-unsupported-market")
    _shadow(
        db,
        "shadow-unsupported-market",
        "opp-unsupported-market",
        "key-unsupported-market",
        market=ranked_market,
    )
    _outcome(
        db,
        "key-unsupported-market",
        "15m",
        2.0,
        market=outcome_market,
    )
    db.commit()

    report = build_ranker_objective_audit()
    batch = report["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]
    assert batch["horizons"]["15m"]["settled_count"] == 0
    assert report["outcome_linkage_exclusions"] == {"unsupported_market_type": 1}


@pytest.mark.parametrize("penalty", ["malformed", "NaN", "Infinity", "-Infinity"])
def test_malformed_or_nonfinite_late_entry_penalty_is_unavailable(penalty):
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_member(db, "member-invalid-penalty", penalty=penalty)
    _seed_opportunity(db, "opp-invalid-penalty", "member-invalid-penalty")
    _shadow(
        db,
        "shadow-invalid-penalty",
        "opp-invalid-penalty",
        "key-invalid-penalty",
    )
    db.commit()

    report = build_ranker_objective_audit()
    assert report["availability"]["late_entry"] == {"available": 0, "unavailable": 1}
    assert {
        item["value"]
        for item in report["strata"]
        if item["dimension"] == "late_entry_status"
    } == {"unavailable"}

def test_earliest_aligned_first_exposure_is_the_only_outcome_sample():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-repeat")
    _shadow(db, "too-early", "opp-repeat", "key-repeat", minute=-2, batch="old")
    _shadow(db, "first-aligned", "opp-repeat", "key-repeat", minute=0, batch="first")
    _shadow(db, "later-repeat", "opp-repeat", "key-repeat", minute=0, batch="later")
    _outcome(db, "key-repeat", "15m", 4, entry_minute=-0.5)
    db.commit()

    report = build_ranker_objective_audit(hours=24)
    variant = report["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]

    assert variant["by_batch"]["first"]["horizons"]["15m"]["settled_count"] == 1
    assert variant["by_batch"]["old"]["horizons"]["15m"]["settled_count"] == 0
    assert variant["by_batch"]["later"]["horizons"]["15m"]["settled_count"] == 0
    assert report["outcome_linkage_exclusions"]["entry_alignment_exceeded"] == 1
    assert report["outcome_linkage_exclusions"]["repeated_exposure"] == 1


def test_admission_reasons_are_separate_and_first_exposure_wins():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    reasons = ["duplicate_symbol", "open_position_duplicate", "active_operator_carrier", "opposing_direction_queued", "signal_cooldown", "signal_queue_capacity", "signal_batch_limit"]
    for index, reason in enumerate(reasons):
        oid, key = f"opp-{index}", f"key-{index}"
        _seed_opportunity(db, oid)
        _shadow(db, f"shadow-{index}", oid, key, symbol=f"S{index}/USDT")
        _decision(db, index, oid, key, reason)
    _decision(db, 99, "opp-0", "key-0", "signal_queue_capacity", minute=1)
    db.commit()

    report = build_ranker_objective_audit(hours=24)

    summary = report["candidate_admission_summary"]
    assert summary["reason_counts"] == {reason: 1 for reason in sorted(reasons)}
    assert summary["authoritative_batch_linkage"] is False
    assert summary["batch_linkage"] == "unavailable"
    assert report["admission_effects"] == {}
    assert not any(s["value"] == "duplicate_symbol" for s in report["strata"] if s["dimension"] == "duplicate_status")


def test_admission_linkage_does_not_guess_between_persisted_exposures():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-shared")
    _shadow(db, "a", "opp-shared", "same-key", batch="batch-a")
    _shadow(db, "b", "opp-shared", "same-key", batch="batch-b")
    _decision(db, 1, "opp-shared", "same-key", "duplicate_symbol")
    db.commit()

    report = build_ranker_objective_audit(hours=24)
    assert report["candidate_admission_summary"]["reason_counts"]["duplicate_symbol"] == 1
    assert report["candidate_admission_summary"]["batch_linkage"] == "unavailable"
    assert report["availability"]["queue_constraint"] == {"available": 0, "unavailable": 2}


def test_outcome_status_is_case_normalized():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-case")
    _shadow(db, "case", "opp-case", "key-case")
    _outcome(db, "key-case", "15m", 2.5, status="SETTLED")
    db.execute("UPDATE filter_candidate_outcomes SET settled_at=?", (_now(),))
    db.commit()

    batch = build_ranker_objective_audit(hours=24)["variants"]["current_persisted_ranking"]["by_policy_epoch"]["epoch-a"]["by_batch"]["batch-a"]
    assert batch["outcome_states"]["15m"] == {"settled": 1}
    assert batch["horizons"]["15m"]["mean_net_return_pct"] == 2.5


def test_partial_related_schemas_degrade_only_affected_dimensions():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-partial")
    _shadow(db, "partial", "opp-partial", "key-partial")
    db.execute("DROP TABLE filter_decisions")
    db.execute("CREATE TABLE filter_decisions (id TEXT, stage TEXT)")
    db.commit()

    report = build_ranker_objective_audit(hours=24)
    assert report["variants"]["current_persisted_ranking"]["sample_count"] == 1
    assert report["availability"]["trade_score"] == {"available": 1, "unavailable": 0}
    assert report["availability"]["queue_constraint"] == {"available": 0, "unavailable": 1}
    assert report["status"] != "ok"
    assert report["persisted_window_dimensions_available"] is False


def test_limit_cut_through_excludes_entire_persisted_membership_window_boundary_batch_and_reports_it():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for index in range(3):
        _seed_opportunity(db, f"opp-cut-{index}")
        _shadow(db, f"cut-{index}", f"opp-cut-{index}", f"key-cut-{index}", batch="cut-batch")
    _seed_opportunity(db, "opp-whole")
    _shadow(db, "whole", "opp-whole", "key-whole", batch="whole-batch", minute=1)
    db.commit()

    report = build_ranker_objective_audit(hours=24, limit=2)

    assert report["persisted_membership_window_boundary_exclusions"] == {
        "batch_count": 1,
        "batch_ids": ["cut-batch"],
        "excluded_loaded_row_count": 1,
    }
    assert report["variants"]["current_persisted_ranking"]["representative_ids"] == ["whole"]
    assert report["exclusions"]["persisted_membership_cut_through_rows"] == 1


def test_lookback_cut_through_excludes_batch_with_omitted_pre_cutoff_members():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-recent")
    _shadow(db, "recent", "opp-recent", "key-recent", batch="cutoff-batch")
    _seed_opportunity(db, "opp-old")
    _shadow(db, "old", "opp-old", "key-old", batch="cutoff-batch", minute=-(25 * 60))
    _seed_opportunity(db, "opp-whole")
    _shadow(db, "whole", "opp-whole", "key-whole", batch="whole-batch")
    db.commit()

    report = build_ranker_objective_audit(hours=24, limit=10)

    assert report["truncated"] is False
    assert report["persisted_membership_window_boundary_exclusions"] == {
        "batch_count": 1,
        "batch_ids": ["cutoff-batch"],
        "excluded_loaded_row_count": 1,
    }
    assert report["variants"]["current_persisted_ranking"]["representative_ids"] == ["whole"]
    assert report["exclusions"]["persisted_membership_cut_through_rows"] == 1


def test_missing_required_batch_identity_fields_fail_comparison_closed():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    missing_cases: tuple[tuple[str, dict[str, Any]], ...] = (
        ("missing-batch", {"batch": None}),
        ("missing-epoch", {"epoch": None}),
    )
    for name, kwargs in missing_cases:
        _seed_opportunity(db, f"opp-{name}")
        _shadow(db, name, f"opp-{name}", f"key-{name}", **kwargs)
    _seed_opportunity(db, "missing-id")
    _shadow(db, None, "missing-id", "key-missing-id")
    _seed_opportunity(db, "missing-time")
    _shadow(db, "missing-time", "missing-time", "key-missing-time")
    db.execute("UPDATE shadow_trades SET created_at='' WHERE id='missing-time'")
    _seed_opportunity(db, "invalid-time")
    _shadow(db, "invalid-time", "invalid-time", "key-invalid-time")
    db.execute("UPDATE shadow_trades SET created_at='not-a-time' WHERE id='invalid-time'")
    db.commit()

    report = build_ranker_objective_audit(hours=24)

    assert report["variants"]["current_persisted_ranking"]["sample_count"] == 0
    assert report["batch_comparison_exclusions"]["reason_counts"] == {
        "invalid_required_time": 1,
        "missing_batch_id": 1,
        "missing_policy_epoch_id": 1,
        "missing_required_time": 1,
        "missing_row_id": 1,
    }
    assert report["batch_comparison_exclusions"]["row_ids"] == [
        "invalid-time", "missing-batch", "missing-epoch", "missing-time", "unavailable"
    ]


def test_status_describes_persisted_window_only_and_never_grants_action_or_promotion():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_member(db, "member-complete", penalty=0)
    _seed_opportunity(db, "opp-complete", "member-complete")
    _shadow(db, "complete", "opp-complete", "key-complete")
    _decision(db, 1, "opp-complete", "key-complete", "duplicate_symbol")
    _outcome(db, "key-complete", "15m", 1)
    _seed_member(db, "member-extra", penalty=0)
    _seed_opportunity(db, "opp-extra", "member-extra")
    _shadow(db, "extra", "opp-extra", "key-extra")
    _decision(db, 2, "opp-extra", "key-extra", "duplicate_symbol")
    db.commit()

    report = build_ranker_objective_audit(hours=24, limit=10)
    assert report["status"] == "partial_data"
    assert report["candidate_admission_summary"]["batch_linkage"] == "unavailable"
    assert report["intended_batch_completeness"] == "unavailable_on_current_schema"
    assert report["writer_atomicity"] is False
    assert report["within_batch_metrics_scope"] == "descriptive_over_persisted_members_only"
    batch = report["variants"]["current_persisted_ranking"]["by_policy_epoch"][
        "epoch-a"
    ]["by_batch"]["batch-a"]
    assert batch["metric_scope"] == "descriptive_over_persisted_members_only"
    assert report["actionable"] is False
    assert report["promotion_eligible"] is False

    truncated = build_ranker_objective_audit(hours=24, limit=1)
    assert truncated["truncated"] is True
    assert truncated["status"] == "partial_data"
    assert truncated["actionable"] is False
    assert truncated["promotion_eligible"] is False


def test_score_counterfactual_is_unavailable_without_durable_components():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_opportunity(db, "opp-score")
    _shadow(db, "score", "opp-score", "key-score")
    db.commit()

    report = build_ranker_objective_audit(hours=24)

    assert report["variants"]["current_persisted_ranking"]["available"] is True
    counterfactual = report["variants"]["deduplicated_score_counterfactual"]
    assert counterfactual["available"] is False
    assert "component" in counterfactual["reason"]


@pytest.mark.real_db
def test_file_backed_audit_does_not_create_or_modify_db_or_wal(tmp_path, monkeypatch):
    import aletheia.storage.sqlite as store
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit

    path = tmp_path / "audit.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE shadow_trades (id TEXT PRIMARY KEY,batch_id TEXT,opportunity_id TEXT,"
        "symbol TEXT,market_type TEXT,direction TEXT,scanner_family TEXT,trade_score REAL,"
        "rank INTEGER,was_selected INTEGER,created_at TEXT,resolved_at TEXT,"
        "outcome_candidate_key TEXT,policy_epoch_id TEXT)"
    )
    db.execute(
        "INSERT INTO shadow_trades VALUES "
        "('s','b','o','BTC/USDT','spot','long','mom',1,1,0,?,NULL,NULL,'e')",
        (_now(),),
    )
    db.commit()
    db.close()
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(store, "_injected_db_active", True)
    monkeypatch.setattr(store, "_injected_db_path", path)

    report = build_ranker_objective_audit(hours=24)

    assert report["read_only"] is True
    assert report["query_only_enforced"] is True
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()


def test_injected_connection_query_only_state_is_restored():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    assert db.execute("PRAGMA query_only").fetchone()[0] == 0

    build_ranker_objective_audit(hours=24)

    assert db.execute("PRAGMA query_only").fetchone()[0] == 0
    db.execute("CREATE TABLE query_only_restoration_probe (id INTEGER)")


def test_production_shadow_and_canonical_outcome_writers_become_audit_eligible(monkeypatch):
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.counterfactual import trades
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    monkeypatch.setattr(trades, "_current_price", lambda *args, **kwargs: 100.0)
    monkeypatch.setattr(trades, "_current_regime", lambda: "normal")
    monkeypatch.setattr(trades, "_current_policy_epoch", lambda: "epoch-production")
    candidate = {
        "opportunity_id": "opp-production",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "scanner_family": "momentum",
        "trade_score": 80.0,
    }

    shadow_ids = trades.persist_shadow_batch([candidate], candidate, top_n=1)
    assert len(shadow_ids) == 1
    db = get_db()
    shadow = db.execute(
        "SELECT * FROM shadow_trades WHERE id=?", (shadow_ids[0],)
    ).fetchone()
    assert shadow["cost_model_version"] is None  # rank-time writer has no settled economics
    key = str(shadow["outcome_candidate_key"])
    entry_ts = str(shadow["created_at"])
    record_filter_decision(
        candidate_key=key,
        candidate_kind="opportunity",
        filter_name="paper_trader",
        stage="paper_trader_selection",
        decision="passed",
        reason_code="selected",
        symbol=str(shadow["symbol"]),
        market_type=str(shadow["market_type"]),
        opportunity_id=str(shadow["opportunity_id"]),
        venue=str(shadow["venue"]),
        direction=str(shadow["direction"]),
        scanner_family=str(shadow["scanner_family"]),
        evaluated_at=entry_ts,
        outcome_candidate_key=key,
        entry_price_usd=100.0,
        entry_ts=entry_ts,
        raise_on_error=True,
    )
    outcome = db.execute(
        "SELECT * FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon='15m'",
        (key,),
    ).fetchone()
    economics = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=101.0,
        direction="long",
        market_type="spot",
        hold_seconds=15 * 60,
        entry_spread_bps=0.0,
        fallback_spread_bps=0.0,
        funding_rate_at_entry=None,
        execution_fee_pct=0.001,
        slippage_bps=1.0,
    )
    repo.settle_filter_candidate_outcome(
        str(outcome["id"]),
        exit_price=101.0,
        settled_at=_now(),
        economics=economics,
        settlement_price_source="regression_projection",
        settlement_lag_sec=0.0,
    )

    report = build_ranker_objective_audit(hours=24)
    batch_id = str(shadow["batch_id"])
    batch = report["variants"]["current_persisted_ranking"]["by_policy_epoch"][
        "epoch-production"
    ]["by_batch"][batch_id]

    assert batch["horizons"]["15m"]["settled_count"] == 1
    assert batch["outcome_states"]["15m"] == {"settled": 1}
    assert report["outcome_linkage_exclusions"] == {}


def test_missing_or_partially_migrated_evidence_returns_insufficient_data():
    from aletheia.analytics.ranker_objective_audit import build_ranker_objective_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("DROP TABLE shadow_trades")
    db.commit()

    report = build_ranker_objective_audit(hours=24)

    assert report["status"] == "insufficient_data"
    assert report["read_only"] is True
    assert report["variants"] == {}
